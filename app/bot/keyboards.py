from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.constants.cities import CITIES

CITY_CALLBACK_PREFIX = "city:"
CITIES_DONE_CALLBACK = "cities_done"


def cities_keyboard(selected_slugs: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for slug, name in CITIES.items():
        mark = "✅ " if slug in selected_slugs else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{name}", callback_data=f"{CITY_CALLBACK_PREFIX}{slug}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="پایان انتخاب ✔️", callback_data=CITIES_DONE_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏙 انتخاب شهرها", callback_data="menu:cities")],
            [InlineKeyboardButton(text="📊 وضعیت اشتراک", callback_data="menu:status")],
            [InlineKeyboardButton(text="💳 خرید/تمدید اشتراک", callback_data="menu:subscribe")],
        ]
    )
