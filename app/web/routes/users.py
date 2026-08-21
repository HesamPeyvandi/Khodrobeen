from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.constants.cities import city_name
from app.db.models import User
from app.db.session import get_session
from app.services.subscription import activate_subscription, disable_user, enable_user
from app.web.auth import login_required

bp = Blueprint("users", __name__)


@bp.get("/users")
@login_required
def list_users():
    session_db = get_session()
    try:
        users = session_db.scalars(select(User).order_by(User.created_at.desc())).all()
        rows = [
            {
                "id": u.id,
                "telegram_user_id": u.telegram_user_id,
                "telegram_username": u.telegram_username,
                "status": u.status.value,
                "is_active": u.is_currently_active(),
                "expires_at": u.subscription_expires_at,
                "cities": ", ".join(city_name(s) for s in u.city_slugs()) or "-",
            }
            for u in users
        ]
        return render_template("users.html", users=rows)
    finally:
        session_db.close()


@bp.post("/users/<int:user_id>/activate")
@login_required
def activate(user_id: int):
    days = request.form.get("days", type=int, default=30)
    session_db = get_session()
    try:
        user = session_db.get(User, user_id)
        if not user:
            flash("کاربر پیدا نشد.", "error")
            return redirect(url_for("users.list_users"))
        activate_subscription(session_db, user, days=days, provider="manual", note="activated via panel")
        flash(f"اشتراک {days} روز تمدید/فعال شد.", "success")
    finally:
        session_db.close()
    return redirect(url_for("users.list_users"))


@bp.post("/users/<int:user_id>/disable")
@login_required
def disable(user_id: int):
    session_db = get_session()
    try:
        user = session_db.get(User, user_id)
        if user:
            disable_user(session_db, user)
            flash("کاربر غیرفعال شد.", "success")
    finally:
        session_db.close()
    return redirect(url_for("users.list_users"))


@bp.post("/users/<int:user_id>/enable")
@login_required
def enable(user_id: int):
    session_db = get_session()
    try:
        user = session_db.get(User, user_id)
        if user:
            enable_user(session_db, user)
            flash("کاربر فعال شد.", "success")
    finally:
        session_db.close()
    return redirect(url_for("users.list_users"))
