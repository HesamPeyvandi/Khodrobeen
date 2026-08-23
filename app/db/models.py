import enum
from datetime import datetime, timedelta

from app.time_utils import utcnow
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    DISABLED = "disabled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.TRIAL
    )
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    max_price_toman: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_price_toman: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    watched_cities: Mapped[list["WatchedCity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["PaymentRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def is_currently_active(self) -> bool:
        if self.status == SubscriptionStatus.DISABLED:
            return False
        if self.subscription_expires_at is None:
            # No expiry set means unlimited (e.g. admin-granted access)
            return self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL)
        return self.subscription_expires_at > utcnow()

    def city_slugs(self) -> list[str]:
        return [c.city_slug for c in self.watched_cities]


class WatchedCity(Base):
    __tablename__ = "watched_cities"
    __table_args__ = (UniqueConstraint("user_id", "city_slug", name="uq_user_city"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    city_slug: Mapped[str] = mapped_column(String(64))

    user: Mapped["User"] = relationship(back_populates="watched_cities")


class ListingStatus(str, enum.Enum):
    OK = "ok"
    SCRAPE_FAILED = "scrape_failed"
    ESTIMATE_FAILED = "estimate_failed"
    SKIPPED = "skipped"


class SeenListing(Base):
    """Global de-duplication table: once a Divar listing token has been
    processed, we never re-fetch its price estimate or re-notify about it -
    including listings that failed, so a permanently-broken listing (bad
    data, unsupported model, etc.) doesn't get retried forever.
    """

    __tablename__ = "seen_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    city_slug: Mapped[str] = mapped_column(String(64))
    category_slug: Mapped[str] = mapped_column(String(32))

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(String(512))
    divar_price_toman: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_price_toman: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_deal: Mapped[bool] = mapped_column(Boolean, default=False)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.OK, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )


class PaymentRecord(Base):
    """Kept intentionally generic so a real gateway (e.g. Zarinpal) can be
    plugged in later without changing the schema. See app/services/payment.
    """

    __tablename__ = "payment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    provider: Mapped[str] = mapped_column(String(32))  # "manual", "zarinpal", ...
    amount_toman: Mapped[float] = mapped_column(Float)
    extends_days: Mapped[int] = mapped_column(Integer)
    reference_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="payments")


def default_trial_expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
