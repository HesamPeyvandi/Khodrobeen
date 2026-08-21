"""Single-process entrypoint.

Runs three things together so the whole project fits on one free web
service (e.g. Render's free plan, kept awake with an UptimeRobot ping to
/health):

  1. The Flask admin panel (in a background thread, listening on $PORT)
  2. The APScheduler job that scans Divar + estimates prices on an interval
  3. The Telegram bot, long-polling for commands/button presses

Run locally with:  python main.py
"""

import asyncio
import logging
import os
import threading

from waitress import serve

from app.bot.bot_instance import create_bot, create_dispatcher
from app.config import settings
from app.db.session import init_db
from app.logging_config import configure_logging
from app.scheduler.setup import create_scheduler
from app.web.app import create_app

logger = logging.getLogger(__name__)


def _run_web_panel() -> None:
    app = create_app()
    port = int(os.getenv("PORT", settings.web_port))
    logger.info("Starting admin panel on %s:%s", settings.web_host, port)
    serve(app, host=settings.web_host, port=port)


async def _run_bot_and_scheduler() -> None:
    bot = create_bot()
    dispatcher = create_dispatcher()

    scheduler = create_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started (interval: %ss)", settings.poll_interval_seconds)

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    configure_logging()
    init_db()

    web_thread = threading.Thread(target=_run_web_panel, daemon=True)
    web_thread.start()

    asyncio.run(_run_bot_and_scheduler())


if __name__ == "__main__":
    main()
