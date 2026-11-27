# -*- coding: utf-8 -*-
"""Проверка новых игр экономики: блэкджек, мины, краш, рулетка, выше-ниже, крестики-нолики.

Запуск: python check_games.py
"""

import os
import sys
import tempfile

import storage

# Не трогаем боевую папку data/ — всё пишем во временную.
storage.DATA_DIR = os.path.join(tempfile.mkdtemp(prefix="games_check_"), "data")

import cogs.economy as eco  # noqa: E402

failed = 0


def check(name, cond, extra=""):
    global failed
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        failed += 1


# ── Блэкджек ──────────────────────────────────────────────────────────────────

check("bj blackjack A+K", eco._bj_value([(14, "♠"), (13, "♥")]) == 21)
check("bj blackjack detected", eco._bj_blackjack([(14, "♠"), (13, "♥")]))
check("bj two aces soften", eco._bj_value([(14, "♠"), (14, "♥")]) == 12)
check("bj ace+9+5", eco._bj_value([(14, "♠"), (9, "♥"), (5, "♦")]) == 15)
check("bj ten+ten+two", eco._bj_value([(10, "♠"), (10, "♥"), (2, "♦")]) == 22)
check("bj king counts as 10", eco._bj_value([(13, "♠"), (10, "♥")]) == 20)
check("bj not blackjack with 3 cards", not eco._bj_blackjack([(7, "♠"), (7, "♥"), (7, "♦")]))
check("bj not blackjack at 20", not eco._bj_blackjack([(9, "♠"), (13, "♥")]))
check("bj ace+face+8 = 19", eco._bj_value([(14, "♠"), (12, "♥"), (8, "♦")]) == 19)

# Оформление: рамки, выравнивание, ANSI-цвета
box = eco._bj_box(["A♠", "10♦"], "21")
lines = box.split("\n")
check("bj box three lines", len(lines) == 3)
check("bj box borders aligned", len(lines[0]) == len(lines[2]) == 13, f"{len(lines[0])}/{len(lines[2])}")
check("bj box shows cards", "A♠" in lines[1] and "10♦" in lines[1])
check("bj box shows value", lines[1].endswith("→ 21"))
check("bj box green ansi", eco._bj_box(["A♠"], "21", "0;32").startswith("\u001b[0;32m"))
dealer_cells = eco._bj_cells([(9, "♦"), (13, "♥")], reveal_all=False)
check("bj dealer hides all but first", dealer_cells == ["9♦", "??"])
check("bj ten is three chars", len(eco._bj_cells([(10, "♦")], True)[0]) == 3)
bj_view = eco.BlackjackView(1, 2, 10, [(2, "♠"), (3, "♥")], [(4, "♦"), (5, "♣")])
bj_emb = bj_view.render_embed()
check("bj play embed has table", "```ansi" in bj_emb.description and "ДИЛЕР" in bj_emb.description and "??" in bj_emb.description)

# ── Мины ──────────────────────────────────────────────────────────────────────

check("mines empty multiplier is 1", eco._mines_multiplier(3, 0) == 1.0)
expected_first = round((eco.MINES_CELLS / (eco.MINES_CELLS - 3)) * 0.97, 2)
check("mines first cell formula", eco._mines_multiplier(3, 1) == expected_first,
      f"{eco._mines_multiplier(3, 1)} vs {expected_first}")
check("mines monotonic", eco._mines_multiplier(3, 1) < eco._mines_multiplier(3, 2) < eco._mines_multiplier(3, 3))
check("mines more mines pays more", eco._mines_multiplier(5, 1) > eco._mines_multiplier(3, 1))
check("mines full board survives", eco._mines_multiplier(3, eco.MINES_CELLS - 3) > 50)

# Честность: средняя выплата по всем путям должна возвращать ~0.97 ставки.
# Для одного шага это просто p * mult == 0.97.
for mines in (1, 3, 5, 10):
    safe = eco.MINES_CELLS - mines
    p_first = safe / eco.MINES_CELLS
    ev = p_first * eco._mines_multiplier(mines, 1)
    check(f"mines ev fair (mines={mines})", abs(ev - 0.97) < 0.01, f"ev={ev:.4f}")

# ── Краш ──────────────────────────────────────────────────────────────────────

