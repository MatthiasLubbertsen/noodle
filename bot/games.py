"""small games, played in matthias's dm ONLY when HE asks for one (noodle never
starts a game on its own, and the tool refuses anyone else / any channel).

the engine owns the rules, the state and noodle's moves (an llm is bad at
those); the model owns the talking. every call returns plain facts to the model
("you placed X at 5, noodle placed O at 1, ongoing") and the board is attached
to the reply automatically by llm.ask_noodle() - drawn here out of emoji, which
are the same width in every client and font (slack's code blocks are not always
monospace, so ascii grids drifted out of alignment), so the board always lines up
and the model can't mangle it. one game at a time, persisted to data/games.json so a
restart doesn't lose it.
"""
import difflib
import json
import logging
import random
import re
import threading

import config

logger = logging.getLogger("noodle")

# ---------------------------------------------------------------------------
# attachment: the board rides along with the model's reply
# ---------------------------------------------------------------------------
_local = threading.local()


def clear_attachment() -> None:
    _local.block = None


def pop_attachment() -> str | None:
    block = getattr(_local, "block", None)
    _local.block = None
    return block


def _attach(text: str) -> None:
    _local.block = text


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(config.GAMES_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.GAMES_STATE_PATH.write_text(json.dumps(data), encoding="utf-8")


def _record(data: dict, game: str, result: str) -> str:
    """update + describe matthias's win/loss/draw record for this game."""
    rec = data.setdefault("records", {}).setdefault(game, {"win": 0, "loss": 0, "draw": 0})
    rec[result] += 1
    return f"his record in {game} vs noodle: {rec['win']}W {rec['loss']}L {rec['draw']}D"


# ---------------------------------------------------------------------------
# tic-tac-toe (matthias = X, moves first; noodle = O)
# ---------------------------------------------------------------------------
def _keycap(n: int) -> str:
    return f"{n}\ufe0f\u20e3"  # 1️⃣ .. 9️⃣ (a keycap emoji, same width as the other pieces)


_TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def _ttt_winner(b: list[str]) -> str | None:
    for a, c, d in _TTT_LINES:
        if b[a] != " " and b[a] == b[c] == b[d]:
            return b[a]
    return None


def _ttt_minimax(b: list[str], turn: str) -> int:
    """score from O's point of view: +1 O wins, -1 X wins, 0 draw."""
    w = _ttt_winner(b)
    if w:
        return 1 if w == "O" else -1
    free = [i for i, c in enumerate(b) if c == " "]
    if not free:
        return 0
    scores = []
    for i in free:
        b[i] = turn
        scores.append(_ttt_minimax(b, "X" if turn == "O" else "O"))
        b[i] = " "
    return max(scores) if turn == "O" else min(scores)


def _ttt_ai_move(b: list[str], difficulty: str) -> int:
    free = [i for i, c in enumerate(b) if c == " "]
    blunder = {"easy": 1.0, "normal": 0.25, "hard": 0.0}[difficulty]
    if random.random() < blunder:
        return random.choice(free)
    best, best_score = [], -2
    for i in free:
        b[i] = "O"
        score = _ttt_minimax(b, "X")
        b[i] = " "
        if score > best_score:
            best, best_score = [i], score
        elif score == best_score:
            best.append(i)
    return random.choice(best)


_TTT_PIECE = {"X": "\u274c", "O": "\u2b55"}  # ❌ ⭕ ; free squares show their number


def _ttt_render(g: dict) -> str:
    c = [_TTT_PIECE.get(x) or _keycap(i + 1) for i, x in enumerate(g["board"])]
    rows = ["".join(c[r:r + 3]) for r in (0, 3, 6)]
    return "tic-tac-toe\n" + "\n".join(rows)


def _ttt_new(args: dict) -> dict:
    return {"game": "tictactoe", "board": [" "] * 9, "difficulty": args.get("difficulty") or "normal"}


_TTT_NAMES = ["top left", "top middle", "top right", "middle left", "center", "middle right",
              "bottom left", "bottom middle", "bottom right"]


def _ttt_state(b: list[str]) -> str:
    """the board in words, so the model never has to map square numbers to places itself."""
    def squares(piece: str) -> str:
        return ", ".join(f"{i + 1} ({_TTT_NAMES[i]})" for i, c in enumerate(b) if c == piece) or "none"
    return f"board now - X (his): {squares('X')}; O (noodle's): {squares('O')}; free squares: {squares(' ')}."


def _ttt_move(g: dict, move: str) -> tuple[str, str | None]:
    try:
        i = int(str(move).strip()) - 1
        assert 0 <= i < 9
    except (ValueError, AssertionError):
        return "that isn't a square - he needs to pick a number from 1 to 9.", None
    b = g["board"]
    if b[i] != " ":
        return f"square {i + 1} ({_TTT_NAMES[i]}) is already taken - he has to pick a free one. {_ttt_state(b)}", None
    b[i] = "X"
    his = f"he placed X at square {i + 1} ({_TTT_NAMES[i]})"
    if _ttt_winner(b) == "X":
        return f"{his} and completed three in a row - he WON. {_ttt_state(b)}", "win"
    if " " not in b:
        return f"{his}. the board is full - it's a DRAW. {_ttt_state(b)}", "draw"
    j = _ttt_ai_move(b, g["difficulty"])
    b[j] = "O"
    mine = f"then noodle placed O at square {j + 1} ({_TTT_NAMES[j]})"
    if _ttt_winner(b) == "O":
        return f"{his}, {mine} and completed three in a row - noodle WON. {_ttt_state(b)}", "loss"
    if " " not in b:
        return f"{his}, {mine}. the board is full - it's a DRAW. {_ttt_state(b)}", "draw"
    return f"{his}, {mine}. game continues, his turn. {_ttt_state(b)}", None


# ---------------------------------------------------------------------------
# connect four (matthias = X, moves first; noodle = O). 6 rows x 7 columns.
# ---------------------------------------------------------------------------
_R, _C = 6, 7


def _c4_drop(b: list[list[str]], col: int, piece: str) -> int | None:
    for r in range(_R - 1, -1, -1):
        if b[r][col] == ".":
            b[r][col] = piece
            return r
    return None


def _c4_windows():
    for r in range(_R):
        for c in range(_C):
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = [(r + dr * k, c + dc * k) for k in range(4)]
                if all(0 <= rr < _R and 0 <= cc < _C for rr, cc in cells):
                    yield cells


_C4_WINDOWS = list(_c4_windows())


def _c4_winner(b: list[list[str]]) -> str | None:
    for cells in _C4_WINDOWS:
        vals = {b[r][c] for r, c in cells}
        if len(vals) == 1 and "." not in vals:
            return vals.pop()
    return None


def _c4_eval(b: list[list[str]]) -> int:
    """heuristic from O's point of view."""
    score = sum(3 for r in range(_R) if b[r][_C // 2] == "O") - sum(3 for r in range(_R) if b[r][_C // 2] == "X")
    for cells in _C4_WINDOWS:
        vals = [b[r][c] for r, c in cells]
        o, x = vals.count("O"), vals.count("X")
        if o and x:
            continue
        if o:
            score += {1: 1, 2: 4, 3: 20, 4: 10000}[o]
        elif x:
            score -= {1: 1, 2: 4, 3: 20, 4: 10000}[x]
    return score


def _c4_search(b, depth: int, alpha: int, beta: int, turn: str) -> int:
    w = _c4_winner(b)
    if w:
        return 100000 + depth if w == "O" else -100000 - depth
    cols = [c for c in (3, 2, 4, 1, 5, 0, 6) if b[0][c] == "."]
    if not cols:
        return 0
    if depth == 0:
        return _c4_eval(b)
    if turn == "O":
        best = -10**9
        for c in cols:
            r = _c4_drop(b, c, "O")
            best = max(best, _c4_search(b, depth - 1, alpha, beta, "X"))
            b[r][c] = "."
            alpha = max(alpha, best)
            if alpha >= beta:
                break
        return best
    best = 10**9
    for c in cols:
        r = _c4_drop(b, c, "X")
        best = min(best, _c4_search(b, depth - 1, alpha, beta, "O"))
        b[r][c] = "."
        beta = min(beta, best)
        if alpha >= beta:
            break
    return best


def _c4_ai_move(b: list[list[str]], difficulty: str) -> int:
    cols = [c for c in range(_C) if b[0][c] == "."]
    depth = {"easy": 1, "normal": 3, "hard": 5}[difficulty]
    if difficulty == "easy" and random.random() < 0.4:
        return random.choice(cols)
    scored = []
    for c in cols:
        r = _c4_drop(b, c, "O")
        scored.append((_c4_search(b, depth - 1, -10**9, 10**9, "X"), c))
        b[r][c] = "."
    top = max(s for s, _ in scored)
    return random.choice([c for s, c in scored if s == top])


_C4_PIECE = {"X": "\U0001f534", "O": "\U0001f7e1", ".": "\u26aa"}  # 🔴 him, 🟡 noodle, ⚪ empty


def _c4_render(g: dict) -> str:
    lines = ["connect four", "".join(_keycap(c + 1) for c in range(_C))]
    lines += ["".join(_C4_PIECE[x] for x in row) for row in g["board"]]
    return "\n".join(lines)


def _c4_new(args: dict) -> dict:
    return {
        "game": "connect4",
        "board": [["."] * _C for _ in range(_R)],
        "difficulty": args.get("difficulty") or "normal",
    }


def _c4_move(g: dict, move: str) -> tuple[str, str | None]:
    try:
        col = int(str(move).strip()) - 1
        assert 0 <= col < _C
    except (ValueError, AssertionError):
        return "that isn't a column - he needs to pick a number from 1 to 7.", None
    b = g["board"]
    if _c4_drop(b, col, "X") is None:
        return f"column {col + 1} is full - he has to pick another one.", None
    if _c4_winner(b) == "X":
        return f"he dropped his red piece in column {col + 1} and connected four - he WON.", "win"
    if all(b[0][c] != "." for c in range(_C)):
        return f"he dropped his red piece in column {col + 1}. the board is full - it's a DRAW.", "draw"
    j = _c4_ai_move(b, g["difficulty"])
    _c4_drop(b, j, "O")
    if _c4_winner(b) == "O":
        return f"he dropped his red piece in column {col + 1}, then noodle dropped its yellow piece in column {j + 1} and connected four - noodle WON.", "loss"
    if all(b[0][c] != "." for c in range(_C)):
        return f"he dropped his red piece in column {col + 1}, then noodle dropped its yellow piece in column {j + 1}. board full - DRAW.", "draw"
    return f"he dropped his red piece in column {col + 1}, then noodle dropped its yellow piece in column {j + 1}. game continues, his turn.", None


# ---------------------------------------------------------------------------
# hangman (noodle picks the word; 6 wrong guesses and he's out)
# ---------------------------------------------------------------------------
_WORDS = """
abandon academy balloon bicycle blanket bubble cactus campfire candle canyon carousel castle
chimney circus compass cookie crystal curtain dolphin dragon eclipse elephant engine feather
fiddle forest fossil galaxy garden giraffe glacier goblin guitar hammock harbor helmet island
jacket jigsaw jungle kitchen ladder lantern lizard magnet marble meadow mirror monster mountain
muffin mystery noodle nugget octopus orchard oyster paddle parrot pebble penguin pickle pillow
pirate planet pocket puzzle rabbit rainbow rocket saddle sailor sandwich scooter shadow signal
skeleton snorkel spider squirrel statue sunrise swallow teapot thunder ticket tornado trumpet
tunnel turtle umbrella unicorn vampire velvet volcano walrus whistle window wizard yogurt zeppelin
""".split()
_HANG_LIVES = 6


def _hang_render(g: dict) -> str:
    wrong = [c for c in g["guessed"] if c not in g["word"]]
    word = " ".join(c if c in g["guessed"] else "_" for c in g["word"])
    lives = "\u2764\ufe0f" * (_HANG_LIVES - len(wrong)) + "\U0001f5a4" * len(wrong)  # ❤️ left, 🖤 lost
    return (
        f"hangman  {lives}\n"
        f"`{word}`\n"  # inline code so slack never treats the underscores as italics
        f"wrong: {' '.join(wrong) or '-'}  ({len(wrong)}/{_HANG_LIVES})"
    )


def _hang_new(args: dict) -> dict:
    return {"game": "hangman", "word": random.choice(_WORDS), "guessed": []}


def _hang_move(g: dict, move: str) -> tuple[str, str | None]:
    guess = str(move).strip().lower()
    if not guess.isalpha():
        return "he needs to guess a letter (or the whole word).", None
    word = g["word"]
    if len(guess) > 1:  # whole-word guess
        if guess == word:
            g["guessed"] = sorted(set(word) | set(g["guessed"]))
            return f"he guessed the whole word and it was '{word}' - he WON.", "win"
        g["guessed"].append("?")  # a wrong word costs a life
        wrong = len([c for c in g["guessed"] if c not in word])
        if wrong >= _HANG_LIVES:
            return f"he guessed '{guess}' - wrong, and that was his last life. the word was '{word}' - he LOST.", "loss"
        return f"he guessed the word '{guess}' - wrong, that costs a life.", None
    if guess in g["guessed"]:
        return f"he already guessed '{guess}' - no penalty, he can pick another.", None
    g["guessed"].append(guess)
    wrong = len([c for c in g["guessed"] if c not in word])
    if guess in word:
        if all(c in g["guessed"] for c in word):
            return f"'{guess}' is in the word ({word.count(guess)}x) and that completes it: '{word}' - he WON.", "win"
        return f"'{guess}' is in the word ({word.count(guess)}x).", None
    if wrong >= _HANG_LIVES:
        return f"'{guess}' is not in the word, that was his last life. the word was '{word}' - he LOST.", "loss"
    left = _HANG_LIVES - wrong
    return f"'{guess}' is not in the word. {left} {'life' if left == 1 else 'lives'} left.", None


# ---------------------------------------------------------------------------
# wordle-style (noodle picks a 5-letter word; 6 guesses)
# ---------------------------------------------------------------------------
_WORDLE = """
about above actor adult after again alarm alien alley amber angle apple arrow beach beard berry
blank blaze bloom board brave bread brick bride brush cabin candy chair chalk charm chess chief
cliff clock cloud coast couch crane crown dance delta diary dizzy dream dress eagle earth fairy
feast fever field flame flint float flute frost fruit ghost giant glass globe grace grape grass
happy heart honey horse house hotel ivory jelly joker juice knife lemon light lunar magic maple
melon mouse music night noble ocean olive onion paint panda pearl piano pilot pizza plant pride
queen quiet radio raven river robot salad scarf shark sheep shine skate sleep smile snake solar
space spoon stair storm sugar tiger toast torch tower trail train ultra vivid water whale wheat
world youth zebra
""".split()


def _wordle_score(guess: str, answer: str) -> str:
    """🟩 right spot, 🟨 in the word elsewhere, ⬛ not in the word (duplicates handled)."""
    res = ["⬛"] * 5
    left = list(answer)
    for i in range(5):
        if guess[i] == answer[i]:
            res[i] = "🟩"
            left[i] = None
    for i in range(5):
        if res[i] == "⬛" and guess[i] in left:
            res[i] = "🟨"
            left[left.index(guess[i])] = None
    return "".join(res)


def _wordle_render(g: dict) -> str:
    rows = [f"{_wordle_score(w, g['word'])}  {w.upper()}" for w in g["guesses"]]
    rows += ["⬜⬜⬜⬜⬜" for _ in range(6 - len(rows))]
    return "\n".join(rows)


def _wordle_new(args: dict) -> dict:
    return {"game": "wordle", "word": random.choice(_WORDLE), "guesses": []}


def _wordle_move(g: dict, move: str) -> tuple[str, str | None]:
    guess = str(move).strip().lower()
    if len(guess) != 5 or not guess.isalpha():
        return "a guess has to be exactly 5 letters.", None
    g["guesses"].append(guess)
    if guess == g["word"]:
        return f"he guessed '{guess}' - that's the word, he WON in {len(g['guesses'])}/6.", "win"
    if len(g["guesses"]) >= 6:
        return f"he guessed '{guess}' - wrong, and that was his 6th guess. the word was '{g['word']}' - he LOST.", "loss"
    return f"he guessed '{guess}'. {6 - len(g['guesses'])} guesses left.", None


# ---------------------------------------------------------------------------
# mastermind (noodle hides 4 colours from R G B Y P O, repeats allowed; 10 guesses)
# ---------------------------------------------------------------------------
_COLORS = "RGBYPO"


def _mm_feedback(guess: str, secret: str) -> tuple[int, int]:
    exact = sum(a == b for a, b in zip(guess, secret))
    common = sum(min(guess.count(c), secret.count(c)) for c in _COLORS)
    return exact, common - exact


_MM_PEG = {"R": "\U0001f534", "G": "\U0001f7e2", "B": "\U0001f535", "Y": "\U0001f7e1", "P": "\U0001f7e3", "O": "\U0001f7e0"}


def _mm_render(g: dict) -> str:
    lines = ["mastermind"]
    for guess in g["guesses"]:
        e, c = _mm_feedback(guess, g["secret"])
        # always 4 feedback slots (\u26ab right spot, \u26aa right colour wrong spot, \u25ab\ufe0f nothing) so rows line up
        slots = "\u26ab" * e + "\u26aa" * c + "\u25ab\ufe0f" * (4 - e - c)
        lines.append("".join(_MM_PEG[ch] for ch in guess) + "  \u279c  " + slots)
    lines.append(f"{' '.join(ch + _MM_PEG[ch] for ch in _COLORS)}  |  \u26ab right spot, \u26aa right colour wrong spot")
    lines.append(f"{10 - len(g['guesses'])} guesses left")
    return "\n".join(lines)


def _mm_new(args: dict) -> dict:
    return {"game": "mastermind", "secret": "".join(random.choice(_COLORS) for _ in range(4)), "guesses": []}


def _mm_move(g: dict, move: str) -> tuple[str, str | None]:
    guess = "".join(str(move).upper().split())
    if len(guess) != 4 or any(c not in _COLORS for c in guess):
        return f"a guess is 4 letters from {' '.join(_COLORS)} (e.g. RGBY).", None
    g["guesses"].append(guess)
    e, c = _mm_feedback(guess, g["secret"])
    if e == 4:
        return f"he guessed {guess} - cracked the code in {len(g['guesses'])}/10, he WON.", "win"
    if len(g["guesses"]) >= 10:
        return f"he guessed {guess} ({e} exact, {c} close) - that was guess 10. the code was {g['secret']} - he LOST.", "loss"
    return f"he guessed {guess}: {e} in the right spot, {c} right colour but wrong spot.", None


# ---------------------------------------------------------------------------
# registry + the tool entrypoint
# ---------------------------------------------------------------------------
GAMES = {
    "tictactoe": {
        "new": _ttt_new, "move": _ttt_move, "render": _ttt_render,
        "rules": "he is ❌ (X) and moves first, noodle is ⭕ (O). he picks a free square by its number 1-9. three in a row wins. difficulty: easy / normal / hard (hard never loses).",
    },
    "connect4": {
        "new": _c4_new, "move": _c4_move, "render": _c4_render,
        "rules": "7 columns x 6 rows, he is red 🔴 and moves first, noodle is yellow 🟡. he picks a column 1-7 and the piece falls to the bottom. four in a row (any direction) wins. difficulty: easy / normal / hard.",
    },
    "hangman": {
        "new": _hang_new, "move": _hang_move, "render": _hang_render,
        "rules": "noodle picked a secret word. he guesses one letter at a time (or the whole word, wrong word costs a life). 6 wrong guesses and he's out.",
    },
    "wordle": {
        "new": _wordle_new, "move": _wordle_move, "render": _wordle_render,
        "rules": "noodle picked a secret 5-letter word. he has 6 guesses (any 5 letters). green = right letter right spot, yellow = in the word but elsewhere, black = not in it.",
    },
    "mastermind": {
        "new": _mm_new, "move": _mm_move, "render": _mm_render,
        "rules": "noodle hid a code of 4 colours from R G B Y P O (repeats allowed). he guesses 4 letters (e.g. RGBY) and gets 'exact' (right colour right spot) and 'close' (right colour wrong spot). 10 guesses.",
    },
}
ACTIONS = ["list", "rules", "start", "move", "show", "quit"]
ENGINE_NOTE = " the board is attached to your reply automatically - do NOT draw or retype it, just react in your own voice."


def _show(g: dict) -> None:
    _attach(GAMES[g["game"]]["render"](g))


def play(args: dict, user_id: str | None, origin: dict | None) -> str:
    """the play_game tool. matthias-only, dm-only, checked here."""
    if user_id != config.USER_ID:
        return "only matthias can play games with noodle."
    if not (origin and origin.get("is_dm")):
        return "games are only played in dms, not here."
    action = args.get("action", "")
    data = _load()
    current = data.get("current")

    if action == "list":
        return "games: " + ", ".join(GAMES) + ". " + " | ".join(f"{k}: {v['rules']}" for k, v in GAMES.items())
    if action == "rules":
        name = args.get("game") or (current or {}).get("game")
        return GAMES[name]["rules"] if name in GAMES else "which game? one of: " + ", ".join(GAMES)
    if action == "start":
        name = args.get("game")
        if name not in GAMES:
            return "which game? one of: " + ", ".join(GAMES)
        if (args.get("difficulty") or "normal") not in ("easy", "normal", "hard"):
            return "difficulty must be easy, normal or hard."
        data["current"] = GAMES[name]["new"](args)
        data["last_game"] = name
        _save(data)
        _show(data["current"])
        replaced = f" (this replaced his unfinished {current['game']} game)" if current else ""
        return f"started {name}{replaced}. rules: {GAMES[name]['rules']}.{ENGINE_NOTE}"
    if action in ("move", "show", "quit") and not current:
        return "no game is running right now - he has to ask for one."
    if action == "show":
        _show(current)
        return "here's the current game." + ENGINE_NOTE
    if action == "quit":
        data["current"] = None
        _save(data)
        return f"he quit the {current['game']} game, nothing is running now."
    if action == "move":
        msg, result = GAMES[current["game"]]["move"](current, args.get("move", ""))
        if result:
            msg += " " + _record(data, current["game"], result) + "."
            _show(current)
            data["current"] = None
        else:
            data["current"] = current
            _show(current)
        _save(data)
        return msg + ENGINE_NOTE + (" the game is over." if result else "")
    return f"unknown game action: {action}"


# ---------------------------------------------------------------------------
# the engine drives the game, the model only narrates
# ---------------------------------------------------------------------------
# a cheap model doesn't reliably call play_game: it would say "you took the middle
# right, i went center" without calling it, and nothing changed. so before the model
# even runs, a clear start request or a clear move is applied here, and the model just
# gets the result to react to. anything that isn't clearly one of those still goes
# through the model + tool as before.
_NAME_PATTERNS = {
    "tictactoe": r"tic[\s-]*tac[\s-]*toe|\bttt\b|noughts?\s*(?:and|&)\s*crosses|\bx\s*(?:and|&|n)\s*o\b",
    "connect4": r"connect[\s-]*(?:4|four)|(?:4|four)\s*in\s*a\s*row",
    "hangman": r"hang[\s-]*m[ae]n",
    "wordle": r"\bword(?:le|el|l)\b",
    "mastermind": r"master[\s-]*mind|code[\s-]*breaker",
}
# typos in the long names ("tick tack to", "tik tak toe", "conect four"): compare every run of
# 1-3 words, squashed together, with the name. short names (wordle, hangman) stay regex-only,
# because fuzzy matching would read "world" as "wordle".
_FUZZY_NAMES = (("tictactoe", "tictactoe", 0.75), ("mastermind", "mastermind", 0.8), ("connect4", "connectfour", 0.8))


def find_game(text: str) -> str | None:
    """which game a message is talking about, tolerant of typos."""
    for game, pattern in _NAME_PATTERNS.items():
        if re.search(pattern, text, re.I):
            return game
    words = re.findall(r"[a-z0-9]+", text.lower())
    windows = {"".join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words) - n + 1)}
    windows = {w for w in windows if 7 <= len(w) <= 14}
    for game, target, threshold in _FUZZY_NAMES:
        if any(difflib.SequenceMatcher(None, w, target).ratio() >= threshold for w in windows):
            return game
    return None


_WANTS_TO_PLAY = re.compile(r"\b(play|start|begin|another|again|rematch|new game|let'?s do|wanna|want to|can we|could we|shall we|up for)\b", re.I)
_REFUSAL = re.compile(r"\b(don'?t|dont|do not|not|never|no|stop|quit|cancel|rather not|hate|why)\b", re.I)
_PLAY_WORD = re.compile(r"\b(play|start|begin|rematch|new game|another one|one more)\b", re.I)
_BARE_AGAIN = re.compile(r"\W*(again|rematch|one more|another one)\W*", re.I)
_AGAIN = re.compile(r"\b(again|rematch|another one|one more|new game)\b", re.I)
_MOVE_FILLER = {
    "lets", "let's", "let", "us", "do", "go", "play", "ill", "i'll", "i", "will", "take", "pick", "choose",
    "square", "column", "col", "number", "no", "in", "at", "on", "the", "my", "move", "is", "then", "please",
    "pls", "ok", "okay", "alright", "so", "and", "it", "that", "here", "uh", "um", "hmm", "its", "it's", "put", "drop", "place",
}
# a number right next to one of these is chat, not a move ("see you at 9", "i have 2")
_NOT_A_MOVE_BEFORE = {"at", "around", "by", "until", "till", "about", "past", "after", "before", "than",
                      "have", "has", "got", "im", "i'm", "am", "was", "were", "are", "only", "just"}
AUTO_NOTE = (
    "game engine update - the engine already handled this and the result below is the truth, so "
    "never contradict it (a move it says was placed WAS placed). you have no game tool this turn, "
    "don't try to make or repeat a move. react in your own voice; the board is attached "
    "automatically. engine result: "
)


def _with_his_words(note: str, text: str) -> str:
    return (
        f'{note}\nhis full message was: "{text}". if it says anything besides the move (a joke, a '
        "comment, a feeling), answer THAT like a person first, then react to the game. describe "
        "squares with the names the engine used, never by guessing which number is where."
    )


def _auto_move(game: str, text: str) -> str | None:
    """the move in a short message, if it clearly is one for this game."""
    t = text.strip()
    if game in ("tictactoe", "connect4"):
        top = 9 if game == "tictactoe" else 7
        # a clear move is either essentially just the move ("6", "let's do 6", "square 6 please"),
        # or a chatty message that starts or ends with it ("this will end in a draw lol. 7").
        # a number in the middle of a sentence ("i'm 3 days late") or after a word like "at"
        # ("see you at 9") is not a move.
        tokens = re.findall(r"[a-z0-9']+", t.lower())
        idx = [i for i, w in enumerate(tokens) if re.fullmatch(r"[1-9]", w)]
        if len(idx) == 1 and int(tokens[idx[0]]) <= top:
            i = idx[0]
            rest = tokens[:i] + tokens[i + 1:]
            if all(w in _MOVE_FILLER for w in rest):
                return tokens[i]
            near = tokens[i - 1] if i == len(tokens) - 1 else tokens[i + 1] if i == 0 else None
            if len(t) <= 80 and (i == 0 or i == len(tokens) - 1) and near not in _NOT_A_MOVE_BEFORE:
                return tokens[i]
    elif game == "hangman":
        m = re.fullmatch(r"(?:(?:letter|guess)\s+)?([a-z])", t, re.I) or re.fullmatch(r"(?:guess\s+)([a-z]{2,})", t, re.I)
        if m:
            return m.group(1)
    elif game == "wordle":
        m = re.fullmatch(r"(?:guess\s+)?([a-z]{5})", t, re.I)
        if m:
            return m.group(1)
    elif game == "mastermind":
        code = re.sub(r"[\s,.-]+", "", t)
        if re.fullmatch(r"[rgbypo]{4}", code, re.I):
            return code
    return None


def auto(text: str, user_id: str | None, origin: dict | None) -> str | None:
    """apply a clear game start / move from matthias's dm message right away.
    returns a note for the model, or None if the message isn't clearly either."""
    if user_id != config.USER_ID or not (origin and origin.get("is_dm")):
        return None
    text = (text or "").strip()
    if not text:
        return None
    data = _load()
    current = data.get("current")

    if current:
        move = _auto_move(current["game"], text)
        if move is not None:
            return _with_his_words(AUTO_NOTE + play({"action": "move", "move": move}, user_id, origin), text)
    # start: needs a game name (or "again" after a game) AND a clear wish to play, no refusal
    if len(text) > 120 or _REFUSAL.search(text) or not _WANTS_TO_PLAY.search(text):
        return None
    name = find_game(text)
    if name is None and _AGAIN.search(text):
        # "try again, i forgot to build" is not "play again": with no game named, "again"
        # only counts with a real play word, or as the whole message ("again!")
        if not _PLAY_WORD.search(text) and not _BARE_AGAIN.fullmatch(text):
            return None
        name = data.get("last_game")
    if name not in GAMES:
        return None
    difficulty = (re.search(r"\b(easy|normal|hard)\b", text, re.I) or [None, None])[1]
    args = {"action": "start", "game": name}
    if difficulty:
        args["difficulty"] = difficulty.lower()
    return _with_his_words(AUTO_NOTE + "he asked to play, so the engine started it. " + play(args, user_id, origin), text)


# ---------------------------------------------------------------------------
# safety net: the model describing a game turn that never happened
# ---------------------------------------------------------------------------
_FAKE_TURN = re.compile(
    r"you'?re \u274c|you are \u274c|i'?m \u2b55|pick (?:a |another )?(?:free )?(?:square|column)|"
    r"your (?:move|turn)\b|you (?:start|go) first|guess a letter|make your guess|your guess\b",
    re.I,
)
_GAMEY = re.compile(r"play|game|move|turn|square|column|guess|tic|tac|toe|connect|hang|wordle|mastermind", re.I)
FAKE_TURN_NOTE = (
    "your last draft described a game turn (a board, a move, whose turn it is) but no game tool "
    "ran, so nothing actually happened. call play_game now: action=start with the game he asked "
    "for, or action=move with his move, or action=list if he didn't actually ask about a game. "
    "write your reply only after the tool has run."
)


def looks_like_fake_turn(reply: str, user_text: str, user_id: str | None, origin: dict | None) -> bool:
    """true if, in matthias's dm, the reply narrates a game turn but no game tool put a
    board on this turn - a cheap model sometimes just says it started / moved."""
    if user_id != config.USER_ID or not (origin and origin.get("is_dm")):
        return False
    if getattr(_local, "block", None):  # a board was attached this turn: the engine did run
        return False
    if _load().get("current"):
        # a game is running: "your move" in a reply to plain chat is fine. only suspicious if he
        # sent something that looks like a move (a number) and nothing was applied.
        plausible = bool(re.search(r"\b[1-9]\b", user_text or ""))
    else:
        plausible = bool(_GAMEY.search(user_text or ""))
    return plausible and bool(_FAKE_TURN.search(reply or ""))
