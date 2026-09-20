"""repair services — required facts, labels, and the pipeline stages."""

from __future__ import annotations

import re
from typing import Any

#: Fields required before the project can advance.
REQUIRED_FACTS: tuple[str, ...] = ("room_type", "area", "budget")

#: Human-readable project field names (for the «Уточните…» questions).
FACT_LABELS: dict[str, str] = {
    "room_type": "тип помещения",
    "area": "площадь",
    "budget": "бюджет",
    "ceiling_height": "высоту потолков",
    "length": "длину",
    "width": "ширину",
    "style": "стиль",
    "wall_color": "цвет стен",
    "ceiling_color": "цвет потолка",
    "floor_material": "пол",
}

#: Pipeline stages in order.
STAGES: tuple[str, ...] = (
    "collect",
    "design_choice",
    "plan",
    "estimate",
    "final_approval",
    "assistant",
)

# --- deterministic fact extraction (offline / gap-fill) --------------------- #
#
# The LLM extractor is the primary path, but it must not be the *only* one: with
# no provider configured the assistant still has to collect facts, and even with
# a model a value stated plainly in the text ("10 метров") sometimes comes back
# null. These regexes are a conservative, Russian-only net.

_AREA_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м|квадратн\w*\s*метр\w*|метр\w*|\bм\b)",
    re.IGNORECASE,
)
_CEIL_RE = re.compile(r"потол\w*[^\d]{0,12}(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_BUDGET_RE = re.compile(
    r"(\d[\d\s]*(?:[.,]\d+)?)\s*"
    r"(тыс\w*|тысяч\w*|к\b|млн\w*|миллион\w*|руб\w*|₽|р\b)",
    re.IGNORECASE,
)
_BUDGET_NEAR_RE = re.compile(
    r"(?:бюджет\w*|потратить|уложиться|рассчитыва\w*)\D{0,12}(\d[\d\s]*(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_ROOM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("детск", "детская"),
    ("ванн", "ванная"),
    ("санузел", "санузел"),
    ("спальн", "спальня"),
    ("кухн", "кухня"),
    ("гостин", "гостиная"),
    ("прихож", "прихожая"),
    ("коридор", "прихожая"),
    ("офис", "офис"),
)
_STYLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("космич", "космический"),
    ("косм", "космический"),
    ("футур", "футуристический"),
    ("техно", "футуристический"),
    ("морск", "морской"),
    ("океан", "морской"),
    ("скандинав", "скандинавский"),
    ("сканди", "скандинавский"),
    ("лофт", "лофт"),
    ("минимал", "минимализм"),
    ("прованс", "прованс"),
    ("неоклассик", "неоклассика"),
    ("классик", "классический"),
    ("современ", "современный"),
)


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _near(text: str, position: int, word: str, window: int = 16) -> bool:
    return word in text[max(0, position - window) : position].lower()


def parse_facts(text: str) -> dict[str, Any]:
    """A conservative, deterministic `ProjectInfo`-shaped dict from Russian text.

    Fills what it can and omits the rest (the caller merges). Handles the usual
    phrasings: «10 метров»/«10 м»/«10 кв.м»/«10 м²» → `area`,
    «300 тысяч»/«50к»/«150000 рублей» → `budget` (in rubles),
    «потолок 2.7» → `ceiling_height`, plus `room_type`/`style` keywords. A
    «потолок 2.7 м» is not mistaken for the area.
    """
    facts: dict[str, Any] = {}
    lower = text.lower()

    for stem, value in _ROOM_KEYWORDS:
        if stem in lower:
            facts["room_type"] = value
            break
    for stem, value in _STYLE_KEYWORDS:
        if stem in lower:
            facts["style"] = value
            break

    ceiling_match = _CEIL_RE.search(text)
    if ceiling_match:
        ceiling = _to_float(ceiling_match.group(1))
        if ceiling is not None and 0 < ceiling <= 6:
            facts["ceiling_height"] = ceiling

    for match in _AREA_RE.finditer(text):
        if _near(text, match.start(), "потол"):
            continue  # e.g. «потолок 2.7 м» — that is the height, not the area
        area = _to_float(match.group(1))
        if area is not None and area > 0:
            facts["area"] = area
            break

    for match in _BUDGET_RE.finditer(text):
        amount = _to_float(match.group(1))
        if amount is None:
            continue
        unit = match.group(2).lower()
        if unit.startswith("тыс") or unit == "к":
            amount *= 1_000
        elif unit.startswith("млн") or unit.startswith("миллион"):
            amount *= 1_000_000
        facts["budget"] = amount
        break
    if "budget" not in facts:
        near = _BUDGET_NEAR_RE.search(text)
        if near:
            parsed = _to_float(near.group(1))
            if parsed is not None:
                facts["budget"] = parsed

    return facts


__all__ = ["FACT_LABELS", "REQUIRED_FACTS", "STAGES", "parse_facts"]
