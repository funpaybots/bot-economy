# -*- coding: utf-8 -*-
"""Экономика: монеты, дейли со стриком, работа, переводы, магазин, игры,
кейсы, ограбления, дуэли, квесты, дропы в чате, сезоны и статистика."""

import asyncio
import datetime
import logging
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from storage import load, save
from version import BUILD

log = logging.getLogger(__name__)

ECONOMY_FILE = "economy.json"    # {guild_id: {user_id: {...}}}
SHOP_FILE = "shop.json"          # {guild_id: {role_id: {price, name}}}
SETTINGS_FILE = "settings.json"  # {guild_id: {currency, daily_amount, channel_id, ...}}
STATS_FILE = "stats.json"        # {guild_id: {user_id: {messages, voice_total, ...}}}

DEFAULT_CURRENCY = "🪙"
DEFAULT_DAILY = 250
WORK_COOLDOWN = 60 * 60            # час
DAILY_COOLDOWN = 60 * 60 * 24      # сутки
WORK_MIN, WORK_MAX = 50, 150

CASE_PRICE = 500
# (шанс в процентах, множитель от цены) — сумма шансов = 100
CASE_TABLE = [
    (45, 0.0),    # пусто
    (25, 0.5),    # половина назад
    (15, 1.0),    # вернул своё
    (10, 2.0),    # x2
    (4, 5.0),     # x5
    (1, 10.0),    # x10 — джекпот
]

ROB_COOLDOWN = 2 * 60 * 60
ROB_CHANCE = 0.4
ROB_MIN_VICTIM = 50

DROP_MIN, DROP_MAX = 50, 200
DEFAULT_DROP_EVERY = 200
DROP_TTL = 180                 # сколько секунд висит сообщение о дропе

# Чистка своих сообщений в каналах экономики: чтобы не засирать чат
CLEANUP_AFTER = 600            # удалять свои сообщения старше 10 минут
CLEANUP_SCAN = 500             # сколько последних сообщений просматривать за проход
CLEANUP_PER_RUN = 50           # максимум удалений за проход на канал (лимиты Discord)

BET_MIN = 10
DEFAULT_MAX_BET = 1000

QUESTS = [
    {"id": "work3", "btn": "Работа x3", "text": "Поработай 3 раза", "need": 3, "counter": "work", "reward": 300},
    {"id": "games5", "btn": "Игры x5", "text": "Сыграй 5 игр", "need": 5, "counter": "games", "reward": 400},
    {"id": "pay2", "btn": "Переводы x2", "text": "Переведи монеты 2 раза", "need": 2, "counter": "pay", "reward": 250},
]

SEASON_PRIZES = [5000, 3000, 2000]

WORK_LINES = [
    "разносил посылки",
    "мыл посуду в баре",
    "выгулял соседского пса",
    "настроил бабушке телефон",
    "продал старые шмотки",
    "помог с переездом",
    "протестировал игру на баги",
    "сделал лендинг на чистом энтузиазме",
    "выловил кота с дерева",
    "переписал конспекты на продажу",
]

