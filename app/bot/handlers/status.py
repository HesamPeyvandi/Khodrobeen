from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards import main_menu_keyboard
from app.constants.cities import city_name
from app.db.session import get_session
from app.services.payment import PLANS, get_payment_provider
from app.services.subscription import get_or_create_user

router = Router(name="status")


@router.callback_query(F.data == "menu:status")
async def handle_status(callback: CallbackQuery) -> None:
    session = get_session()
    try:
        user, _ = get_or_create_user(session, telegram_user_id=callback.from_user.id)
        cities = ", ".join(city_name(s) for s in user.city_slugs()) or "هیچکدام"
        expires = (
            user.subscription_expires_at.strftime("%Y-%m-%d")
            if user.subscription_expires_at
            else "نامحدود"
        )
        text = (
            f"وضعیت اشتراک: {user.status.value}\n"
            f"تاریخ انقضا: {expires}\n"
            f"شهرهای رصدشده: {cities}"
        )
    finally:
        session.close()

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="menu:back")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:subscribe")
async def handle_subscribe(callback: CallbackQuery) -> None:
    provider = get_payment_provider()
    lines = ["پلن‌های موجود:\n"]
    for plan in PLANS:
        price = f"{plan.price_toman:,.0f} تومان" if plan.price_toman else "به‌صورت دستی هماهنگ می‌شود"
        lines.append(f"• {plan.title} - {price}")

    if provider.name == "manual":
        lines.append(
            "\nفعلاً فعال‌سازی اشتراک به‌صورت دستی توسط مدیر انجام می‌شود. "
            "بعد از هماهنگی و واریز، شناسه‌ی تلگرام‌ت رو به مدیر بده تا فعال بشی."
        )
        lines.append(f"\nشناسه‌ی تلگرام شما: <code>{callback.from_user.id}</code>")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ بازگشت", callback_data="menu:back")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()