check("crash min is 1.0", eco._crash_point(0.0) == 1.0)
check("crash at 0.5", eco._crash_point(0.5) == 1.94)
check("crash capped at 50", eco._crash_point(0.99) == 50.0)
check("crash never negative", all(eco._crash_point(i / 100) >= 1.0 for i in range(100)))
check("crash monotonic", eco._crash_point(0.9) < eco._crash_point(0.95) < eco._crash_point(0.99))

# Честность: p(выше x) * x == 0.97 в среднем — проверяем на сетке.
grid = [eco._crash_point(i / 1000) for i in range(1000)]
check("crash always at least 1.0", min(grid) >= 1.0)
check("crash capped", max(grid) == 50.0)

# ── Рулетка ───────────────────────────────────────────────────────────────────

check("roulette red has 18", len(eco.ROULETTE_RED) == 18)
check("roulette zero color", eco._roulette_color(0) == "зеро")
check("roulette one is red", eco._roulette_color(1) == "красное")
check("roulette two is black", eco._roulette_color(2) == "чёрное")
check("roulette red pays 2", eco._roulette_mult("красное", 1) == 2)
check("roulette zero loses color bet", eco._roulette_mult("красное", 0) == 0)
check("roulette zero pays 30", eco._roulette_mult("зеро", 0) == 30)
check("roulette zero loses on numbers", eco._roulette_mult("зеро", 1) == 0)
check("roulette black pays 2", eco._roulette_mult("чёрное", 2) == 2)
check("roulette wrong color loses", eco._roulette_mult("чёрное", 1) == 0)

# ── Выше-ниже ─────────────────────────────────────────────────────────────────

check("hl no way higher from ace", eco._hl_mult(14, "выше") == 0.0)
check("hl no way lower from two", eco._hl_mult(2, "ниже") == 0.0)
check("hl from two higher pays 1.05", eco._hl_mult(2, "выше") == 1.05)
check("hl rare guess from king pays 12.61", eco._hl_mult(13, "выше") == 12.61)

# Честность: p(угадал) * множитель == 0.97 для каждой карты и направления.
for card in range(2, 15):
    for direction in ("выше", "ниже"):
        chance = eco._hl_chance(card, direction)
        if chance <= 0:
            continue
        ev = chance * eco._hl_mult(card, direction)
        check(f"hl ev fair ({card} {direction})", abs(ev - 0.97) < 0.01, f"ev={ev:.4f}")

# ── Крестики-нолики ───────────────────────────────────────────────────────────

check("ttt row wins", eco._ttt_winner(["X", "X", "X", "", "", "", "", "", ""]) == "X")
check("ttt column wins", eco._ttt_winner(["", "X", "O", "", "X", "O", "", "", "O"]) == "O")
check("ttt diagonal wins", eco._ttt_winner(["X", "", "", "O", "X", "", "O", "", "X"]) == "X")
check("ttt empty no winner", eco._ttt_winner([""] * 9) == "")
draw_board = ["X", "O", "X", "X", "O", "O", "O", "X", "X"]
check("ttt draw board", eco._ttt_full(draw_board) and eco._ttt_winner(draw_board) == "")
check("ttt not full early", not eco._ttt_full(["X"] + [""] * 8))
check("ttt board text", eco._ttt_text(draw_board) == "X O X\nX O O\nO X X")

# ── Регистрация команд и вьюх ─────────────────────────────────────────────────

names = {c.name for c in eco.Economy.__cog_app_commands__}
new_games = {"blackjack", "mines", "crash", "roulette", "highlow", "ttt"}
check("new games registered", new_games <= names, str(sorted(new_games - names)))

for view_name in ("BlackjackView", "MinesView", "CrashView", "HighLowView", "TicTacToeView", "TicTacToeChallengeView"):
    check(f"{view_name} exists", hasattr(eco, view_name))

# Мины: поле обязано влезать в лимит Discord — 25 кнопок.
view = eco.MinesView(1, 2, 10, 3)
check("mines buttons fit limit", len(view.children) == eco.MINES_CELLS + 1 and len(view.children) <= 25,
      f"{len(view.children)} кнопок")
mine_view2 = eco.TicTacToeView(1, 10, 2, 3)
check("ttt nine cells", len(mine_view2.children) == 9)

print()
if failed:
    print(f"FAIL: провалов {failed}")
    sys.exit(1)
print("Все проверки игр прошли.")
