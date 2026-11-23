# -*- coding: utf-8 -*-
"""Смоук-проверка перед пушем: все вьюхи создаются, команды на месте.

Запуск: python check_views.py
Ловит такие вещи, как несуществующий аргумент discord.ui.View,
из-за которого команда падает уже на сервере.
"""

import sys

import cogs.economy as eco

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
for cls, args in CASES:
    try:
        view = cls(*args)
    except Exception as e:
        failed += 1
        print(f"FAIL {cls.__name__}: {type(e).__name__}: {e}")
        continue
    label = "OK   " if view.children else "FAIL "
    failed += 0 if view.children else 1
    print(f"{label}{cls.__name__}: {len(view.children)} кнопок")

commands = eco.Economy.__cog_app_commands__
print(f"\nКоманд в коге: {len(commands)}")
if len(commands) != 27:
    print("FAIL ожидалось 27 команд — проверь декораторы/отступы")
    failed += 1

# Кириллица не должна превращаться в кракозябры при правках файла.
sample = eco.Economy.balance.description
if "баланс" not in sample.lower():
    print(f"FAIL кодировка: {sample!r}")
    failed += 1

print(f"\nПровалов: {failed}")
sys.exit(1 if failed else 0)
