from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from app.config import settings
from app.db.models import User
from app.db.session import get_session
from app.services.subscription import activate_subscription, disable_user, enable_user

router = Router(name="admin")


def _is_admin(telegram_user_id: int) -> bool:
    return telegram_user_id in settings.admin_telegram_ids


@router.message(Command("admin_users"))
async def handle_admin_users(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    session = get_session()
    try:
        users = session.scalars(select(User)).all()
        if not users:
            await message.answer("هنوز کاربری ثبت نشده.")
            return
        lines = []
        for u in users:
            expires = u.subscription_expires_at.strftime("%Y-%m-%d") if u.subscription_expires_at else "-"
            lines.append(f"{u.telegram_user_id} | @{u.telegram_username or '-'} | {u.status.value} | تا {expires}")
        await message.answer("\n".join(lines))
    finally:
        session.close()


@router.message(Command("admin_activate"))
async def handle_admin_activate(message: Message) -> None:
    """Usage: /admin_activate <telegram_id> <days>"""
    if not _is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("فرمت درست: /admin_activate <telegram_id> <days>")
        return

    target_id, days = int(parts[1]), int(parts[2])
    session = get_session()
    try:
        user = session.scalar(select(User).where(User.telegram_user_id == target_id))
        if not user:
            await message.answer("کاربری با این شناسه پیدا نشد. باید حداقل یک‌بار /start زده باشد.")
            return
        activate_subscription(session, user, days=days, provider="manual", note=f"activated by admin {message.from_user.id}")
        await message.answer(f"اشتراک کاربر {target_id} به مدت {days} روز فعال/تمدید شد.")
    finally:
        session.close()


@router.message(Command("admin_disable"))
async def handle_admin_disable(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /admin_disable <telegram_id>")
        return

    target_id = int(parts[1])
    session = get_session()
    try:
        user = session.scalar(select(User).where(User.telegram_user_id == target_id))
        if not user:
            await message.answer("کاربر پیدا نشد.")
            return
        disable_user(session, user)
        await message.answer(f"کاربر {target_id} غیرفعال شد.")
    finally:
        session.close()


@router.message(Command("admin_enable"))
async def handle_admin_enable(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /admin_enable <telegram_id>")
        return

    target_id = int(parts[1])
    session = get_session()
    try:
        user = session.scalar(select(User).where(User.telegram_user_id == target_id))
        if not user:
            await message.answer("کاربر پیدا نشد.")
            return
        enable_user(session, user)
        await message.answer(f"کاربر {target_id} فعال شد.")
    finally:
        session.close()
