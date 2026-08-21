from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.bot.handlers import admin, cities, start, status
from app.config import settings


def create_bot() -> Bot:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    session = None
    if settings.telegram_proxy_url:
        # Only relevant for local development inside Iran, where
        # api.telegram.org is filtered. A server deployed outside Iran
        # (e.g. Render) reaches Telegram directly and never needs this.
        session = AiohttpSession(proxy=settings.telegram_proxy_url)

    return Bot(
        token=settings.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(cities.router)
    dp.include_router(status.router)
    dp.include_router(admin.router)
    return dp
