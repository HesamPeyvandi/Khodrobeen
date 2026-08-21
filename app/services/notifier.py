import logging

from aiogram import Bot

from app.constants.cities import city_name
from app.db.models import SeenListing

logger = logging.getLogger(__name__)


def format_deal_message(listing: SeenListing) -> str:
    price = f"{listing.divar_price_toman:,.0f}" if listing.divar_price_toman else "نامشخص"
    estimate = (
        f"{listing.estimated_price_toman:,.0f}" if listing.estimated_price_toman else "نامشخص"
    )
    diff = ""
    if listing.divar_price_toman and listing.estimated_price_toman:
        diff_amount = listing.estimated_price_toman - listing.divar_price_toman
        diff_percent = (diff_amount / listing.estimated_price_toman) * 100
        diff = f"\n💰 اختلاف: {diff_amount:,.0f} تومان ({diff_percent:.1f}٪ ارزان‌تر)"

    return (
        "🚗 <b>یک ماشین ارزنده پیدا شد!</b>\n\n"
        f"<b>{listing.title or 'بدون عنوان'}</b>\n"
        f"📍 {city_name(listing.city_slug)}\n"
        f"💵 قیمت دیوار: {price} تومان\n"
        f"📊 تخمین همراه مکانیک: {estimate} تومان"
        f"{diff}\n\n"
        f"🔗 {listing.url}"
    )


async def notify_users(bot: Bot, telegram_user_ids: list[int], listing: SeenListing) -> None:
    message = format_deal_message(listing)
    for user_id in telegram_user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=message, disable_web_page_preview=False)
        except Exception:
            logger.exception("Failed to send deal notification to %s", user_id)
