# -*- coding: utf-8 -*-
"""Смоук-проверка перед пушем: вьюхи создаются, команды на месте,
ответы не передают view=None (на этом discord.py падает).

Запуск: python check_views.py
"""

import asyncio
import datetime
import os
import sys
import tempfile

import storage

# Не трогаем боевую папку data/ — всё пишем во временную.
storage.DATA_DIR = os.path.join(tempfile.mkdtemp(prefix="eco_check_"), "data")

import cogs.economy as eco  # noqa: E402

CASES = [
    (eco.DropView, (100, "🪙")),
    (eco.DuelView, (1, 2, 10)),
    (eco.QuestView, (1, 2)),
    (eco.DiceRematchView, (10,)),
    (eco.SlotsRematchView, (10,)),
    (eco.FlipView, (10,)),
    (eco.RPSView, (10,)),
]

failed = 0


def fail(text):
    global failed
    failed += 1
    print(f"FAIL {text}")


for cls, args in CASES:
    try:
        view = cls(*args)
    except Exception as e:
        fail(f"{cls.__name__}: {type(e).__name__}: {e}")
        continue
    if not view.children:
        fail(f"{cls.__name__}: нет кнопок")
        continue
    print(f"OK   {cls.__name__}: {len(view.children)} кнопок")

commands = eco.Economy.__cog_app_commands__
print(f"\nКоманд в коге: {len(commands)}")
if len(commands) != 27:
    fail("ожидалось 27 команд — проверь декораторы/отступы")

if "баланс" not in eco.Economy.balance.description.lower():
    fail(f"кодировка: {eco.Economy.balance.description!r}")

# Ответ без вьюхи не должен содержать ключ view
kwargs = eco._reply(eco._embed("тест"))
if "view" in kwargs:
    fail("_reply передаёт view=None")

kwargs = eco._reply(eco._embed("тест"), eco.DiceRematchView(10))
if kwargs.get("view") is None:
    fail("_reply потерял настоящую вьюху")


class FakeResponse:
    def __init__(self):
        self.kwargs = None

    def is_done(self):
        return False

    async def send_message(self, **kw):
        self.kwargs = kw

    async def edit_message(self, **kw):
        self.kwargs = kw


class FakeInteraction:
    def __init__(self, guild_id, user_id):
        self.guild = type("G", (), {"id": guild_id})()
        self.user = type("U", (), {"id": user_id})()
        self.response = FakeResponse()


# Проиграл всё: рематч-кнопки нет, и ответ не должен падать
# Кубики подменяем, иначе тест зависит от удачи: 1+1 против 6+6 — проигрыш.
def _forced_dice(mine_a, mine_b, bot_a, bot_b):
    """Подменяет random.randint на последовательность бросков."""
    values = iter([mine_a, mine_b, bot_a, bot_b])
    orig = eco.random.randint
    eco.random.randint = lambda a, b: next(values)
    return orig


storage.save(eco.ECONOMY_FILE, {"1": {"1": {"balance": 100}}})
interaction = FakeInteraction(1, 1)
_orig = _forced_dice(1, 1, 6, 6)
try:
    asyncio.run(eco.play_dice(interaction, 100))
finally:
    eco.random.randint = _orig
if interaction.response.kwargs is None:
    fail("play_dice не ответил")
elif "view" in interaction.response.kwargs:
    fail("play_dice с нулевым балансом передал view")
else:
    print("OK   /dice без денег на повтор")

# Есть деньги: кнопка «ещё» приходит
storage.save(eco.ECONOMY_FILE, {"1": {"1": {"balance": 1000}}})
interaction = FakeInteraction(1, 1)
_orig = _forced_dice(1, 1, 6, 6)
try:
    asyncio.run(eco.play_dice(interaction, 100))
finally:
    eco.random.randint = _orig
if "view" not in interaction.response.kwargs:
    fail("play_dice не выдал кнопку рематча")
else:
    print("OK   /dice с деньгами на повтор")


# ── Чистка своих сообщений ────────────────────────────────────────────────

class FakePerms:
    def __init__(self, manage_messages=True):
        self.manage_messages = manage_messages


class FakeMsg:
    def __init__(self, mid, author_id, age_sec, pinned=False):
        self.id = mid
        self.author = type("A", (), {"id": author_id})()
        self.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_sec)
        self.pinned = pinned
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeHistory:
    def __init__(self, messages):
        self._messages = messages

    def __aiter__(self):
        self._it = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCleanChannel:
    def __init__(self, messages, manage_messages=True):
        self.id = 4242
        self.name = "🧺--корзина"
        self._messages = messages
        self._manage = manage_messages
        self.guild = type("G", (), {"me": type("M", (), {"id": 999})()})()

    def permissions_for(self, member):
        return FakePerms(self._manage)

    def history(self, limit=None):
        return FakeHistory(self._messages)


bot_id = 999
messages = [
    FakeMsg(1, bot_id, 3600),          # старое своё — удалить
    FakeMsg(2, bot_id, 60),            # свежее своё — оставить
    FakeMsg(3, 42, 3600),              # чужое — оставить
    FakeMsg(4, bot_id, 3600, True),    # своё, но закреплено — оставить
]
channel = FakeCleanChannel(messages)
removed = asyncio.run(eco._purge_bot_messages(channel))
if removed != 1:
    fail(f"чистка удалила {removed}, ожидалось 1")
elif not messages[0].deleted:
    fail("чистка не удалила старое своё сообщение")
elif any(m.deleted for m in messages[1:]):
    fail("чистка тронула лишнее (свежее/чужое/закреп)")
else:
    print("OK   чистка: только старое своё, не закреплённое")

no_perms = asyncio.run(eco._purge_bot_messages(FakeCleanChannel(messages, manage_messages=False)))
if no_perms != 0:
    fail("чистка без прав manage_messages всё равно удаляла")
else:
    print("OK   чистка без прав ничего не трогает")

print(f"\nПровалов: {failed}")
sys.exit(1 if failed else 0)
