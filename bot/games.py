"""small games, played in matthias's dm ONLY when HE asks for one (noodle never
starts a game on its own, and the tool refuses anyone else / any channel).

the engine owns the rules, the state and noodle's moves (an llm is bad at
those); the model owns the talking. every call returns plain facts to the model
("you placed X at 5, noodle placed O at 1, ongoing") and the board is attached
to the reply automatically by llm.ask_noodle() - drawn here, in a monospace
code block with fixed-width cells, so the alignment is always right and the
model can't mangle it. one game at a time, persisted to data/games.json so a
restart doesn't lose it.
"""
import json
import logging
import random
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


def _attach(text: str, fenced: bool = True) -> None:
    _local.block = f"```\n{text}\n```" if fenced else text


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


def _ttt_render(g: dict) -> str:
    c = [x if x != " " else str(i + 1) for i, x in enumerate(g["board"])]
    rows = [f" {c[r]} | {c[r + 1]} | {c[r + 2]}" for r in (0, 3, 6)]
    return ("\n---+---+---\n").join(rows)


def _ttt_new(args: dict) -> dict:
    return {"game": "tictactoe", "board": [" "] * 9, "difficulty": args.get("difficulty") or "normal"}


def _ttt_move(g: dict, move: str) -> tuple[str, str | None]:
    try:
        i = int(str(move).strip()) - 1
        assert 0 <= i < 9
    except (ValueError, AssertionError):
        return "that isn't a square - he needs to pick a number from 1 to 9.", None
    b = g["board"]
    if b[i] != " ":
        return f"square {i + 1} is already taken - he has to pick a free one.", None
    b[i] = "X"
    if _ttt_winner(b) == "X":
        return f"he placed X at {i + 1} and completed three in a row - he WON.", "win"
    if " " not in b:
        return f"he placed X at {i + 1}. the board is full - it's a DRAW.", "draw"
    j = _ttt_ai_move(b, g["difficulty"])
    b[j] = "O"
    if _ttt_winner(b) == "O":
        return f"he placed X at {i + 1}, then noodle placed O at {j + 1} and completed three in a row - noodle WON.", "loss"
    if " " not in b:
        return f"he placed X at {i + 1}, then noodle placed O at {j + 1}. the board is full - it's a DRAW.", "draw"
    return f"he placed X at {i + 1}, then noodle placed O at {j + 1}. game continues, his turn.", None


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


def _c4_render(g: dict) -> str:
    lines = [" " + " ".join(str(c + 1) for c in range(_C))]
    lines += [" " + " ".join(row) for row in g["board"]]
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
        return f"he dropped X in column {col + 1} and connected four - he WON.", "win"
    if all(b[0][c] != "." for c in range(_C)):
        return f"he dropped X in column {col + 1}. the board is full - it's a DRAW.", "draw"
    j = _c4_ai_move(b, g["difficulty"])
    _c4_drop(b, j, "O")
    if _c4_winner(b) == "O":
        return f"he dropped X in column {col + 1}, then noodle dropped O in column {j + 1} and connected four - noodle WON.", "loss"
    if all(b[0][c] != "." for c in range(_C)):
        return f"he dropped X in column {col + 1}, then noodle dropped O in column {j + 1}. board full - DRAW.", "draw"
    return f"he dropped X in column {col + 1}, then noodle dropped O in column {j + 1}. game continues, his turn.", None


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


def _hang_frame(wrong: int) -> list[str]:
    head = "O" if wrong >= 1 else " "
    body = "|" if wrong >= 2 else " "
    la = "/" if wrong >= 3 else " "
    ra = "\\" if wrong >= 4 else " "
    ll = "/" if wrong >= 5 else " "
    rl = "\\" if wrong >= 6 else " "
    return [
        " +---+ ",
        " |   | ",
        f" |   {head} ",
        f" |  {la}{body}{ra}",
        f" |  {ll} {rl}",
        " |     ",
        "=======",
    ]


def _hang_render(g: dict) -> str:
    wrong = [c for c in g["guessed"] if c not in g["word"]]
    word = " ".join(c if c in g["guessed"] else "_" for c in g["word"])
    lines = _hang_frame(len(wrong))
    lines.append(f"word:  {word}")
    lines.append(f"wrong: {' '.join(wrong) or '-'}  ({len(wrong)}/{_HANG_LIVES})")
    return "\n".join(lines)


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


def _mm_render(g: dict) -> str:
    lines = [" #  guess    exact close"]
    for n, guess in enumerate(g["guesses"], 1):
        e, c = _mm_feedback(guess, g["secret"])
        lines.append(f"{n:>2}  {' '.join(guess):<7}  {e:>5} {c:>5}")
    lines.append(f"colours: {' '.join(_COLORS)}   guesses left: {10 - len(g['guesses'])}")
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
        "new": _ttt_new, "move": _ttt_move, "render": _ttt_render, "fenced": True,
        "rules": "he is X and moves first, noodle is O. he picks a square 1-9. three in a row wins. difficulty: easy / normal / hard (hard never loses).",
    },
    "connect4": {
        "new": _c4_new, "move": _c4_move, "render": _c4_render, "fenced": True,
        "rules": "7 columns x 6 rows, he is X and moves first, noodle is O. he picks a column 1-7 and the piece falls to the bottom. four in a row (any direction) wins. difficulty: easy / normal / hard.",
    },
    "hangman": {
        "new": _hang_new, "move": _hang_move, "render": _hang_render, "fenced": True,
        "rules": "noodle picked a secret word. he guesses one letter at a time (or the whole word, wrong word costs a life). 6 wrong guesses and he's out.",
    },
    "wordle": {
        "new": _wordle_new, "move": _wordle_move, "render": _wordle_render, "fenced": False,
        "rules": "noodle picked a secret 5-letter word. he has 6 guesses (any 5 letters). green = right letter right spot, yellow = in the word but elsewhere, black = not in it.",
    },
    "mastermind": {
        "new": _mm_new, "move": _mm_move, "render": _mm_render, "fenced": True,
        "rules": "noodle hid a code of 4 colours from R G B Y P O (repeats allowed). he guesses 4 letters (e.g. RGBY) and gets 'exact' (right colour right spot) and 'close' (right colour wrong spot). 10 guesses.",
    },
}
ACTIONS = ["list", "rules", "start", "move", "show", "quit"]
ENGINE_NOTE = " the board is attached to your reply automatically - do NOT draw or retype it, just react in your own voice."


def _show(g: dict) -> None:
    spec = GAMES[g["game"]]
    _attach(spec["render"](g), fenced=spec["fenced"])


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
