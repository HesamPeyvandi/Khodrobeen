from datetime import timedelta

from flask import Blueprint, render_template
from sqlalchemy import func, select

from app.db.models import SeenListing, SubscriptionStatus, User
from app.db.session import get_session
from app.time_utils import utcnow
from app.web.auth import login_required

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    session_db = get_session()
    try:
        total_users = session_db.scalar(select(func.count(User.id))) or 0
        active_users = sum(1 for u in session_db.scalars(select(User)).all() if u.is_currently_active())
        trial_users = session_db.scalar(
            select(func.count(User.id)).where(User.status == SubscriptionStatus.TRIAL)
        ) or 0

        since = utcnow() - timedelta(days=1)
        deals_today = session_db.scalar(
            select(func.count(SeenListing.id)).where(
                SeenListing.is_deal.is_(True), SeenListing.first_seen_at >= since
            )
        ) or 0
        scanned_today = session_db.scalar(
            select(func.count(SeenListing.id)).where(SeenListing.first_seen_at >= since)
        ) or 0

        recent_deals = session_db.scalars(
            select(SeenListing)
            .where(SeenListing.is_deal.is_(True))
            .order_by(SeenListing.first_seen_at.desc())
            .limit(10)
        ).all()

        return render_template(
            "dashboard.html",
            total_users=total_users,
            active_users=active_users,
            trial_users=trial_users,
            deals_today=deals_today,
            scanned_today=scanned_today,
            recent_deals=recent_deals,
        )
    finally:
        session_db.close()
