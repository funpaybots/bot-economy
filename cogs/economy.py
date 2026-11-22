# -*- coding: utf-8 -*-
"""Экономика: монеты, дейли, работа, переводы, магазин ролей."""

import datetime
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save

log = logging.getLogger(__name__)

ECONOMY_FILE = "economy.json"   # {guild_id: {user_id: {balance, last_daily, last_work}}}
SHOP_FILE = "shop.json"         # {guild_id: {role_id: {price, name}}}
SETTINGS_FILE = "settings.json"  # {guild_id: {currency, daily_amount}}

DEFAULT_CURRENCY = "🪙"
DEFAULT_DAILY = 250
WORK_COOLDOWN = 60 * 60            # час
DAILY_COOLDOWN = 60 * 60 * 24      # сутки
WORK_MIN, WORK_MAX = 50, 150

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

COLOR = 0xA9C9FF


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def _settings(guild_id: int) -> dict:
    return load(SETTINGS_FILE, {}).get(str(guild_id), {})


def _currency(guild_id: int) -> str:
    return _settings(guild_id).get("currency", DEFAULT_CURRENCY)


def _daily_amount(guild_id: int) -> int:
    return int(_settings(guild_id).get("daily_amount", DEFAULT_DAILY))


def _acc(guild_id: int, user_id: int):
    """Возвращает (все данные, аккаунт пользователя). Аккаунт не сохраняется сам."""
    data = load(ECONOMY_FILE, {})
    acc = data.setdefault(str(guild_id), {}).setdefault(str(user_id), {})
    acc.setdefault("balance", 0)
    return data, acc


