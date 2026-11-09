"""Deterministic pipeline fallbacks (no LLM, §67).

A separate module so that `steps.py` stays about stages while the fallback
design options and plan live here. Used only when the model is not
configured (demo mode); when a model is configured, failures are not masked.
"""

from __future__ import annotations

import math

from .models import DesignOption, PlanStep, ProjectInfo

#: Themed fallback design sets. Selected by style keywords;
#: the palette carries `style` — the `image_prompt` render will honor it.
_FALLBACK_THEMES: list[
    tuple[tuple[str, ...], list[tuple[str, dict[str, str], str]]]
] = [
    (
        ("косм", "футур", "звёзд", "галактик", "техно", "sci-fi"),
        [
            (
                "Космическая станция",
                {
                    "style": "космический",
                    "wall_color": "тёмно-синий",
                    "ceiling_color": "белый",
                    "floor_material": "синий ковролин",
                },
                "Тёмно-синие стены, белый потолок с точечными светильниками, синий ковролин — как на космической станции.",
            ),
            (
                "Млечный путь",
                {
                    "style": "космический",
                    "wall_color": "графит",
                    "ceiling_color": "белый",
                    "floor_material": "серый ламинат",
                },
                "Графитовые стены с акцентной «звёздной» стеной и светлый пол.",
            ),
            (
                "Глубина космоса",
                {
                    "style": "космический",
                    "wall_color": "синий",
                    "ceiling_color": "тёмно-синий",
                    "floor_material": "синий ковролин",
                },
                "Насыщенно-синий интерьер, потолок с имитацией звёздного неба.",
            ),
        ],
    ),
    (
        ("морск", "океан", "волн", "причал", "якор"),
        [
            (
                "Морское приключение",
                {
                    "style": "морской",
                    "wall_color": "голубой",
                    "ceiling_color": "белый",
                    "floor_material": "светлый дуб",
                },
                "Голубые стены, белый потолок, пол из светлого дуба — морская свежесть.",
            ),
            (
                "Океанский бриз",
                {
                    "style": "морской",
                    "wall_color": "бирюзовый",
                    "ceiling_color": "белый",
                    "floor_material": "песочный ламинат",
                },
                "Бирюзовые стены и тёплый песочный пол.",
            ),
            (
                "Морская волна",
                {
                    "style": "морской",
                    "wall_color": "бирюзово-зелёный",
                    "ceiling_color": "белый",
                    "floor_material": "голубой ковролин",
                },
                "Морская гамма с акцентом-волной на стене.",
            ),
        ],
    ),
    (
        ("скандинав", "сканди", "минимализм", "минималистич"),
        [
            (
                "Сканди-свет",
                {
                    "style": "современный скандинавский",
                    "wall_color": "белый",
                    "ceiling_color": "белый",
                    "floor_material": "светлый ламинат",
                },
                "Светлый скандинавский минимализм.",
            ),
            (
                "Тёплый сканди",
                {
                    "style": "современный скандинавский",
                    "wall_color": "светло-бежевый",
                    "ceiling_color": "белый",
                    "floor_material": "тёплый ламинат",
                },
                "Скандинавская база с тёплыми акцентами.",
            ),
            (
                "Сканди-уют",
                {
                    "style": "современный скандинавский",
                    "wall_color": "молочный",
                    "ceiling_color": "белый",
                    "floor_material": "паркет",
                },
                "Нейтральный уютный скандинавский интерьер.",
            ),
        ],
    ),
    (
        ("лофт", "индустриал", "кирпич"),
        [
            (
                "Лофт-кирпич",
                {
                    "style": "лофт",
                    "wall_color": "терракотовый",
                    "ceiling_color": "белый",
                    "floor_material": "бетонный пол",
                },
                "Лофт с кирпичной стеной.",
            ),
            (
                "Тёмный лофт",
                {
                    "style": "лофт",
                    "wall_color": "серый",
                    "ceiling_color": "белый",
                    "floor_material": "тёмный ламинат",
                },
                "Индустриальная серость.",
            ),
            (
                "Лофт-микс",
                {
                    "style": "лофт",
                    "wall_color": "графит",
                    "ceiling_color": "серый",
                    "floor_material": "бетон",
                },
                "Строгий лофт.",
            ),
        ],
    ),
    (
        ("классик", "неоклассик", "прованс", "кантри", "барокко"),
        [
            (
                "Классика",
                {
                    "style": "классический",
                    "wall_color": "кремовый",
                    "ceiling_color": "белый",
                    "floor_material": "паркет",
                },
                "Классическая светлая палитра.",
            ),
            (
                "Неоклассика",
                {
                    "style": "неоклассика",
                    "wall_color": "светло-бежевый",
                    "ceiling_color": "белый",
                    "floor_material": "паркет",
                },
                "Элегантная неоклассика.",
            ),
            (
                "Прованс",
                {
                    "style": "прованс",
                    "wall_color": "лавандовый",
                    "ceiling_color": "белый",
                    "floor_material": "светлый ламинат",
                },
                "Нежный прованс.",
            ),
        ],
    ),
]

