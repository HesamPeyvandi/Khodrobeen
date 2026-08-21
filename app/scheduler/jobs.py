import logging

from aiogram import Bot

from app.config import settings
from app.db.session import get_session
from app.services.deal_checker import process_city_category
from app.services.divar_client import DivarScraper
from app.services.notifier import notify_users
from app.services.price_estimator import HamrahMechanicEstimator
from app.services.subscription import (
    active_users_watching_city,
    active_watched_city_slugs,
    mark_expired_subscriptions,
)

logger = logging.getLogger(__name__)

_scan_running = False  # simple re-entrancy guard in case a scan overruns its interval


async def run_scan_cycle(bot: Bot) -> None:
    global _scan_running
    if _scan_running:
        logger.warning("Previous scan cycle still running - skipping this tick")
        return

    _scan_running = True
    try:
        await _run_scan_cycle_inner(bot)
    finally:
        _scan_running = False


async def _run_scan_cycle_inner(bot: Bot) -> None:
    session = get_session()
    try:
        expired = mark_expired_subscriptions(session)
        if expired:
            logger.info("Marked %d subscription(s) as expired", expired)

        city_slugs = active_watched_city_slugs(session)
        if not city_slugs:
            logger.info("No active users are watching any city yet - nothing to scan")
            return

        logger.info("Scanning %d cities: %s", len(city_slugs), ", ".join(sorted(city_slugs)))

        async with DivarScraper() as scraper, HamrahMechanicEstimator() as estimator:
            for city_slug in city_slugs:
                for category_slug in settings.divar_categories:
                    try:
                        new_records = await process_city_category(
                            session, scraper, estimator, city_slug, category_slug
                        )
                    except Exception:
                        logger.exception(
                            "Scan failed for %s/%s", city_slug, category_slug
                        )
                        continue

                    for record in new_records:
                        if not record.is_deal or record.notified:
                            continue
                        interested_users = active_users_watching_city(session, city_slug)
                        user_ids = [u.telegram_user_id for u in interested_users]
                        if user_ids:
                            await notify_users(bot, user_ids, record)
                        record.notified = True
                        session.commit()
    finally:
        session.close()
