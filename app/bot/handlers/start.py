from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import cities_keyboard, main_menu_keyboard
from app.db.session import get_session
from app.services.subscription import get_or_create_user

router = Router(name="start")

WELCOME_TEXT = (
    "سلام! 👋 به خودروبین خوش اومدی 🚗\n\n"
    "این بات به‌طور خودکار آگهی‌های خودرو و وانت دیوار رو در شهرهای انتخابی‌ت "
    "بررسی می‌کنه و اگه قیمت آگهی از تخمین همراه مکانیک پایین‌تر باشه، "
    "بلافاصله بهت خبر می‌ده.\n\n"
    "برای شروع، اول شهرهات رو انتخاب کن."
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    session = get_session()
    try:
        user, created = get_or_create_user(
            session,
            telegram_user_id=message.from_user.id,
            telegram_username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        text = WELCOME_TEXT if created else "خوش برگشتی! از منوی زیر ادامه بده."
        await message.answer(text, reply_markup=main_menu_keyboard())
    finally:
        session.close()


@router.callback_query(F.data == "menu:cities")
async def handle_menu_cities(callback: CallbackQuery) -> None:
    session = get_session()
    try:
        user, _ = get_or_create_user(session, telegram_user_id=callback.from_user.id)
        selected = set(user.city_slugs())
        await callback.message.edit_text(
            "شهرهایی که می‌خوای رصد بشن رو انتخاب کن (می‌تونی چندتا انتخاب کنی):",
            reply_markup=cities_keyboard(selected),
        )
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data == "menu:back")
async def handle_menu_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text("منوی اصلی:", reply_markup=main_menu_keyboard())
    await callback.answer()