#: Default — kids room without a style: bright, cheerful, themed.
_FALLBACK_KIDS: list[tuple[str, dict[str, str], str]] = [
    (
        "Морская детская",
        {
            "style": "морской",
            "wall_color": "голубой",
            "ceiling_color": "белый",
            "floor_material": "светлый ламинат",
        },
        "Светло-голубая детская в морской теме.",
    ),
    (
        "Солнечная детская",
        {
            "style": "современный",
            "wall_color": "жёлтый",
            "ceiling_color": "белый",
            "floor_material": "тёплый ламинат",
        },
        "Яркая солнечная детская.",
    ),
    (
        "Уютная детская",
        {
            "style": "современный",
            "wall_color": "зелёный",
            "ceiling_color": "белый",
            "floor_material": "ковролин",
        },
        "Спокойная зелёная детская.",
    ),
]

_FALLBACK_DEFAULT: list[tuple[str, dict[str, str], str]] = [
    (
        "Светлый",
        {
            "style": "современный скандинавский",
            "wall_color": "белый",
            "ceiling_color": "белый",
            "floor_material": "светлый ламинат",
        },
        "Нейтральная светлая палитра.",
    ),
    (
        "Тёплый",
        {
            "style": "современный",
            "wall_color": "бежевый",
            "ceiling_color": "белый",
            "floor_material": "тёплый ламинат",
        },
        "Тёплая уютная гамма.",
    ),
    (
        "Тёмный",
        {
            "style": "современный",
            "wall_color": "графит",
            "ceiling_color": "белый",
            "floor_material": "кварцвинил",
        },
        "Акцентная тёмная стена.",
    ),
]


def _make_option(name: str, palette: dict[str, str], description: str) -> DesignOption:
    return DesignOption(name=name, palette=palette, description=description)


def fallback_options(info: ProjectInfo) -> list[DesignOption]:
    """Design options without an LLM: consider the style/room (космический, морской,
    детская…), so the fallback is not anonymous and the renders are meaningful."""
    style = (info.style or "").lower()
    room = (info.room_type or "").lower()
    for keywords, options in _FALLBACK_THEMES:
        if any(k in style for k in keywords):
            return [_make_option(n, p, d) for n, p, d in options]
    if "детск" in room and not style:
        return [_make_option(n, p, d) for n, p, d in _FALLBACK_KIDS]
    return [_make_option(n, p, d) for n, p, d in _FALLBACK_DEFAULT]


def fallback_plan(info: ProjectInfo) -> list[PlanStep]:
    """Deterministic plan when the model did not answer (§67)."""
    floor = info.floor_area or info.area or 10.0
    walls = info.walls_area or round(floor * 2.6, 1)
    packs = max(1, round(floor / 2.0))
    perimeter = info.perimeter or round(2 * (math.sqrt(floor) + math.sqrt(floor)), 1)
    return [
        PlanStep(
            name="Черновые работы",
            description="Выровнять стены и потолок, прогрунтовать под финиш.",
            materials=[
                f"штукатурка ~{max(6, round(walls / 6))} мешков",
                f"шпаклёвка ~{max(3, round(walls / 8))} мешка",
                "грунтовка ~3 л",
            ],
        ),
        PlanStep(
            name="Отделка стен",
            description="Покрасить или поклеить обои с учётом палитры.",
            materials=["краска ~2 банки", "обои ~4 рулона"],
        ),
        PlanStep(
            name="Отделка потолка",
            description="Побелить или покрасить потолок.",
            materials=["грунтовка ~1 л", "краска потолочная ~2 л"],
        ),
        PlanStep(
            name="Укладка пола",
            description="Постелить подложку и напольное покрытие, установить плинтусы.",
            materials=[
                f"подложка ~{round(floor)} м²",
                f"ламинат ~{packs} упаковок",
                f"плинтус ~{round(perimeter)} м",
            ],
        ),
    ]
