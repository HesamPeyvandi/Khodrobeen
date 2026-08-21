from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.keyboards import (
    CITIES_DONE_CALLBACK,
    CITY_CALLBACK_PREFIX,
    cities_keyboard,
    main_menu_keyboard,
)
from app.constants.cities import is_valid_city
from app.db.session import get_session
from app.services.subscription import get_or_create_user, toggle_user_city

router = Router(name="cities")


@router.callback_query(F.data.startswith(CITY_CALLBACK_PREFIX))
async def handle_toggle_city(callback: CallbackQuery) -> None:
    city_slug = callback.data.removeprefix(CITY_CALLBACK_PREFIX)
    if not is_valid_city(city_slug):
        await callback.answer("شهر نامعتبر", show_alert=True)
        return

    session = get_session()
    try:
        user, _ = get_or_create_user(session, telegram_user_id=callback.from_user.id)
        toggle_user_city(session, user, city_slug)
        selected = set(user.city_slugs())
        await callback.message.edit_reply_markup(reply_markup=cities_keyboard(selected))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data == CITIES_DONE_CALLBACK)
async def handle_cities_done(callback: CallbackQuery) -> None:
    session = get_session()
    try:
        user, _ = get_or_create_user(session, telegram_user_id=callback.from_user.id)
        cities = user.city_slugs()
    finally:
        session.close()

    if not cities:
        text = "هنوز هیچ شهری انتخاب نکردی. بدون انتخاب شهر، بات نمی‌تونه چیزی برات پیدا کنه."
    else:
        text = f"✅ شهرهای انتخابی ثبت شد. تعداد: {len(cities)}"

    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()
