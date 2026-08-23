import asyncio
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

# Hard ceiling on a single scan cycle's total duration. This is a safety
# net, not the primary fix for slow scans (see the Hamrah Mechanic circuit
# breaker in price_estimator.py, which is what actually keeps a fully-down
# upstream site from making scans balloon to an hour+) - but it guarantees
# the scheduler can never get stuck skipping runs forever no matter what
# goes wrong inside a scan, since _scan_running always gets released.
MAX_SCAN_CYCLE_SECONDS = 20 * 60


async def run_scan_cycle(bot: Bot) -> None:
    global _scan_running
    if _scan_running:
        logger.warning("Previous scan cycle still running - skipping this tick")
        return

    _scan_running = True
    try:
        await asyncio.wait_for(_run_scan_cycle_inner(bot), timeout=MAX_SCAN_CYCLE_SECONDS)
    except asyncio.TimeoutError:
        logger.error(
            "Scan cycle exceeded the %d-minute safety limit and was cancelled - "
            "if this keeps happening, Hamrah Mechanic/Divar are likely unreachable "
            "and PAGE_GOTO_RETRIES or MAX_LISTINGS_PER_SCAN may need lowering",
            MAX_SCAN_CYCLE_SECONDS // 60,
        )
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
                        session.rollback()
                        continue

                    for record in new_records:
                        if not record.is_deal or record.notified:
                            continue
                        interested_users = active_users_watching_city(session, city_slug)
                        user_ids = [u.telegram_user_id for u in interested_users]
                        if user_ids:
                            await notify_users(bot, user_ids, record)
                        record.notified = True
                        try:
                            session.commit()
                        except Exception:
                            logger.exception("Failed to mark record notified for token=%s", record.token)
                            session.rollback()
    finally:
        session.close()
