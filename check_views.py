# -*- coding: utf-8 -*-
"""Смоук-проверка перед пушем: вьюхи создаются, команды на месте,
ответы не передают view=None (на этом discord.py падает).

Запуск: python check_views.py
"""

import asyncio
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
storage.save(eco.ECONOMY_FILE, {"1": {"1": {"balance": 100}}})
interaction = FakeInteraction(1, 1)
asyncio.run(eco.play_dice(interaction, 100))
if interaction.response.kwargs is None:
    fail("play_dice не ответил")
elif "view" in interaction.response.kwargs:
    fail("play_dice с нулевым балансом передал view")
else:
    print("OK   /dice без денег на повтор")

# Есть деньги: кнопка «ещё» приходит
storage.save(eco.ECONOMY_FILE, {"1": {"1": {"balance": 1000}}})
interaction = FakeInteraction(1, 1)
asyncio.run(eco.play_dice(interaction, 100))
if "view" not in interaction.response.kwargs:
    fail("play_dice не выдал кнопку рематча")
else:
    print("OK   /dice с деньгами на повтор")

print(f"\nПровалов: {failed}")
sys.exit(1 if failed else 0)
