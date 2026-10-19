import random
import re

MAX_DICE = 20
MAX_SIDES = 1000

_NOTATION = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")


def roll(notation: str) -> str:
    """roll dice in standard notation ("d20", "2d6+3", "4d8-1") and describe it.

    returns a plain-text result for the model to narrate, or an error message
    (never raises) when the notation is not understood or too big.
    """
    match = _NOTATION.match((notation or "").lower().replace(" ", ""))
    if not match:
        return "that isn't dice notation, try something like d20 or 2d6+3."
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if not 1 <= count <= MAX_DICE:
        return f"i can roll between 1 and {MAX_DICE} dice at once."
    if not 2 <= sides <= MAX_SIDES:
        return f"dice need between 2 and {MAX_SIDES} sides."
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    detail = ", ".join(str(r) for r in rolls)
    suffix = f" {modifier:+d}" if modifier else ""
    return f"rolled {count}d{sides}{'%+d' % modifier if modifier else ''}: [{detail}]{suffix} = {total}"
