from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.scheduler.jobs import run_scan_cycle


def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Tehran")
    scheduler.add_job(
        run_scan_cycle,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        args=[bot],
        id="divar_scan_cycle",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