SLOTS_EMOJIS = ["🍒", "🍋", "🍇", "💎", "🔔", "7️⃣"]
RPS_CHOICES = {"rock": "🪨 Камень", "paper": "📄 Бумага", "scissors": "✂️ Ножницы"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

COLOR = 0xA9C9FF

# Статистика держится в памяти и сбрасывается на диск раз в минуту,
# чтобы не переписывать файл на каждое сообщение.
_stats_cache = None
_stats_dirty = False
_voice_since = {}


# ── Форматирование ────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _human_left(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{int(hours)} ч {int(minutes)} мин"
    if minutes:
        return f"{int(minutes)} мин"
    return f"{seconds} сек"


def _human_span(seconds) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


def _today() -> str:
    return datetime.date.today().isoformat()


def _month() -> str:
    return datetime.date.today().strftime("%Y-%m")


def _embed(title: str, description: str = None) -> discord.Embed:
    e = discord.Embed(title=title, color=COLOR, timestamp=datetime.datetime.now())
    if description:
        e.description = description
    return e


def _reply(embed: discord.Embed, view=None) -> dict:
    """Аргументы ответа. view=None в send_message передавать нельзя —
    discord.py зовёт у него is_finished() и падает."""
    kwargs = {"embed": embed}
    if view is not None:
        kwargs["view"] = view
    return kwargs


# ── Настройки и доступ ────────────────────────────────────────────────────────

def _settings(guild_id: int) -> dict:
    return load(SETTINGS_FILE, {}).get(str(guild_id), {})


def _currency(guild_id: int) -> str:
    return _settings(guild_id).get("currency", DEFAULT_CURRENCY)


def _daily_amount(guild_id: int) -> int:
    return int(_settings(guild_id).get("daily_amount", DEFAULT_DAILY))


def _max_bet(guild_id: int) -> int:
    return int(_settings(guild_id).get("max_bet", DEFAULT_MAX_BET))


def _eco_channel(guild_id: int):
    cid = _settings(guild_id).get("channel_id")
    return int(cid) if cid else None


def _drop_channel(guild_id: int):
    cfg = _settings(guild_id)
    cid = cfg.get("drop_channel_id") or cfg.get("channel_id")
    return int(cid) if cid else None


def _drop_every(guild_id: int) -> int:
    return max(20, int(_settings(guild_id).get("drop_every", DEFAULT_DROP_EVERY)))


async def _purge_bot_messages(channel, older_than: float = CLEANUP_AFTER) -> int:
    """Удаляет старые сообщения самого бота в канале экономики.

    Свои сообщения не жалко: ответы на /balance, итоги игр, дропы после
    таймаута и прочее. Чужие сообщения и закреплённые не трогаем.
    Возвращает число удалённых.
    """
    guild = getattr(channel, "guild", None)
    me = getattr(guild, "me", None)
    if me is None:
        return 0
    try:
        perms = channel.permissions_for(me)
    except Exception:
        return 0
    if not getattr(perms, "manage_messages", False):
        return 0

    cutoff = time.time() - older_than
    deleted = 0
    try:
        async for msg in channel.history(limit=CLEANUP_SCAN):
            if msg.author.id != me.id or msg.pinned:
                continue
            try:
                created = msg.created_at.timestamp()
            except Exception:
                continue
            if created > cutoff:
                continue
            try:
                await msg.delete()
                deleted += 1
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException:
                log.warning("Не смог удалить своё сообщение %s в канале %s", msg.id, channel.id)
                continue
            if deleted >= CLEANUP_PER_RUN:
                break
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Чистка канала %s не удалась: %s", getattr(channel, "id", "?"), e)
    return deleted


def _clean_name(name: str) -> str:
    return re.sub(r"[^а-яa-z0-9]", "", (name or "").lower())


def _looks_like_eco(name: str) -> bool:
    clean = _clean_name(name)
    return "карзин" in clean or "корзин" in clean


def _economy_channel_ids(guild) -> list:
    """ID каналов экономики/дропов. Если не настроены — ищем по имени,
    как это делает гейт команд."""
    ids = [cid for cid in (_eco_channel(guild.id), _drop_channel(guild.id)) if cid]
    if ids:
        return ids
    return [ch.id for ch in guild.text_channels if _looks_like_eco(ch.name)]


def _is_allowed(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    cid = _eco_channel(interaction.guild.id)
    if cid:
        return interaction.channel_id == cid
    return _looks_like_eco(getattr(interaction.channel, "name", ""))


async def _eco_predicate(interaction: discord.Interaction) -> bool:
    return _is_allowed(interaction)


eco_only = app_commands.check(_eco_predicate)


# ── Аккаунты ──────────────────────────────────────────────────────────────────

def _ensure(acc: dict) -> dict:
    """Достраивает старый аккаунт до актуальной схемы."""
    acc.setdefault("balance", 0)
    acc.setdefault("daily_streak", 0)
    acc.setdefault("season", _month())
    acc.setdefault("season_earned", 0)
    acc.setdefault("last_seen", _today())

    today = _today()
    if acc.get("today", {}).get("date") != today:
        acc["today"] = {
            "date": today, "work": 0, "games": 0, "pay": 0,
            "daily": 0, "rob": 0, "case": 0, "drop": 0,
        }
    if acc.get("quests", {}).get("date") != today:
        acc["quests"] = {"date": today, "claimed": []}

    if acc.get("season") != _month():
        acc["season"] = _month()
        acc["season_earned"] = 0

    acc["last_seen"] = today
    return acc


def _acc_from(data: dict, guild_id: int, user_id: int) -> dict:
    """Аккаунт из уже загруженных данных — чтобы правки разных людей шли в один словарь."""
    acc = data.setdefault(str(guild_id), {}).setdefault(str(user_id), {})
    return _ensure(acc)


def _acc(guild_id: int, user_id: int):
    """Возвращает (все данные, аккаунт пользователя). Аккаунт не сохраняется сам."""
    data = load(ECONOMY_FILE, {})
    return data, _acc_from(data, guild_id, user_id)


def _bump(acc: dict, counter: str, n: int = 1) -> None:
    today = acc.get("today") or {"date": _today()}
    today[counter] = int(today.get(counter, 0)) + n
    acc["today"] = today


def _earn(acc: dict, amount: int) -> None:
    """Начисление с учётом сезонного счётчика."""
    if amount <= 0:
        return
    acc["balance"] = int(acc.get("balance", 0)) + int(amount)
    acc["season_earned"] = int(acc.get("season_earned", 0)) + int(amount)


def _spend(acc: dict, amount: int) -> bool:
    """Списание. Возвращает False, если не хватает."""
    if int(acc.get("balance", 0)) < amount:
        return False
    acc["balance"] = int(acc.get("balance", 0)) - int(amount)
    return True


def _shop(guild_id: int) -> dict:
    return load(SHOP_FILE, {}).get(str(guild_id), {})


# ── Статистика ────────────────────────────────────────────────────────────────

def _stats_data() -> dict:
    global _stats_cache
    if _stats_cache is None:
        _stats_cache = load(STATS_FILE, {})
    return _stats_cache


def _stats_save(force: bool = False) -> None:
    global _stats_dirty
    if _stats_cache is None:
        return
    if not (_stats_dirty or force):
        return
    save(STATS_FILE, _stats_cache)
    _stats_dirty = False


def _stat_user(guild_id: int, user_id: int) -> dict:
    guild = _stats_data().setdefault(str(guild_id), {})
    st = guild.setdefault(str(user_id), {})
    today = _today()
    st.setdefault("messages", 0)
    st.setdefault("voice_total", 0)
    st.setdefault("voice_today", 0)
    st.setdefault("voice_day", today)
    st.setdefault("first_seen", today)
    if st.get("voice_day") != today:
        st["voice_day"] = today
        st["voice_today"] = 0
    st["last_seen"] = today
    return st


def _add_voice(guild_id: int, user_id: int, seconds: float) -> None:
    global _stats_dirty
    seconds = int(seconds)
    if seconds <= 0:
        return
    st = _stat_user(guild_id, user_id)
    st["voice_total"] = int(st.get("voice_total", 0)) + seconds
    st["voice_today"] = int(st.get("voice_today", 0)) + seconds
    _stats_dirty = True


# ── Кейсы ─────────────────────────────────────────────────────────────────────

def _open_case() -> float:
    roll = random.uniform(0, 100)
    acc = 0
    for chance, mult in CASE_TABLE:
        acc += chance
        if roll < acc:
            return mult
    return 0.0


def _case_face(mult: float) -> str:
    if mult <= 0:
        return "⬛ пусто"
    if mult < 1:
        return f"🟨 x{mult:g}"
    if mult == 1:
        return "🟦 вернул своё"
    if mult < 10:
        return f"🟩 x{mult:g}"
    return "💎 x10 ДЖЕКПОТ"


# ── Дропы ─────────────────────────────────────────────────────────────────────

class TimeoutView(discord.ui.View):
    """Вью, которая по таймауту гасит кнопки своего сообщения."""

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        message = getattr(self, "message", None)
        if message is not None:
            try:
                await message.edit(view=self)
            except discord.HTTPException:
                pass


class DropView(TimeoutView):
    def __init__(self, amount: int, currency: str):
        super().__init__(timeout=180)
        self.amount = amount
        self.currency = currency
        self.claimed = False

    @discord.ui.button(label="Забрать", emoji="💰", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:
            await interaction.response.send_message("Уже разобрали.", ephemeral=True)
            return
        data, acc = _acc(interaction.guild.id, interaction.user.id)
        _earn(acc, self.amount)
        _bump(acc, "drop")
        save(ECONOMY_FILE, data)
        self.claimed = True
        self.stop()
        e = _embed(
            "💰 Дроп поймали!",
            f"{interaction.user.mention} забрал **{_fmt(self.amount)} {self.currency}**.\n"
            f"Баланс: **{_fmt(acc['balance'])} {self.currency}**",
        )
        await interaction.response.edit_message(embed=e, view=None)


# ── Дуэль ─────────────────────────────────────────────────────────────────────

class DuelView(TimeoutView):
    def __init__(self, challenger_id: int, opponent_id: int, bet: int):
        super().__init__(timeout=120)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.bet = bet

    async def _decline(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            embed=_embed("⚔️ Дуэль отменена", f"{interaction.user.mention} отказался."),
            view=None,
        )

    @discord.ui.button(label="Принять", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Это чужая дуэль.", ephemeral=True)
            return
        guild = interaction.guild
        cur = _currency(guild.id)
        data = load(ECONOMY_FILE, {})
        challenger = _acc_from(data, guild.id, self.challenger_id)
        opponent = _acc_from(data, guild.id, self.opponent_id)
        if challenger["balance"] < self.bet:
            await interaction.response.edit_message(
                embed=_embed("⚔️ Дуэль сорвалась", "У вызывающего уже не хватает монет."), view=None
            )
            return
        if opponent["balance"] < self.bet:
            await interaction.response.send_message(
                f"Не хватает: ставка {_fmt(self.bet)} {cur}.", ephemeral=True
            )
            return

        challenger["balance"] -= self.bet
        opponent["balance"] -= self.bet
        winner = random.choice([self.challenger_id, self.opponent_id])
        acc = challenger if winner == self.challenger_id else opponent
        acc["balance"] += self.bet * 2
        _bump(challenger, "games")
        _bump(opponent, "games")
        save(ECONOMY_FILE, data)
        self.stop()
        await interaction.response.edit_message(
            embed=_embed(
                "⚔️ Дуэль",
                f"<@{self.challenger_id}> **{_fmt(self.bet)} {cur}** ⚔️ <@{self.opponent_id}> **{_fmt(self.bet)} {cur}**\n\n"
                f"Победил <@{winner}> — забрал банк **{_fmt(self.bet * 2)} {cur}**.",
            ),
            view=None,
        )

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message("Это чужая дуэль.", ephemeral=True)
            return
        await self._decline(interaction)


# ── Квесты ────────────────────────────────────────────────────────────────────

class QuestView(TimeoutView):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        for quest in QUESTS:
            button = discord.ui.Button(label=quest["btn"], style=discord.ButtonStyle.success)
            button.callback = self._claim_cb(quest)
            self.add_item(button)

    def _claim_cb(self, quest):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Это чужой список квестов.", ephemeral=True)
                return
            data, acc = _acc(self.guild_id, self.user_id)
            quests = acc.setdefault("quests", {"date": _today(), "claimed": []})
            if quest["id"] in quests.get("claimed", []):
                await interaction.response.send_message("Этот квест уже сдан.", ephemeral=True)
                return
            done = int((acc.get("today") or {}).get(quest["counter"], 0))
            if done < quest["need"]:
                await interaction.response.send_message(
                    f"Ещё рано: {done}/{quest['need']}.", ephemeral=True
                )
                return
            _earn(acc, quest["reward"])
            quests.setdefault("claimed", []).append(quest["id"])
            save(ECONOMY_FILE, data)
            cur = _currency(self.guild_id)
            await interaction.response.send_message(
                f"✅ Квест «{quest['text']}» сдан: **+{_fmt(quest['reward'])} {cur}**.", ephemeral=True
            )

        return callback


# ── Ког ───────────────────────────────────────────────────────────────────────

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._msg_count = {}

    async def cog_load(self):
        self._flush_stats.start()
        self._autoclean.start()

    async def cog_unload(self):
        self._flush_stats.cancel()
        self._autoclean.cancel()
        _stats_save(force=True)

    @tasks.loop(seconds=60)
    async def _flush_stats(self):
        _stats_save()

    @_flush_stats.before_loop
    async def _before_flush(self):
        await self.bot.wait_until_ready()

    # ── Чистка своих сообщений ────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def _autoclean(self):
        for guild in self.bot.guilds:
            channels = []
            for cid in _economy_channel_ids(guild):
                channel = guild.get_channel(cid)
                if channel is not None and channel not in channels:
                    channels.append(channel)
            for channel in channels:
                try:
                    removed = await _purge_bot_messages(channel)
                except Exception:
                    log.exception("Чистка канала %s упала", channel.id)
                    continue
                if removed:
                    log.info("Чистка %s: удалено своих сообщений — %d", channel.name, removed)

    @_autoclean.before_loop
    async def _before_autoclean(self):
        await self.bot.wait_until_ready()

    # ── Баланс ────────────────────────────────────────────────────────────

    @app_commands.command(name="balance", description="Показать баланс")
    @app_commands.describe(user="Чей баланс (по умолчанию твой)")
    @eco_only
    async def balance(self, interaction: discord.Interaction, user: discord.Member = None):
        member = user or interaction.user
        _, acc = _acc(interaction.guild.id, member.id)
        cur = _currency(interaction.guild.id)

        e = _embed(f"💳 Баланс {member.display_name}")
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="На счету", value=f"{_fmt(acc['balance'])} {cur}", inline=False)

        now = time.time()
        daily_left = DAILY_COOLDOWN - (now - acc.get("last_daily", 0))
        work_left = WORK_COOLDOWN - (now - acc.get("last_work", 0))
        e.add_field(
            name="Дейлик",
            value="✅ можно забрать (/daily)" if daily_left <= 0 else f"⏳ через {_human_left(daily_left)}",
        )
        e.add_field(
            name="Работа",
            value="✅ можно работать (/work)" if work_left <= 0 else f"⏳ через {_human_left(work_left)}",
        )
        e.add_field(name="Стрик дейлика", value=f"🔥 {acc.get('daily_streak', 0)} дн.", inline=False)
        e.set_footer(text=f"сборка {BUILD}")
        await interaction.response.send_message(embed=e)

    # ── Дейлик ────────────────────────────────────────────────────────────

    @app_commands.command(name="daily", description="Забрать ежедневную награду")
    @eco_only
    async def daily(self, interaction: discord.Interaction):
        data, acc = _acc(interaction.guild.id, interaction.user.id)
        now = time.time()
        left = DAILY_COOLDOWN - (now - acc.get("last_daily", 0))
        if left > 0:
            await interaction.response.send_message(
                f"⏳ Дейлик уже забрал. Следующий через **{_human_left(left)}**.", ephemeral=True
            )
            return

        last = acc.get("last_daily", 0)
        gap = now - last if last else None
        if gap is not None and gap < DAILY_COOLDOWN + 12 * 3600:
            streak = int(acc.get("daily_streak", 0)) + 1
        else:
            streak = 1

        bonus_pct = min(streak - 1, 6) * 10
        amount = int(_daily_amount(interaction.guild.id) * (100 + bonus_pct) / 100)
        cur = _currency(interaction.guild.id)

        _earn(acc, amount)
        acc["daily_streak"] = streak
        acc["last_daily"] = now
        _bump(acc, "daily")
        save(ECONOMY_FILE, data)

        e = _embed(
            "💳 Дейлик",
            f"Забрал **{_fmt(amount)} {cur}**.\n"
            f"🔥 Стрик: **{streak} дн.** (бонус +{bonus_pct}%)\n"
            f"Баланс: **{_fmt(acc['balance'])} {cur}**",
        )
        if streak and streak % 7 == 0:
            e.set_footer(text="Неделя подряд — так держать!")
        await interaction.response.send_message(embed=e)

    # ── Работа ────────────────────────────────────────────────────────────

    @app_commands.command(name="work", description="Подработать (раз в час)")
    @eco_only
    async def work(self, interaction: discord.Interaction):
        data, acc = _acc(interaction.guild.id, interaction.user.id)
        now = time.time()
        left = WORK_COOLDOWN - (now - acc.get("last_work", 0))
        if left > 0:
            await interaction.response.send_message(
                f"⏳ Устал. Можешь работать снова через **{_human_left(left)}**.", ephemeral=True
            )
            return

        amount = random.randint(WORK_MIN, WORK_MAX)
        cur = _currency(interaction.guild.id)
        line = random.choice(WORK_LINES)
        _earn(acc, amount)
        acc["last_work"] = now
        _bump(acc, "work")
        save(ECONOMY_FILE, data)

        await interaction.response.send_message(
            embed=_embed(
                "🛠 Работа",
                f"Ты {line} и заработал **{_fmt(amount)} {cur}**.\n"
                f"Баланс: **{_fmt(acc['balance'])} {cur}**",
            )
        )

    # ── Перевод ───────────────────────────────────────────────────────────

    @app_commands.command(name="pay", description="Перевести монеты другому участнику")
    @app_commands.describe(user="Кому", amount="Сколько")
    @eco_only
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        if user.bot:
            await interaction.response.send_message("Ботам монеты не нужны.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("Себе переводить — это стирание граней.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        data = load(ECONOMY_FILE, {})
        sender = _acc_from(data, interaction.guild.id, interaction.user.id)
        if sender["balance"] < amount:
            await interaction.response.send_message(
                f"Не хватает. У тебя {_fmt(sender['balance'])} {cur}.", ephemeral=True
            )
            return

        receiver = _acc_from(data, interaction.guild.id, user.id)
        sender["balance"] -= amount
        _earn(receiver, amount)
        _bump(sender, "pay")
        save(ECONOMY_FILE, data)

        await interaction.response.send_message(
            embed=_embed(
                "💸 Перевод",
                f"{interaction.user.mention} → {user.mention}: **{_fmt(amount)} {cur}**\n"
                f"Осталось у отправителя: **{_fmt(sender['balance'])} {cur}**",
            )
        )

    # ── Топ ───────────────────────────────────────────────────────────────

    @app_commands.command(name="top", description="Топ-10 богачей сервера")
    @eco_only
    async def top(self, interaction: discord.Interaction):
        users = load(ECONOMY_FILE, {}).get(str(interaction.guild.id), {})
        rich = sorted(
            ((int(uid), info.get("balance", 0)) for uid, info in users.items()),
            key=lambda x: x[1],
            reverse=True,
        )[:10]
        if not rich:
            await interaction.response.send_message("Пока все нищие.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
        lines = [
            f"{medals[i]} <@{uid}> — **{_fmt(balance)} {cur}**"
            for i, (uid, balance) in enumerate(rich)
        ]
        await interaction.response.send_message(embed=_embed("🏆 Богачи сервера", "\n".join(lines)))

    # ── Магазин ───────────────────────────────────────────────────────────

    @app_commands.command(name="shop", description="Магазин ролей за монеты")
    @eco_only
    async def shop(self, interaction: discord.Interaction):
        items = _shop(interaction.guild.id)
        if not items:
            await interaction.response.send_message(
                "Магазин пуст. Админ может добавить роли: /shop_add роль цена", ephemeral=True
            )
            return
        cur = _currency(interaction.guild.id)
        lines = [
            f"• <@&{role_id}> — **{_fmt(info['price'])} {cur}**"
            for role_id, info in sorted(items.items(), key=lambda x: x[1]["price"])
        ]
        e = _embed("🛒 Магазин ролей", "\n".join(lines))
        e.set_footer(text="Купить: /buy роль")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="buy", description="Купить роль за монеты")
    @app_commands.describe(role="Роль из магазина")
    @eco_only
    async def buy(self, interaction: discord.Interaction, role: discord.Role):
        items = _shop(interaction.guild.id)
        info = items.get(str(role.id))
        if not info:
            await interaction.response.send_message("Такой роли нет в магазине.", ephemeral=True)
            return
        member = interaction.user
        if role in member.roles:
            await interaction.response.send_message("Эта роль у тебя уже есть.", ephemeral=True)
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "Роль выше моей — не могу выдать. Пусть админ опустит её ниже роли бота.", ephemeral=True
            )
            return

        cur = _currency(interaction.guild.id)
        price = int(info["price"])
        data, acc = _acc(interaction.guild.id, member.id)
        if acc["balance"] < price:
            await interaction.response.send_message(
                f"Не хватает: нужно {_fmt(price)} {cur}, у тебя {_fmt(acc['balance'])} {cur}.",
                ephemeral=True,
            )
            return

        # Списание ДО сетевого вызова: между load и save не должно быть await,
        # иначе сохранение устаревшей копии затирает чужие начисления
        # (дейли, переводы и т.п.), произошедшие во время выдачи роли.
        acc["balance"] -= price
        save(ECONOMY_FILE, data)

        # Дальше сетевой вызов выдачи роли — останавливаем таймер ответа Discord.
        await interaction.response.defer()

        try:
            await member.add_roles(role, reason=f"Покупка в магазине ({interaction.user})")
        except discord.Forbidden:
            # Возврат: свежая загрузка, чтобы не перезаписать чужие изменения.
            data2, acc2 = _acc(interaction.guild.id, member.id)
            acc2["balance"] = int(acc2.get("balance", 0)) + price
            save(ECONOMY_FILE, data2)
            await interaction.followup.send(
                "Не смог выдать роль — нет права «Управлять ролями» или роль выше моей. Монеты вернул.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=_embed(
                "🛒 Покупка",
                f"{member.mention} купил {role.mention} за **{_fmt(price)} {cur}**.\n"
                f"Осталось: **{_fmt(acc['balance'])} {cur}**",
            )
        )

    # ── Кейсы ─────────────────────────────────────────────────────────────

    @app_commands.command(name="case", description="Открыть кейс за монеты")
    @app_commands.describe(count="Сколько кейсов открыть (1-10)")
    @eco_only
    async def case(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 10] = 1):
        cur = _currency(interaction.guild.id)
        data, acc = _acc(interaction.guild.id, interaction.user.id)
        total_price = CASE_PRICE * count
        if acc["balance"] < total_price:
            await interaction.response.send_message(
                f"Кейс стоит {_fmt(CASE_PRICE)} {cur}. На {count} шт. нужно {_fmt(total_price)}, "
                f"у тебя {_fmt(acc['balance'])} {cur}.",
                ephemeral=True,
            )
            return

        acc["balance"] -= total_price
        faces = []
        total_win = 0
        for _ in range(count):
            mult = _open_case()
            total_win += int(CASE_PRICE * mult)
            faces.append(_case_face(mult))
        _earn(acc, total_win)
        _bump(acc, "case", count)
        save(ECONOMY_FILE, data)

        profit = total_win - total_price
        e = _embed(
            "🎁 Кейсы",
            "\n".join(f"Кейс {i + 1}: {face}" for i, face in enumerate(faces))
            + f"\n\nПотратил **{_fmt(total_price)} {cur}**, вернулось **{_fmt(total_win)} {cur}** "
            f"({'📈 +' if profit >= 0 else '📉 −'}{_fmt(abs(profit))} {cur})\n"
            f"Баланс: **{_fmt(acc['balance'])} {cur}**",
        )
        await interaction.response.send_message(embed=e)

    # ── Ограбление ────────────────────────────────────────────────────────

    @app_commands.command(name="rob", description="Попробовать ограбить участника (кулдаун 2 часа)")
    @app_commands.describe(user="Кого грабим")
    @eco_only
    async def rob(self, interaction: discord.Interaction, user: discord.Member):
        if user.bot:
            await interaction.response.send_message("У бота нечего брать.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("Себя грабить — это уже клиника.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        data = load(ECONOMY_FILE, {})
        thief = _acc_from(data, interaction.guild.id, interaction.user.id)
        victim = _acc_from(data, interaction.guild.id, user.id)

        now = time.time()
        left = ROB_COOLDOWN - (now - thief.get("last_rob", 0))
        if left > 0:
            await interaction.response.send_message(
                f"🕵️ Тебя ещё ищут с прошлого дела. Следующая попытка через **{_human_left(left)}**.",
                ephemeral=True,
            )
            return
        if victim["balance"] < ROB_MIN_VICTIM:
            await interaction.response.send_message(
                f"У {user.mention} и так пусто — не позорься.", ephemeral=True
            )
            return

        steal = max(ROB_MIN_VICTIM, int(victim["balance"] * random.uniform(0.10, 0.25)))
        steal = min(steal, victim["balance"])
        thief["last_rob"] = now

        if random.random() < ROB_CHANCE:
            victim["balance"] -= steal
            _earn(thief, steal)
            _bump(thief, "rob")
            save(ECONOMY_FILE, data)
            e = _embed(
                "🕵️ Ограбление удалось",
                f"{interaction.user.mention} обчистил {user.mention} на **{_fmt(steal)} {cur}**.\n"
                f"Твой баланс: **{_fmt(thief['balance'])} {cur}**",
            )
        else:
            fine = min(thief["balance"], steal * 2)
            thief["balance"] -= fine
            save(ECONOMY_FILE, data)
            e = _embed(
                "🚨 Попался!",
                f"{interaction.user.mention} попытался обчистить {user.mention}, но его поймали.\n"
                f"Штраф: **{_fmt(fine)} {cur}**\n"
                f"Осталось: **{_fmt(thief['balance'])} {cur}**",
            )
        await interaction.response.send_message(embed=e)

    # ── Дуэль ─────────────────────────────────────────────────────────────

    @app_commands.command(name="duel", description="Вызвать участника на дуэль на монеты")
    @app_commands.describe(user="Соперник", bet="Ставка")
    @eco_only
    async def duel(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        bet: app_commands.Range[int, 1],
    ):
        if not await GameBase._check_bet(interaction, bet):
            return
        if user.bot:
            await interaction.response.send_message("Боты в дуэли не ходят.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("Дуэль с собой — это медитация.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        e = _embed(
            "⚔️ Вызов на дуэль",
            f"{interaction.user.mention} вызывает {user.mention}\n"
            f"Ставка: **{_fmt(bet)} {cur}** с каждого\n\n"
            f"{user.mention}, принимаешь?",
        )
        await interaction.response.send_message(
            embed=e, view=DuelView(interaction.user.id, user.id, bet)
        )

    # ── Квесты ────────────────────────────────────────────────────────────

    @app_commands.command(name="quests", description="Ежедневные квесты и прогресс")
    @eco_only
    async def quests(self, interaction: discord.Interaction):
        _, acc = _acc(interaction.guild.id, interaction.user.id)
        cur = _currency(interaction.guild.id)
        today = acc.get("today") or {}
        claimed = set((acc.get("quests") or {}).get("claimed", []))

        lines = []
        for quest in QUESTS:
            done = int(today.get(quest["counter"], 0))
            mark = "✅" if quest["id"] in claimed else ("🟢" if done >= quest["need"] else "▫️")
            lines.append(
                f"{mark} **{quest['text']}** — {min(done, quest['need'])}/{quest['need']} "
                f"· награда {_fmt(quest['reward'])} {cur}"
            )
        e = _embed("📋 Квесты на сегодня", "\n".join(lines))
        e.set_footer(text="Жми кнопку, когда выполнено")
        await interaction.response.send_message(embed=e, view=QuestView(interaction.guild.id, interaction.user.id))

    # ── Сезон ─────────────────────────────────────────────────────────────

    @app_commands.command(name="season", description="Сезонный топ по заработку")
    @eco_only
    async def season(self, interaction: discord.Interaction):
        users = load(ECONOMY_FILE, {}).get(str(interaction.guild.id), {})
        rows = []
        for uid, info in users.items():
            _ensure(info)
            earned = int(info.get("season_earned", 0))
            if earned > 0:
                rows.append((int(uid), earned))
        rows.sort(key=lambda x: x[1], reverse=True)
        if not rows:
            await interaction.response.send_message("Сезон только начался — заработай первый.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
        lines = [
            f"{medals[i]} <@{uid}> — **{_fmt(earned)} {cur}**"
            for i, (uid, earned) in enumerate(rows[:10])
        ]
        e = _embed(f"🗓 Сезон {_month()}", "\n".join(lines))
        e.set_footer(
            text="Призы в конце месяца: " + ", ".join(f"{_fmt(p)}" for p in SEASON_PRIZES)
        )
        await interaction.response.send_message(embed=e)

    # ── Статистика ────────────────────────────────────────────────────────

    @app_commands.command(name="stats", description="Статистика участника: сообщения, время в войсе")
    @app_commands.describe(user="Чья статистика (по умолчанию твоя)")
    @eco_only
    async def stats(self, interaction: discord.Interaction, user: discord.Member = None):
        member = user or interaction.user
        _, acc = _acc(interaction.guild.id, member.id)
        st = _stat_user(interaction.guild.id, member.id)
        cur = _currency(interaction.guild.id)

        # Если человек прямо сейчас сидит в войсе — показываем живой счётчик.
        live = 0
        key = (interaction.guild.id, member.id)
        if key in _voice_since:
            live = int(time.time() - _voice_since[key])

        voice_today = int(st.get("voice_today", 0)) + live
        voice_total = int(st.get("voice_total", 0)) + live
        today = acc.get("today") or {}

        e = _embed(f"📊 {member.display_name}")
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Баланс", value=f"**{_fmt(acc['balance'])} {cur}**", inline=False)
        e.add_field(name="Сообщений", value=_fmt(st.get("messages", 0)), inline=True)
        e.add_field(name="🔥 Стрик", value=f"{acc.get('daily_streak', 0)} дн.", inline=True)
        e.add_field(
            name="В войсе сегодня",
            value=_human_span(voice_today) + (" 🎧" if live else ""),
            inline=True,
        )
        e.add_field(name="В войсе всего", value=_human_span(voice_total), inline=True)
        e.add_field(name="Игр сегодня", value=_fmt(today.get("games", 0)), inline=True)
        e.add_field(name="Кейсов сегодня", value=_fmt(today.get("case", 0)), inline=True)
        e.add_field(name="Краж сегодня", value=_fmt(today.get("rob", 0)), inline=True)
        e.add_field(name="С нами с", value=st.get("first_seen", "—"), inline=True)
        await interaction.response.send_message(embed=e)

    # ── Список игр ────────────────────────────────────────────────────────

    @app_commands.command(name="games", description="Список мини-игр и правила")
    @eco_only
    async def games(self, interaction: discord.Interaction):
        cur = _currency(interaction.guild.id)
        e = _embed(
            "🎮 Мини-игры",
            f"Ставки: от **{_fmt(BET_MIN)}** до **{_fmt(_max_bet(interaction.guild.id))} {cur}**\n\n"
            "🎲 **/dice ставка** — кости против бота, у кого сумма выше.\n"
            "Победа x1.9, ничья — ставка возвращается\n\n"
            "🎰 **/slots ставка** — три барабана.\n"
            "Три в ряд x10, два в ряд x1.5\n\n"
            "🪙 **/flip ставка** — угадай орёл или решка. Угадал x1.9\n\n"
            "✌️ **/rps ставка** — камень-ножницы-бумага против бота. Победа x2\n\n"
            "🃏 **/blackjack ставка** — 21 очко против дилера. Блэкджек x2.5\n"
            "💣 **/mines ставка [мин]** — открывай клетки, множитель растёт, забирай в любой момент\n"
            "🚀 **/crash ставка** — множитель тикает вверх, успей забрать до краша\n"
            "🎡 **/roulette ставка цвет** — красное/чёрное x2, зеро x30\n"
            "📈 **/highlow ставка** — угадай, выше или ниже следующая карта\n\n"
            "⚔️ **/duel @ник ставка** — дуэль, победитель забирает банк\n"
            "⭕ **/ttt @ник ставка** — крестики-нолики, победитель забирает банк\n"
            "🎁 **/case** — кейс за монеты, внутри до x10\n"
            "🕵️ **/rob @ник** — ограбление, шанс 40%",
        )
        e.set_footer(text="Игры — слив монет, играй в удовольствие")
        await interaction.response.send_message(embed=e)

    # ── Игры ──────────────────────────────────────────────────────────────

    @app_commands.command(name="dice", description="Кости против бота — у кого сумма выше")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def dice(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        await play_dice(interaction, bet)

    @app_commands.command(name="slots", description="Слот-машина: три в ряд = x10")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        await play_slots(interaction, bet)

    @app_commands.command(name="flip", description="Орёл или решка — угадай сторону")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def flip(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await GameBase._check_bet(interaction, bet):
            return
        e = _embed(
            "🪙 Монетка",
            f"Ставка: **{_fmt(bet)} {_currency(interaction.guild.id)}**\nВыбирай сторону:",
        )
        await interaction.response.send_message(embed=e, view=FlipView(bet))

    @app_commands.command(name="rps", description="Камень, ножницы, бумага против бота")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def rps(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await GameBase._check_bet(interaction, bet):
            return
        e = _embed(
            "✌️ КНБ",
            f"Ставка: **{_fmt(bet)} {_currency(interaction.guild.id)}**\nДелай ход:",
        )
        await interaction.response.send_message(embed=e, view=RPSView(bet))

    @app_commands.command(name="blackjack", description="Блэкджек: набери 21 и не перебери")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        await play_blackjack(interaction, bet)

    @app_commands.command(name="mines", description="Мины: открывай клетки, множитель растёт")
    @app_commands.describe(bet="Ставка", mines="Сколько мин на поле (по умолчанию 3)")
    @eco_only
    async def mines(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Range[int, 1],
        mines: app_commands.Range[int, 1, 15] = 3,
    ):
        if not await GameBase._check_bet(interaction, bet):
            return
        view = MinesView(interaction.guild.id, interaction.user.id, bet, mines)
        await interaction.response.send_message(embed=view.render_embed(), view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="crash", description="Краш: множитель растёт, успей забрать")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def crash(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await GameBase._check_bet(interaction, bet):
            return
        guild = interaction.guild
        data, acc = _acc(guild.id, interaction.user.id)
        acc["balance"] -= bet
        save(ECONOMY_FILE, data)
        view = CrashView(guild.id, interaction.user.id, bet)
        await interaction.response.send_message(embed=view.render_embed(), view=view)
        view.message = await interaction.original_response()
        view.start()

    @app_commands.command(name="roulette", description="Рулетка: красное x2, чёрное x2, зеро x30")
    @app_commands.describe(bet="Ставка", choice="На что ставишь")
    @app_commands.choices(
        choice=[
            app_commands.Choice(name="Красное — x2", value="красное"),
            app_commands.Choice(name="Чёрное — x2", value="чёрное"),
            app_commands.Choice(name="Зеро — x30", value="зеро"),
        ]
    )
    @eco_only
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Range[int, 1],
        choice: str,
    ):
        if not await GameBase._check_bet(interaction, bet):
            return
        guild = interaction.guild
        cur = _currency(guild.id)
        data, acc = _acc(guild.id, interaction.user.id)
        acc["balance"] -= bet
        spin = random.randint(0, 36)
        mult = _roulette_mult(choice, spin)
        if mult:
            acc["balance"] += int(bet * mult)
        _bump(acc, "games")
        save(ECONOMY_FILE, data)

        color_name = _roulette_color(spin)
        if mult:
            title, color = "🎡 Угадал!", 0x57F287
            result = f"Выпало **{spin} · {color_name}** — x{mult}. Выиграл **{_fmt(int(bet * mult) - bet)} {cur}** чистыми."
        else:
            title, color = "🎡 Не угадал", 0xED4245
            result = f"Выпало **{spin} · {color_name}**, ты ставил на {choice}. Потерял **{_fmt(bet)} {cur}**."
        e = discord.Embed(
            title=title,
            description=f"{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**",
            color=color,
            timestamp=datetime.datetime.now(),
        )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="highlow", description="Выше-ниже: угадай карту, множитель копится")
    @app_commands.describe(bet="Ставка")
    @eco_only
    async def highlow(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await GameBase._check_bet(interaction, bet):
            return
        guild = interaction.guild
        data, acc = _acc(guild.id, interaction.user.id)
        acc["balance"] -= bet
        save(ECONOMY_FILE, data)
        view = HighLowView(guild.id, interaction.user.id, bet, _draw())
        await interaction.response.send_message(embed=view.render_embed(), view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="ttt", description="Крестики-нолики на ставку против другого участника")
    @app_commands.describe(opponent="С кем играем", bet="Ставка каждого")
    @eco_only
    async def ttt(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        bet: app_commands.Range[int, 1],
    ):
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("С собой играть нечестно.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("С ботами не играем — они и так всё знают.", ephemeral=True)
            return
        if not await GameBase._check_bet(interaction, bet):
            return
        cur = _currency(interaction.guild.id)
        view = TicTacToeChallengeView(
            interaction.guild.id, interaction.user.id, opponent.id, bet
        )
        e = _embed(
            "⭕ Крестики-нолики",
            f"{interaction.user.mention} вызывает {opponent.mention}\n"
            f"Ставка каждого: **{_fmt(bet)} {cur}** · банк **{_fmt(bet * 2)} {cur}**\n\n"
            f"{opponent.mention}, принимаешь?",
        )
        await interaction.response.send_message(embed=e, view=view)

    # ── Магазин: настройка ────────────────────────────────────────────────

    @app_commands.command(name="shop_add", description="Добавить роль в магазин")
    @app_commands.describe(role="Роль на продажу", price="Цена в монетах")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_add(self, interaction: discord.Interaction, role: discord.Role, price: app_commands.Range[int, 1]):
        data = load(SHOP_FILE, {})
        gshop = data.setdefault(str(interaction.guild.id), {})
        gshop[str(role.id)] = {"price": price, "name": role.name}
        save(SHOP_FILE, data)
        cur = _currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ {role.mention} в магазине за **{_fmt(price)} {cur}**.", ephemeral=True
        )

    @app_commands.command(name="shop_remove", description="Убрать роль из магазина")
    @app_commands.describe(role="Роль")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_remove(self, interaction: discord.Interaction, role: discord.Role):
        data = load(SHOP_FILE, {})
        gshop = data.get(str(interaction.guild.id), {})
        if str(role.id) not in gshop:
            await interaction.response.send_message("Её и так нет в магазине.", ephemeral=True)
            return
        del gshop[str(role.id)]
        save(SHOP_FILE, data)
        await interaction.response.send_message(f"✅ {role.mention} убрал из магазина.", ephemeral=True)

    # ── Админские команды ─────────────────────────────────────────────────

    @app_commands.command(name="eco_give", description="Выдать монеты участнику")
    @app_commands.describe(user="Кому", amount="Сколько")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_give(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        data, acc = _acc(interaction.guild.id, user.id)
        _earn(acc, amount)
        save(ECONOMY_FILE, data)
        cur = _currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ {user.mention} получил **{_fmt(amount)} {cur}**. Баланс: {_fmt(acc['balance'])} {cur}.",
            ephemeral=True,
        )

    @app_commands.command(name="eco_take", description="Забрать монеты у участника")
    @app_commands.describe(user="У кого", amount="Сколько")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_take(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        data, acc = _acc(interaction.guild.id, user.id)
        taken = min(amount, acc["balance"])
        acc["balance"] -= taken
        save(ECONOMY_FILE, data)
        cur = _currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ У {user.mention} забрал **{_fmt(taken)} {cur}**. Баланс: {_fmt(acc['balance'])} {cur}.",
            ephemeral=True,
        )

    @app_commands.command(name="eco_set", description="Задать баланс участнику")
    @app_commands.describe(user="Кому", amount="Новый баланс")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_set(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 0]):
        data, acc = _acc(interaction.guild.id, user.id)
        acc["balance"] = amount
        save(ECONOMY_FILE, data)
        cur = _currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Баланс {user.mention} = **{_fmt(amount)} {cur}**.", ephemeral=True
        )

    @app_commands.command(name="eco_config", description="Настройки экономики")
    @app_commands.describe(
        currency="Название валюты (например, 🍕 или коины)",
        daily_amount="Размер дейлика",
        max_bet="Максимальная ставка в играх",
        channel="Канал, где работают команды экономики",
        drop_channel="Канал для дропов (по умолчанию — канал экономики)",
        drop_every="Через сколько сообщений падает дроп",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_config(
        self,
        interaction: discord.Interaction,
        currency: str = None,
        daily_amount: app_commands.Range[int, 1] = None,
        max_bet: app_commands.Range[int, 10] = None,
        channel: discord.TextChannel = None,
        drop_channel: discord.TextChannel = None,
        drop_every: app_commands.Range[int, 20, 10000] = None,
    ):
        data = load(SETTINGS_FILE, {})
        gcfg = data.setdefault(str(interaction.guild.id), {})
        if currency:
            gcfg["currency"] = currency[:20]
        if daily_amount:
            gcfg["daily_amount"] = daily_amount
        if max_bet:
            gcfg["max_bet"] = max_bet
        if channel:
            gcfg["channel_id"] = channel.id
        if drop_channel:
            gcfg["drop_channel_id"] = drop_channel.id
        if drop_every:
            gcfg["drop_every"] = drop_every
        save(SETTINGS_FILE, data)

        eco_ch = _eco_channel(interaction.guild.id)
        drop_ch = _drop_channel(interaction.guild.id)
        await interaction.response.send_message(
            embed=_embed(
                "⚙️ Экономика настроена",
                f"Валюта: **{_currency(interaction.guild.id)}**\n"
                f"Дейлик: **{_fmt(_daily_amount(interaction.guild.id))}**\n"
                f"Макс. ставка: **{_fmt(_max_bet(interaction.guild.id))}**\n"
                f"Канал экономики: {f'<#{eco_ch}>' if eco_ch else '— (ищу «карзину» по имени)'}\n"
                f"Канал дропов: {f'<#{drop_ch}>' if drop_ch else '—'}\n"
                f"Дроп каждые **{_drop_every(interaction.guild.id)}** сообщений",
            )
        )

    @app_commands.command(name="eco_stats", description="Статистика экономики сервера")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_stats(self, interaction: discord.Interaction):
        users = load(ECONOMY_FILE, {}).get(str(interaction.guild.id), {})
        stats = _stats_data().get(str(interaction.guild.id), {})
        cur = _currency(interaction.guild.id)

        total = 0
        active = 0
        for uid, info in users.items():
            _ensure(info)
            total += int(info.get("balance", 0))
            if info.get("last_seen") == _today():
                active += 1

        richest = sorted(users.items(), key=lambda kv: kv[1].get("balance", 0), reverse=True)[:5]
        earners = sorted(users.items(), key=lambda kv: kv[1].get("season_earned", 0), reverse=True)[:5]
        voice = sorted(stats.items(), key=lambda kv: kv[1].get("voice_total", 0), reverse=True)[:5]

        e = _embed("📈 Статистика экономики")
        e.add_field(name="Монет в обороте", value=f"**{_fmt(total)} {cur}**", inline=True)
        e.add_field(name="Всего счетов", value=f"**{_fmt(len(users))}**", inline=True)
        e.add_field(name="Активны сегодня", value=f"**{_fmt(active)}**", inline=True)
        if richest:
            e.add_field(
                name="Топ по балансу",
                value="\n".join(f"<@{uid}> — {_fmt(i['balance'])}" for uid, i in richest),
                inline=False,
            )
        if earners:
            e.add_field(
                name=f"Топ сезона {_month()}",
                value="\n".join(f"<@{uid}> — {_fmt(i['season_earned'])}" for uid, i in earners),
                inline=False,
            )
        if voice:
            e.add_field(
                name="Больше всех в войсе",
                value="\n".join(f"<@{uid}> — {_human_span(i.get('voice_total', 0))}" for uid, i in voice),
                inline=False,
            )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="eco_season_end", description="Закрыть сезон: выдать призы топ-3")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_season_end(self, interaction: discord.Interaction):
        data = load(SETTINGS_FILE, {})
        gcfg = data.setdefault(str(interaction.guild.id), {})
        if gcfg.get("season_paid") == _month():
            await interaction.response.send_message("Призы за этот месяц уже выданы.", ephemeral=True)
            return

        eco = load(ECONOMY_FILE, {})
        users = eco.get(str(interaction.guild.id), {})
        rows = []
        for uid, info in users.items():
            _ensure(info)
            rows.append((int(uid), int(info.get("season_earned", 0))))
        rows.sort(key=lambda x: x[1], reverse=True)
        winners = [row for row in rows[:3] if row[1] > 0]
        if not winners:
            await interaction.response.send_message("Некому выдавать призы.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        lines = []
        for place, (uid, earned) in enumerate(winners):
            prize = SEASON_PRIZES[place]
            acc = _acc_from(eco, interaction.guild.id, uid)
            acc["balance"] += prize
            lines.append(f"{['🥇', '🥈', '🥉'][place]} <@{uid}> — **{_fmt(prize)} {cur}** (заработал {_fmt(earned)})")
        save(ECONOMY_FILE, eco)
        gcfg["season_paid"] = _month()
        save(SETTINGS_FILE, data)

        await interaction.response.send_message(
            embed=_embed(f"🏁 Сезон {_month()} закрыт", "\n".join(lines))
        )

    @app_commands.command(name="eco_drop", description="Устроить дроп монет в канале дропов")
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_drop(self, interaction: discord.Interaction):
        amount = random.randint(DROP_MIN, DROP_MAX)
        await self._spawn_drop(interaction.guild, amount)
        await interaction.response.send_message("💰 Дроп отправил.", ephemeral=True)

    # ── Дропы и статистика: слушатели ─────────────────────────────────────

    async def _spawn_drop(self, guild: discord.Guild, amount: int) -> None:
        cid = _drop_channel(guild.id)
        channel = guild.get_channel(cid) if cid else None
        if channel is None:
            return
        cur = _currency(guild.id)
        e = _embed(
            "💰 Монеты на полу!",
            f"Кто-то обронил **{_fmt(amount)} {cur}**.\nКто первый нажал — того и монеты.",
        )
        try:
            await channel.send(
                embed=e,
                view=DropView(amount, cur),
                # Дроп живёт 3 минуты на кнопке; ещё 2 минуты — чтобы успеть увидеть итог
                delete_after=DROP_TTL + 120,
            )
        except discord.Forbidden:
            log.warning("Нет прав на отправку дропа в канал %s", cid)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        global _stats_dirty
        if message.author.bot or message.guild is None:
            return
        # message_content intent не нужен: считаем сами сообщения, а не их текст.

        st = _stat_user(message.guild.id, message.author.id)
        st["messages"] = int(st.get("messages", 0)) + 1
        _stats_dirty = True

        cid = _drop_channel(message.guild.id)
        if not cid or message.channel.id != cid:
            return
        count = self._msg_count.get(message.guild.id, 0) + 1
        if count < _drop_every(message.guild.id):
            self._msg_count[message.guild.id] = count
            return
        self._msg_count[message.guild.id] = 0
        await self._spawn_drop(message.guild, random.randint(DROP_MIN, DROP_MAX))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return
        guild = member.guild
        afk = guild.afk_channel
        was_in = before.channel is not None and before.channel != afk
        now_in = after.channel is not None and after.channel != afk
        key = (guild.id, member.id)

        if not was_in and now_in:
            _voice_since[key] = time.time()
        elif was_in and not now_in:
            started = _voice_since.pop(key, None)
            if started:
                _add_voice(guild.id, member.id, time.time() - started)
                _stats_save()
        # Переход между каналами не считается выходом — счётчик продолжает идти.


# ── Мини-игры ─────────────────────────────────────────────────────────────────

class GameBase:
    """Общие проверки для игр: лимиты и баланс."""

    @staticmethod
    async def _check_bet(interaction: discord.Interaction, amount: int) -> bool:
        if amount < BET_MIN:
            await interaction.response.send_message(
                f"Минимальная ставка — {_fmt(BET_MIN)} {_currency(interaction.guild.id)}.", ephemeral=True
            )
            return False
        if amount > _max_bet(interaction.guild.id):
            await interaction.response.send_message(
                f"Максимальная ставка — {_fmt(_max_bet(interaction.guild.id))} {_currency(interaction.guild.id)}.",
                ephemeral=True,
            )
            return False
        _, acc = _acc(interaction.guild.id, interaction.user.id)
        if acc["balance"] < amount:
            await interaction.response.send_message(
                f"Не хватает: ставка {_fmt(amount)}, у тебя {_fmt(acc['balance'])} {_currency(interaction.guild.id)}.",
                ephemeral=True,
            )
            return False
        return True


# ── Кости ─────────────────────────────────────────────────────────────────────

class DiceRematchView(TimeoutView):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="🎲 Кинуть ещё", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_dice(interaction, self.bet, edit=True)


async def play_dice(interaction: discord.Interaction, bet: int, edit: bool = False):
    if not edit and not await GameBase._check_bet(interaction, bet):
        return

    guild = interaction.guild
    data, acc = _acc(guild.id, interaction.user.id)
    if acc["balance"] < bet:
        msg = f"Не хватает: ставка {_fmt(bet)}, у тебя {_fmt(acc['balance'])} {_currency(guild.id)}."
        if edit:
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    acc["balance"] -= bet
    mine = [random.randint(1, 6), random.randint(1, 6)]
    bots = [random.randint(1, 6), random.randint(1, 6)]
    cur = _currency(guild.id)

    if sum(mine) > sum(bots):
        win = int(bet * 1.9)
        acc["balance"] += win
        title, color = "🎲 Победа!", 0x57F287
        result = f"Выиграл **{_fmt(win - bet)} {cur}** чистыми."
    elif sum(mine) == sum(bots):
        acc["balance"] += bet
        title, color = "🎲 Ничья", 0xFEE75C
        result = "Ставка вернулась на счёт."
    else:
        title, color = "🎲 Проигрыш", 0xED4245
        result = f"Потерял **{_fmt(bet)} {cur}**."

    _bump(acc, "games")
    save(ECONOMY_FILE, data)
    e = discord.Embed(
        title=title,
        description=(
            f"Ты: 🎲 {mine[0]} + {mine[1]} = **{sum(mine)}**\n"
            f"Бот: 🎲 {bots[0]} + {bots[1]} = **{sum(bots)}**\n\n"
            f"{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**"
        ),
        color=color,
        timestamp=datetime.datetime.now(),
    )
    view = DiceRematchView(bet) if acc["balance"] >= bet else None
    if edit:
        await interaction.response.edit_message(embed=e, view=view)
    else:
        await interaction.response.send_message(**_reply(e, view))


# ── Слоты ─────────────────────────────────────────────────────────────────────

class SlotsRematchView(TimeoutView):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="🔄 Крутить ещё", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_slots(interaction, self.bet, edit=True)


async def play_slots(interaction: discord.Interaction, bet: int, edit: bool = False):
    if not edit and not await GameBase._check_bet(interaction, bet):
        return

    guild = interaction.guild
    data, acc = _acc(guild.id, interaction.user.id)
    if acc["balance"] < bet:
        msg = f"Не хватает: ставка {_fmt(bet)}, у тебя {_fmt(acc['balance'])} {_currency(guild.id)}."
        if edit:
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    acc["balance"] -= bet
    reels = [random.choice(SLOTS_EMOJIS) for _ in range(3)]
    cur = _currency(guild.id)

    if reels[0] == reels[1] == reels[2]:
        win = int(bet * 10)
        title, color = "🎰 ДЖЕКПОТ!", 0xFEE75C
        result = f"Три в ряд — x10! Выиграл **{_fmt(win)} {cur}**."
        acc["balance"] += win
    elif len(set(reels)) == 2:
        win = int(bet * 1.5)
        title, color = "🎰 Почти!", 0x57F287
        result = f"Два в ряд — x1.5. Выиграл **{_fmt(win - bet)} {cur}** чистыми."
        acc["balance"] += win
    else:
        title, color = "🎰 Мимо", 0xED4245
        result = f"Потерял **{_fmt(bet)} {cur}**."

    _bump(acc, "games")
    save(ECONOMY_FILE, data)
    e = discord.Embed(
        title=title,
        description=f"# {' ┃ '.join(reels)}\n\n{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**",
        color=color,
        timestamp=datetime.datetime.now(),
    )
    view = SlotsRematchView(bet) if acc["balance"] >= bet else None
    if edit:
        await interaction.response.edit_message(embed=e, view=view)
    else:
        await interaction.response.send_message(**_reply(e, view))


# ── Монетка ───────────────────────────────────────────────────────────────────

class FlipView(TimeoutView):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="Орёл", emoji="🦅", style=discord.ButtonStyle.primary)
    async def heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_flip(interaction, self.bet, "heads")

    @discord.ui.button(label="Решка", emoji="🌙", style=discord.ButtonStyle.secondary)
    async def tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_flip(interaction, self.bet, "tails")


async def _do_flip(interaction: discord.Interaction, bet: int, choice: str):
    guild = interaction.guild
    data, acc = _acc(guild.id, interaction.user.id)
    if acc["balance"] < bet:
        await interaction.response.edit_message(
            content=f"Не хватает: ставка {_fmt(bet)}, у тебя {_fmt(acc['balance'])} {_currency(guild.id)}.",
            embed=None, view=None,
        )
        return

    acc["balance"] -= bet
    side = random.choice(["heads", "tails"])
    side_name = "Орёл 🦅" if side == "heads" else "Решка 🌙"
    my_name = "Орёл 🦅" if choice == "heads" else "Решка 🌙"
    cur = _currency(guild.id)

    if choice == side:
        win = int(bet * 1.9)
        acc["balance"] += win
        title, color = "🪙 Угадал!", 0x57F287
        result = f"Выпало **{side_name}**. Выиграл **{_fmt(win - bet)} {cur}** чистыми."
    else:
        title, color = "🪙 Не угадал", 0xED4245
        result = f"Выпало **{side_name}**, ты ставил на {my_name}. Потерял **{_fmt(bet)} {cur}**."

    _bump(acc, "games")
    save(ECONOMY_FILE, data)
    e = discord.Embed(
        title=title,
        description=f"{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**",
        color=color,
        timestamp=datetime.datetime.now(),
    )
    view = FlipView(bet) if acc["balance"] >= bet else None
    await interaction.response.edit_message(embed=e, view=view)


# ── Камень-ножницы-бумага ─────────────────────────────────────────────────────

class RPSView(TimeoutView):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="Камень", emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_rps(interaction, self.bet, "rock")

    @discord.ui.button(label="Бумага", emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_rps(interaction, self.bet, "paper")

    @discord.ui.button(label="Ножницы", emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_rps(interaction, self.bet, "scissors")


async def _do_rps(interaction: discord.Interaction, bet: int, choice: str):
    guild = interaction.guild
    data, acc = _acc(guild.id, interaction.user.id)
    if acc["balance"] < bet:
        await interaction.response.edit_message(
            content=f"Не хватает: ставка {_fmt(bet)}, у тебя {_fmt(acc['balance'])} {_currency(guild.id)}.",
            embed=None, view=None,
        )
        return

    acc["balance"] -= bet
    bot_choice = random.choice(list(RPS_CHOICES))
    cur = _currency(guild.id)

    if choice == bot_choice:
        acc["balance"] += bet
        title, color = "✌️ Ничья", 0xFEE75C
        result = f"У бота тоже {RPS_CHOICES[bot_choice]}. Ставка вернулась."
    elif RPS_BEATS[choice] == bot_choice:
        win = int(bet * 2)
        acc["balance"] += win
        title, color = "🎉 Победа!", 0x57F287
        result = f"У бота {RPS_CHOICES[bot_choice]}. Выиграл **{_fmt(bet)} {cur}** чистыми."
    else:
        title, color = "💀 Проигрыш", 0xED4245
        result = f"У бота {RPS_CHOICES[bot_choice]}. Потерял **{_fmt(bet)} {cur}**."

    _bump(acc, "games")
    save(ECONOMY_FILE, data)
    e = discord.Embed(
        title=title,
        description=(
            f"Ты: {RPS_CHOICES[choice]}\nБот: {RPS_CHOICES[bot_choice]}\n\n"
            f"{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**"
        ),
        color=color,
        timestamp=datetime.datetime.now(),
    )
    view = RPSView(bet) if acc["balance"] >= bet else None
    await interaction.response.edit_message(embed=e, view=view)


# ── Карты и новые игры ────────────────────────────────────────────────────────

CARD_RANKS = list(range(2, 15))          # 2..10, 11 J, 12 Q, 13 K, 14 A
CARD_SUITS = ["♠", "♥", "♦", "♣"]
CARD_FACES = {11: "J", 12: "Q", 13: "K", 14: "A"}
ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

BLACKJACK_PAYOUT = 2.5       # блэкджек платит x2.5 от ставки
CRASH_CAP = 50.0
CRASH_STEP = 1.2
CRASH_TICK = 1.0

MINES_ROWS = 4               # 4x5 = 20 клеток + кнопка «Забрать» — лимит Discord 25 кнопок
MINES_COLS = 5
MINES_CELLS = MINES_ROWS * MINES_COLS

TTT_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)


def _card(rank: int, suit: str) -> str:
    return f"{CARD_FACES.get(rank, str(rank))}{suit}"


def _draw() -> tuple:
    return random.choice(CARD_RANKS), random.choice(CARD_SUITS)


def _cards_text(cards) -> str:
    return " ".join(_card(rank, suit) for rank, suit in cards)


def _bj_value(cards) -> int:
    """Сумма очков: туз 11, пока не перебор."""
    total = 0
    aces = 0
    for rank, _suit in cards:
        if rank == 14:
            aces += 1
            total += 11
        else:
            total += min(rank, 10)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _bj_blackjack(cards) -> bool:
    return len(cards) == 2 and _bj_value(cards) == 21


def _bj_cells(cards, reveal_all: bool) -> list:
    """Тексты карт для таблицы: у дилера все карты, кроме первой, скрыты."""
    texts = []
    for i, (rank, suit) in enumerate(cards):
        if not reveal_all and i > 0:
            texts.append("??")
        else:
            texts.append(_card(rank, suit))
    return texts


def _bj_box(cells, value_text: str = "", color: str = "") -> str:
    """Карты в рамках, выровнено по моноширинной сетке.

    color — ANSI-код Discord ('0;32' зелёный, '0;31' красный, '0;33' жёлтый).
    """
    padded = [f" {c:>3} " for c in cells]
    top = "┌" + "┬".join("─" * len(c) for c in padded) + "┐"
    row = "│" + "│".join(padded) + "│"
    if value_text:
        row += f"  → {value_text}"
    bottom = "└" + "┴".join("─" * len(c) for c in padded) + "┘"
    body = top + "\n" + row + "\n" + bottom
    if color:
        body = f"\u001b[{color}m" + body + "\u001b[0m"
    return body


def _mines_multiplier(mines: int, opened: int) -> float:
    """Честный множитель за opened открытых клеток при mines минах (маржа 3%)."""
    if opened <= 0:
        return 1.0
    mult = 1.0
    safe = MINES_CELLS - mines
    for i in range(opened):
        mult *= (MINES_CELLS - i) / (safe - i)
    return round(mult * 0.97, 2)


def _crash_point(roll: float) -> float:
    """Точка краха из случайного roll [0, 1). Маржа 3%, потолок CRASH_CAP."""
    roll = min(max(roll, 0.0), 0.9999)
    return min(CRASH_CAP, max(1.0, round(0.97 / (1.0 - roll), 2)))


def _roulette_color(spin: int) -> str:
    if spin == 0:
        return "зеро"
    return "красное" if spin in ROULETTE_RED else "чёрное"


def _roulette_mult(choice: str, spin: int) -> int:
    if spin == 0:
        return 30 if choice == "зеро" else 0
    return 2 if choice == _roulette_color(spin) else 0


def _hl_chance(card: int, direction: str) -> float:
    if direction == "выше":
        return (14 - card) / 13
    if direction == "ниже":
        return (card - 2) / 13
    return 0.0


def _hl_mult(card: int, direction: str) -> float:
    """Честный множитель шага: p(угадал) * x == 0.97 — маржа 3% на каждом шаге."""
    chance = _hl_chance(card, direction)
    if chance <= 0:
        return 0.0
    return round(0.97 / chance, 2)


def _ttt_winner(board) -> str:
    for a, b, c in TTT_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return ""


def _ttt_full(board) -> bool:
    return all(board)


def _ttt_text(board) -> str:
    cells = [cell or "·" for cell in board]
    rows = [" ".join(cells[i:i + 3]) for i in range(0, 9, 3)]
    return "\n".join(rows)


# ── Блэкджек ──────────────────────────────────────────────────────────────────

class BlackjackView(TimeoutView):
    def __init__(self, guild_id: int, user_id: int, bet: int, player, dealer):
        super().__init__(timeout=240)
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.player = list(player)
        self.dealer = list(dealer)
        self.finished = False
        self.message = None

    def render_embed(self) -> discord.Embed:
        cur = _currency(self.guild_id)
        dealer_box = _bj_box(
            _bj_cells(self.dealer, reveal_all=False),
            f"{_bj_value(self.dealer[:1])}+?",
        )
        player_value = _bj_value(self.player)
        player_box = _bj_box(
            _bj_cells(self.player, reveal_all=True),
            f"{player_value} ← БЛЭКДЖЕК" if _bj_blackjack(self.player) else str(player_value),
        )
        table = (
            "```ansi\n"
            "🂠 ДИЛЕР\n" + dealer_box + "\n\n"
            "👤 ТЫ\n" + player_box + "\n"
            "```"
        )
        hint = (
            f"Ставка: **{_fmt(self.bet)} {cur}** · "
            f"блэкджек платит x{BLACKJACK_PAYOUT:g} · дилер добирает до 17"
        )
        return _embed("🃏 Блэкджек", f"{table}\n{hint}")

    def dealer_play(self) -> None:
        while _bj_value(self.dealer) < 17:
            self.dealer.append(_draw())

    async def settle(self) -> None:
        self.finished = True
        for child in self.children:
            child.disabled = True
        cur = _currency(self.guild_id)
        data, acc = _acc(self.guild_id, self.user_id)
        player_value = _bj_value(self.player)
        dealer_value = _bj_value(self.dealer)
        player_bj = _bj_blackjack(self.player)
        dealer_bj = _bj_blackjack(self.dealer)

        if player_value > 21:
            outcome, color = "lose", 0xED4245
            result = f"💀 ПЕРЕБОР — −{_fmt(self.bet)} {cur}"
        elif dealer_bj and not player_bj:
            outcome, color = "lose", 0xED4245
            result = f"💀 У ДИЛЕРА БЛЭКДЖЕК — −{_fmt(self.bet)} {cur}"
        elif player_bj:
            win = int(self.bet * BLACKJACK_PAYOUT)
            acc["balance"] += win
            outcome, color = "win", 0xFEE75C
            result = f"🎉 БЛЭКДЖЕК! +{_fmt(win - self.bet)} {cur}"
        elif dealer_value > 21 or player_value > dealer_value:
            acc["balance"] += self.bet * 2
            outcome, color = "win", 0x57F287
            result = f"🎉 ПОБЕДА +{_fmt(self.bet)} {cur}"
        elif player_value == dealer_value:
            acc["balance"] += self.bet
            outcome, color = "push", 0xFEE75C
            result = "🤝 НИЧЬЯ — ставка вернулась"
        else:
            outcome, color = "lose", 0xED4245
            result = f"💀 ПРОИГРЫШ — −{_fmt(self.bet)} {cur}"

        _bump(acc, "games")
        save(ECONOMY_FILE, data)

        ansi = {"win": "0;32", "push": "0;33", "lose": "0;31"}[outcome]
        dealer_box = _bj_box(_bj_cells(self.dealer, reveal_all=True), str(dealer_value), ansi)
        player_box = _bj_box(_bj_cells(self.player, reveal_all=True), str(player_value), ansi)
        table = (
            "```ansi\n"
            "🂠 ДИЛЕР\n" + dealer_box + "\n\n"
            "👤 ТЫ\n" + player_box + "\n"
            "```"
        )
        e = discord.Embed(
            title="🃏 Блэкджек",
            description=f"{table}\n**{result}**\nБаланс: **{_fmt(acc['balance'])} {cur}**",
            color=color,
            timestamp=datetime.datetime.now(),
        )
        if self.message is not None:
            try:
                await self.message.edit(embed=e, view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Ещё", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужая раздача.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Раздача уже закрыта.", ephemeral=True)
            return
        self.player.append(_draw())
        await interaction.response.defer()
        if _bj_value(self.player) > 21:
            await self.settle()
        else:
            await self.message.edit(embed=self.render_embed(), view=self)

    @discord.ui.button(label="Хватит", emoji="🛑", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужая раздача.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Раздача уже закрыта.", ephemeral=True)
            return
        self.dealer_play()
        await interaction.response.defer()
        await self.settle()

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += self.bet
        save(ECONOMY_FILE, data)
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed("🃏 Блэкджек", "Время вышло — ставка вернулась."), view=None
                )
            except discord.HTTPException:
                pass


async def play_blackjack(interaction: discord.Interaction, bet: int):
    if not await GameBase._check_bet(interaction, bet):
        return
    guild = interaction.guild
    data, acc = _acc(guild.id, interaction.user.id)
    acc["balance"] -= bet
    save(ECONOMY_FILE, data)

    view = BlackjackView(
        guild.id, interaction.user.id, bet, [_draw(), _draw()], [_draw(), _draw()]
    )
    await interaction.response.send_message(embed=view.render_embed(), view=view)
    view.message = await interaction.original_response()
    if _bj_blackjack(view.player):
        await view.settle()


# ── Мины ──────────────────────────────────────────────────────────────────────

class MinesView(TimeoutView):
    def __init__(self, guild_id: int, user_id: int, bet: int, mines: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.mines = mines
        self.opened = 0
        self.finished = False
        self.message = None
        self.mine_cells = set(random.sample(range(MINES_CELLS), mines))
        for index in range(MINES_CELLS):
            button = discord.ui.Button(
                label="·", style=discord.ButtonStyle.secondary, row=index // MINES_COLS
            )
            button.callback = self._cell_cb(index)
            self.add_item(button)
        self.cash = discord.ui.Button(
            label="💸 Забрать", style=discord.ButtonStyle.success, row=MINES_ROWS
        )
        self.cash.callback = self._cash_cb
        self.add_item(self.cash)

    def multiplier(self) -> float:
        return _mines_multiplier(self.mines, self.opened)

    def render_embed(self, text: str = "") -> discord.Embed:
        cur = _currency(self.guild_id)
        mult = self.multiplier()
        body = (
            f"Мин: **{self.mines}** · открыто: **{self.opened}/{MINES_CELLS - self.mines}**\n"
            f"Множитель: **x{mult:.2f}** · забрать: **{_fmt(int(self.bet * mult))} {cur}**\n"
        )
        if text:
            body += f"\n{text}"
        return _embed("💣 Мины", body)

    def freeze(self) -> None:
        self.finished = True
        for child in self.children:
            child.disabled = True

    def _cell_cb(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Это чужое поле.", ephemeral=True)
                return
            if self.finished:
                await interaction.response.send_message("Игра уже закончена.", ephemeral=True)
                return
            button = self.children[index]
            if button.disabled:
                await interaction.response.send_message("Эта клетка уже открыта.", ephemeral=True)
                return
            if index in self.mine_cells:
                self.freeze()
                for child_index in self.mine_cells:
                    mine_button = self.children[child_index]
                    mine_button.label = "💣"
                    mine_button.style = discord.ButtonStyle.danger
                cur = _currency(self.guild_id)
                await interaction.response.edit_message(
                    embed=self.render_embed(f"💥 Мина! Потерял **{_fmt(self.bet)} {cur}**."),
                    view=self,
                )
                return
            self.opened += 1
            button.label = "💎"
            button.style = discord.ButtonStyle.success
            button.disabled = True
            await interaction.response.edit_message(embed=self.render_embed(), view=self)

        return callback

    async def _cash_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужое поле.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Игра уже закончена.", ephemeral=True)
            return
        payout = int(self.bet * self.multiplier())
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += payout
        _bump(acc, "games")
        save(ECONOMY_FILE, data)
        self.freeze()
        cur = _currency(self.guild_id)
        await interaction.response.edit_message(
            embed=_embed(
                "💣 Мины",
                f"Забрал **{_fmt(payout)} {cur}** на **x{self.multiplier():.2f}** "
                f"(открыто {self.opened}).\nБаланс: **{_fmt(acc['balance'])} {cur}**",
            ),
            view=None,
        )

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += self.bet
        save(ECONOMY_FILE, data)
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed("💣 Мины", "Время вышло — ставка вернулась."), view=None
                )
            except discord.HTTPException:
                pass


# ── Краш ──────────────────────────────────────────────────────────────────────

class CrashView(TimeoutView):
    def __init__(self, guild_id: int, user_id: int, bet: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.point = _crash_point(random.random())
        self.mult = 1.0
        self.finished = False
        self.message = None
        self.task = None

    def render_embed(self, text: str = "") -> discord.Embed:
        cur = _currency(self.guild_id)
        body = f"# x{self.mult:.2f}\n\n"
        body += text or f"Забрать сейчас: **{_fmt(int(self.bet * self.mult))} {cur}**"
        return _embed("🚀 Краш", body)

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            while not self.finished:
                await asyncio.sleep(CRASH_TICK)
                if self.finished:
                    return
                nxt = round(self.mult * CRASH_STEP, 2)
                if nxt >= self.point:
                    self.mult = self.point
                    self.finished = True
                    self.cash.disabled = True
                    if self.message is not None:
                        await self.message.edit(
                            embed=self.render_embed(
                                f"💥 Краш на **x{self.point:.2f}**. Ставка сгорела."
                            ),
                            view=self,
                        )
                    return
                self.mult = nxt
                if self.message is not None:
                    self.cash.label = f"💸 Забрать x{self.mult:.2f}"
                    await self.message.edit(embed=self.render_embed(), view=self)
        except asyncio.CancelledError:
            raise
        except discord.HTTPException:
            pass

    @discord.ui.button(label="💸 Забрать x1.00", style=discord.ButtonStyle.success)
    async def cash(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужая игра.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Поздно — уже крашнуло.", ephemeral=True)
            return
        self.finished = True
        if self.task is not None:
            self.task.cancel()
        payout = int(self.bet * self.mult)
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += payout
        _bump(acc, "games")
        save(ECONOMY_FILE, data)
        self.cash.disabled = True
        cur = _currency(self.guild_id)
        await interaction.response.edit_message(
            embed=_embed(
                "🚀 Краш",
                f"Забрал на **x{self.mult:.2f}** — **{_fmt(payout)} {cur}**.\n"
                f"Баланс: **{_fmt(acc['balance'])} {cur}**",
            ),
            view=None,
        )

    async def on_timeout(self):
        if self.task is not None:
            self.task.cancel()
        if self.finished:
            return
        self.finished = True
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += self.bet
        save(ECONOMY_FILE, data)
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed("🚀 Краш", "Время вышло — ставка вернулась."), view=None
                )
            except discord.HTTPException:
                pass


# ── Выше-ниже ─────────────────────────────────────────────────────────────────

class HighLowView(TimeoutView):
    def __init__(self, guild_id: int, user_id: int, bet: int, card: tuple):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.card = card
        self.mult = 1.0
        self.finished = False
        self.message = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.above.disabled = self.finished or _hl_mult(self.card[0], "выше") <= 0
        self.below.disabled = self.finished or _hl_mult(self.card[0], "ниже") <= 0

    def render_embed(self, text: str = "") -> discord.Embed:
        cur = _currency(self.guild_id)
        body = (
            f"Карта: **{_card(*self.card)}**\n"
            f"Множитель: **x{self.mult:.2f}** · забрать: **{_fmt(int(self.bet * self.mult))} {cur}**\n"
        )
        if text:
            body += f"\n{text}"
        return _embed("🃏 Выше-ниже", body)

    def freeze(self) -> None:
        self.finished = True
        for child in self.children:
            child.disabled = True

    async def _guess(self, interaction: discord.Interaction, direction: str):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужая игра.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Игра уже закончена.", ephemeral=True)
            return
        step = _hl_mult(self.card[0], direction)
        if step <= 0:
            await interaction.response.send_message("С этой карты идти уже некуда.", ephemeral=True)
            return

        previous = self.card
        nxt = _draw()
        cur = _currency(self.guild_id)

        if nxt[0] == previous[0]:
            self.freeze()
            await interaction.response.edit_message(
                embed=self.render_embed(
                    f"Выпала та же карта **{_card(*nxt)}** — ничья в пользу казино. "
                    f"Потерял **{_fmt(self.bet)} {cur}**."
                ),
                view=None,
            )
            return

        won = (direction == "выше" and nxt[0] > previous[0]) or (
            direction == "ниже" and nxt[0] < previous[0]
        )
        if not won:
            self.freeze()
            await interaction.response.edit_message(
                embed=self.render_embed(
                    f"Выпала **{_card(*nxt)}** — не угадал. Потерял **{_fmt(self.bet)} {cur}**."
                ),
                view=None,
            )
            return

        self.mult = round(self.mult * step, 2)
        self.card = nxt
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.render_embed(f"Выпала **{_card(*nxt)}** — угадал."),
            view=self,
        )

    @discord.ui.button(label="Выше", emoji="⬆️", style=discord.ButtonStyle.primary)
    async def above(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._guess(interaction, "выше")

    @discord.ui.button(label="Ниже", emoji="⬇️", style=discord.ButtonStyle.primary)
    async def below(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._guess(interaction, "ниже")

    @discord.ui.button(label="💸 Забрать", style=discord.ButtonStyle.success)
    async def cash(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это чужая игра.", ephemeral=True)
            return
        if self.finished:
            await interaction.response.send_message("Игра уже закончена.", ephemeral=True)
            return
        payout = int(self.bet * self.mult)
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += payout
        _bump(acc, "games")
        save(ECONOMY_FILE, data)
        self.freeze()
        cur = _currency(self.guild_id)
        await interaction.response.edit_message(
            embed=_embed(
                "🃏 Выше-ниже",
                f"Забрал **{_fmt(payout)} {cur}** на **x{self.mult:.2f}**.\n"
                f"Баланс: **{_fmt(acc['balance'])} {cur}**",
            ),
            view=None,
        )

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        data, acc = _acc(self.guild_id, self.user_id)
        acc["balance"] += self.bet
        save(ECONOMY_FILE, data)
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed("🃏 Выше-ниже", "Время вышло — ставка вернулась."), view=None
                )
            except discord.HTTPException:
                pass


# ── Крестики-нолики на ставку ─────────────────────────────────────────────────

class TicTacToeView(TimeoutView):
    def __init__(self, guild_id: int, bet: int, x_id: int, o_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.bet = bet
        self.x_id = x_id
        self.o_id = o_id
        self.board = [""] * 9
        self.turn = "X"
        self.finished = False
        self.message = None
        for index in range(9):
            button = discord.ui.Button(
                label="·", style=discord.ButtonStyle.secondary, row=index // 3
            )
            button.callback = self._move_cb(index)
            self.add_item(button)

    def current_id(self) -> int:
        return self.x_id if self.turn == "X" else self.o_id

    def render_embed(self, text: str = "") -> discord.Embed:
        cur = _currency(self.guild_id)
        mark = "❌" if self.turn == "X" else "⭕"
        body = (
            f"❌ <@{self.x_id}> против ⭕ <@{self.o_id}>\n"
            f"Банк: **{_fmt(self.bet * 2)} {cur}**\n\n"
            f"Ход: {mark} <@{self.current_id()}>\n"
        )
        if text:
            body += f"\n{text}"
        return _embed("⭕ Крестики-нолики", body)

    def freeze(self) -> None:
        self.finished = True
        for child in self.children:
            child.disabled = True

    def _move_cb(self, index: int):
        async def callback(interaction: discord.Interaction):
            if self.finished:
                await interaction.response.send_message("Партия уже закончена.", ephemeral=True)
                return
            if interaction.user.id != self.current_id():
                await interaction.response.send_message("Сейчас не твой ход.", ephemeral=True)
                return
            if self.board[index]:
                await interaction.response.send_message("Клетка занята.", ephemeral=True)
                return

            self.board[index] = self.turn
            button = self.children[index]
            button.label = "❌" if self.turn == "X" else "⭕"
            button.disabled = True
            button.style = (
                discord.ButtonStyle.danger if self.turn == "X" else discord.ButtonStyle.primary
            )

            winner = _ttt_winner(self.board)
            if winner or _ttt_full(self.board):
                await self._finish(interaction, winner)
                return

            self.turn = "O" if self.turn == "X" else "X"
            await interaction.response.edit_message(embed=self.render_embed(), view=self)

        return callback

    async def _finish(self, interaction: discord.Interaction, winner: str):
        self.freeze()
        cur = _currency(self.guild_id)
        data = load(ECONOMY_FILE, {})
        x_acc = _acc_from(data, self.guild_id, self.x_id)
        o_acc = _acc_from(data, self.guild_id, self.o_id)
        if winner:
            winner_id = self.x_id if winner == "X" else self.o_id
            acc = x_acc if winner == "X" else o_acc
            acc["balance"] += self.bet * 2
            text = f"Победил <@{winner_id}> — забрал банк **{_fmt(self.bet * 2)} {cur}**."
        else:
            x_acc["balance"] += self.bet
            o_acc["balance"] += self.bet
            text = "Ничья — ставки вернулись."
        _bump(x_acc, "games")
        _bump(o_acc, "games")
        save(ECONOMY_FILE, data)
        await interaction.response.edit_message(
            embed=_embed(
                "⭕ Крестики-нолики",
                f"{_ttt_text(self.board)}\n\n{text}",
            ),
            view=None,
        )

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        data = load(ECONOMY_FILE, {})
        x_acc = _acc_from(data, self.guild_id, self.x_id)
        o_acc = _acc_from(data, self.guild_id, self.o_id)
        x_acc["balance"] += self.bet
        o_acc["balance"] += self.bet
        save(ECONOMY_FILE, data)
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed("⭕ Крестики-нолики", "Время вышло — ставки вернулись."),
                    view=None,
                )
            except discord.HTTPException:
                pass


class TicTacToeChallengeView(TimeoutView):
    def __init__(self, guild_id: int, challenger_id: int, opponent_id: int, bet: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.bet = bet

    @discord.ui.button(label="Принять", emoji="⭕", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("Это чужой вызов.", ephemeral=True)
            return
        cur = _currency(self.guild_id)
        data = load(ECONOMY_FILE, {})
        challenger = _acc_from(data, self.guild_id, self.challenger_id)
        opponent = _acc_from(data, self.guild_id, self.opponent_id)
        if challenger["balance"] < self.bet:
            await interaction.response.edit_message(
                embed=_embed("⭕ Вызов сорвался", "У вызывающего не хватает монет."), view=None
            )
            return
        if opponent["balance"] < self.bet:
            await interaction.response.send_message(
                f"Не хватает: ставка {_fmt(self.bet)} {cur}.", ephemeral=True
            )
            return
        challenger["balance"] -= self.bet
        opponent["balance"] -= self.bet
        save(ECONOMY_FILE, data)
        view = TicTacToeView(self.guild_id, self.bet, self.challenger_id, self.opponent_id)
        self.stop()
        await interaction.response.edit_message(embed=view.render_embed(), view=view)
        view.message = await interaction.original_response()

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message("Это чужой вызов.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(
            embed=_embed("⭕ Вызов отменён", f"{interaction.user.mention} отказался."), view=None
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
