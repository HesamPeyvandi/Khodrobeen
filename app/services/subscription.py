from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import PaymentRecord, SubscriptionStatus, User, WatchedCity
from app.time_utils import utcnow


def get_or_create_user(
    session: Session,
    telegram_user_id: int,
    telegram_username: str | None = None,
    full_name: str | None = None,
) -> tuple[User, bool]:
    """Returns (user, created)."""
    user = session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user:
        return user, False

    is_admin = telegram_user_id in settings.admin_telegram_ids
    expires_at = None
    status = SubscriptionStatus.ACTIVE if is_admin else SubscriptionStatus.TRIAL
    if not is_admin and settings.default_trial_days > 0:
        expires_at = utcnow() + timedelta(days=settings.default_trial_days)

    user = User(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        full_name=full_name,
        is_admin=is_admin,
        status=status,
        subscription_expires_at=expires_at,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user, True


def set_user_cities(session: Session, user: User, city_slugs: list[str]) -> None:
    session.query(WatchedCity).filter(WatchedCity.user_id == user.id).delete()
    for slug in city_slugs:
        session.add(WatchedCity(user_id=user.id, city_slug=slug))
    session.commit()


def toggle_user_city(session: Session, user: User, city_slug: str) -> bool:
    """Adds the city if not watched, removes it otherwise. Returns True if now watched."""
    existing = session.scalar(
        select(WatchedCity).where(
            WatchedCity.user_id == user.id, WatchedCity.city_slug == city_slug
        )
    )
    if existing:
        session.delete(existing)
        session.commit()
        return False

    session.add(WatchedCity(user_id=user.id, city_slug=city_slug))
    session.commit()
    return True


def activate_subscription(
    session: Session,
    user: User,
    days: int,
    provider: str = "manual",
    amount_toman: float = 0,
    reference_code: str | None = None,
    note: str | None = None,
) -> User:
    """Extends (or starts) a user's subscription and logs a payment record.

    This is the single choke point used by both the manual admin-panel
    button and any future real payment gateway webhook, so billing logic
    only has to be correct in one place.
    """
    base = (
        user.subscription_expires_at
        if user.subscription_expires_at and user.subscription_expires_at > utcnow()
        else utcnow()
    )
    user.subscription_expires_at = base + timedelta(days=days)
    user.status = SubscriptionStatus.ACTIVE

    session.add(
        PaymentRecord(
            user_id=user.id,
            provider=provider,
            amount_toman=amount_toman,
            extends_days=days,
            reference_code=reference_code,
            note=note,
        )
    )
    session.commit()
    session.refresh(user)
    return user


def disable_user(session: Session, user: User) -> User:
    user.status = SubscriptionStatus.DISABLED
    session.commit()
    session.refresh(user)
    return user


def enable_user(session: Session, user: User) -> User:
    user.status = (
        SubscriptionStatus.ACTIVE
        if user.subscription_expires_at and user.subscription_expires_at > utcnow()
        else SubscriptionStatus.TRIAL
    )
    session.commit()
    session.refresh(user)
    return user


def mark_expired_subscriptions(session: Session) -> int:
    """Should run periodically (the scheduler calls this every scan cycle)."""
    now = utcnow()
    expired_users = session.scalars(
        select(User).where(
            User.subscription_expires_at.isnot(None),
            User.subscription_expires_at <= now,
            User.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]),
        )
    ).all()
    for user in expired_users:
        user.status = SubscriptionStatus.EXPIRED
    if expired_users:
        session.commit()
    return len(expired_users)


def active_watched_city_slugs(session: Session) -> set[str]:
    """Union of cities watched by every currently-active user - this is the
    set the scraper actually needs to scan.
    """
    users = session.scalars(select(User)).all()
    slugs: set[str] = set()
    for user in users:
        if user.is_currently_active():
            slugs.update(user.city_slugs())
    return slugs


def active_users_watching_city(session: Session, city_slug: str) -> list[User]:
    users = session.scalars(select(User)).all()
    return [
        u
        for u in users
        if u.is_currently_active() and city_slug in u.city_slugs()
    ]