def _human_left(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{int(hours)} ч {int(minutes)} мин"
    if minutes:
        return f"{int(minutes)} мин"
    return f"{seconds} сек"


def _embed(title: str, description: str = None) -> discord.Embed:
    e = discord.Embed(title=title, color=COLOR, timestamp=datetime.datetime.now())
    if description:
        e.description = description
    return e


def _shop(guild_id: int) -> dict:
    return load(SHOP_FILE, {}).get(str(guild_id), {})


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Баланс ────────────────────────────────────────────────────────────

    @app_commands.command(name="balance", description="Показать баланс")
    @app_commands.describe(user="Чей баланс (по умолчанию твой)")
    async def balance(self, interaction: discord.Interaction, user: discord.Member = None):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
        await interaction.response.send_message(embed=e)

    # ── Дейлик ────────────────────────────────────────────────────────────

    @app_commands.command(name="daily", description="Забрать ежедневную награду")
    async def daily(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        data, acc = _acc(interaction.guild.id, interaction.user.id)
        now = time.time()
        left = DAILY_COOLDOWN - (now - acc.get("last_daily", 0))
        if left > 0:
            await interaction.response.send_message(
                f"⏳ Дейлик уже забрал. Следующий через **{_human_left(left)}**.", ephemeral=True
            )
            return

        amount = _daily_amount(interaction.guild.id)
        cur = _currency(interaction.guild.id)
        acc["balance"] += amount
        acc["last_daily"] = now
        save(ECONOMY_FILE, data)

        await interaction.response.send_message(
            embed=_embed("💳 Дейлик", f"Забрал **{_fmt(amount)} {cur}**.\nБаланс: **{_fmt(acc['balance'])} {cur}**")
        )

    # ── Работа ────────────────────────────────────────────────────────────

    @app_commands.command(name="work", description="Подработать (раз в час)")
    async def work(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
        acc["balance"] += amount
        acc["last_work"] = now
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
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("Ботам монеты не нужны.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("Себе переводить — это стирание граней.", ephemeral=True)
            return

        cur = _currency(interaction.guild.id)
        data, sender = _acc(interaction.guild.id, interaction.user.id)
        if sender["balance"] < amount:
            await interaction.response.send_message(
                f"Не хватает. У тебя {_fmt(sender['balance'])} {cur}.", ephemeral=True
            )
            return

        _, receiver = _acc(interaction.guild.id, user.id)
        sender["balance"] -= amount
        receiver["balance"] += amount
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
    async def top(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
    async def shop(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
    async def buy(self, interaction: discord.Interaction, role: discord.Role):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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

        try:
            await member.add_roles(role, reason=f"Покупка в магазине ({interaction.user})")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не смог выдать роль — нет права «Управлять ролями» или роль выше моей.",
                ephemeral=True,
            )
            return

        acc["balance"] -= price
        save(ECONOMY_FILE, data)
        await interaction.response.send_message(
            embed=_embed(
                "🛒 Покупка",
                f"{member.mention} купил {role.mention} за **{_fmt(price)} {cur}**.\n"
                f"Осталось: **{_fmt(acc['balance'])} {cur}**",
            )
        )

    # ── Настройка магазина (админ) ────────────────────────────────────────

    @app_commands.command(name="shop_add", description="Добавить роль в магазин")
    @app_commands.describe(role="Роль на продажу", price="Цена в монетах")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_add(self, interaction: discord.Interaction, role: discord.Role, price: app_commands.Range[int, 1]):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        data, acc = _acc(interaction.guild.id, user.id)
        acc["balance"] += amount
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
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
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
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        data, acc = _acc(interaction.guild.id, user.id)
        acc["balance"] = amount
        save(ECONOMY_FILE, data)
        cur = _currency(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Баланс {user.mention} = **{_fmt(amount)} {cur}**.", ephemeral=True
        )

    @app_commands.command(name="eco_config", description="Настроить валюту, дейлик и лимит ставки")
    @app_commands.describe(
        currency="Название валюты (например, 🍕 или коины)",
        daily_amount="Размер дейлика",
        max_bet="Максимальная ставка в играх",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def eco_config(
        self,
        interaction: discord.Interaction,
        currency: str = None,
        daily_amount: app_commands.Range[int, 1] = None,
        max_bet: app_commands.Range[int, 10] = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        data = load(SETTINGS_FILE, {})
        gcfg = data.setdefault(str(interaction.guild.id), {})
        if currency:
            gcfg["currency"] = currency[:20]
        if daily_amount:
            gcfg["daily_amount"] = daily_amount
        if max_bet:
            gcfg["max_bet"] = max_bet
        save(SETTINGS_FILE, data)
        await interaction.response.send_message(
            embed=_embed(
                "⚙️ Экономика настроена",
                f"Валюта: **{_currency(interaction.guild.id)}**\n"
                f"Дейлик: **{_fmt(_daily_amount(interaction.guild.id))}**\n"
                f"Макс. ставка: **{_fmt(_max_bet(interaction.guild.id))}**",
            )
        )

    # ── Мини-игры: команды ────────────────────────────────────────────────

    @app_commands.command(name="games", description="Список мини-игр и правила")
    async def games(self, interaction: discord.Interaction):
        cur = _currency(interaction.guild.id) if interaction.guild else DEFAULT_CURRENCY
        e = _embed(
            "🎮 Мини-игры",
            f"Ставки: от **{_fmt(BET_MIN)}** до **{_fmt(_max_bet(interaction.guild.id) if interaction.guild else DEFAULT_MAX_BET)} {cur}**\n\n"
            "🎲 **/dice ставка** — кости против бота, у кого сумма выше.\n"
            "Победа x1.9, ничья — ставка возвращается\n\n"
            "🎰 **/slots ставка** — три барабана.\n"
            "Три в ряд x10, два в ряд x1.5\n\n"
            "🪙 **/flip ставка** — угадай орёл или решка. Угадал x1.9\n\n"
            "✌️ **/rps ставка** — камень-ножницы-бумага против бота. Победа x2",
        )
        e.set_footer(text="Игры — это слив монет в никуда, играй в удовольствие")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="dice", description="Кости против бота — у кого сумма выше")
    @app_commands.describe(bet="Ставка")
    async def dice(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        await play_dice(interaction, bet)

    @app_commands.command(name="slots", description="Слот-машина: три в ряд = x10")
    @app_commands.describe(bet="Ставка")
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        await play_slots(interaction, bet)

    @app_commands.command(name="flip", description="Орёл или решка — угадай сторону")
    @app_commands.describe(bet="Ставка")
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
    async def rps(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        if not await GameBase._check_bet(interaction, bet):
            return
        e = _embed(
            "✌️ КНБ",
            f"Ставка: **{_fmt(bet)} {_currency(interaction.guild.id)}**\nДелай ход:",
        )
        await interaction.response.send_message(embed=e, view=RPSView(bet))


# ── Мини-игры ─────────────────────────────────────────────────────────────────

BET_MIN = 10
DEFAULT_MAX_BET = 1000

SLOTS_EMOJIS = ["🍒", "🍋", "🍇", "💎", "🔔", "7️⃣"]
RPS_CHOICES = {"rock": "🪨 Камень", "paper": "📄 Бумага", "scissors": "✂️ Ножницы"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def _max_bet(guild_id: int) -> int:
    return int(_settings(guild_id).get("max_bet", DEFAULT_MAX_BET))


def _game_view():
    return discord.ui.View(timeout=120, disable_on_timeout=True)


class GameBase:
    """Общие проверки для игр: гильдия, лимиты, баланс."""

    @staticmethod
    async def _check_bet(interaction: discord.Interaction, amount: int) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return False
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

class DiceRematchView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120, disable_on_timeout=True)
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
        await interaction.response.send_message(embed=e, view=view)


# ── Слоты ─────────────────────────────────────────────────────────────────────

class SlotsRematchView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120, disable_on_timeout=True)
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

    save(ECONOMY_FILE, data)
    e = discord.Embed(
        title=title,
        description=(
            f"# {' ┃ '.join(reels)}\n\n"
            f"{result}\nБаланс: **{_fmt(acc['balance'])} {cur}**"
        ),
        color=color,
        timestamp=datetime.datetime.now(),
    )
    view = SlotsRematchView(bet) if acc["balance"] >= bet else None
    if edit:
        await interaction.response.edit_message(embed=e, view=view)
    else:
        await interaction.response.send_message(embed=e, view=view)


# ── Монетка ───────────────────────────────────────────────────────────────────

class FlipView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120, disable_on_timeout=True)
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

class RPSView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120, disable_on_timeout=True)
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
