from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import settings

SESSION_KEY = "admin_logged_in"


def hash_password(plain_password: str) -> str:
    """Run once locally to generate ADMIN_PANEL_PASSWORD_HASH:

        python -c "from app.web.auth import hash_password; print(hash_password('your-password'))"

    then put the output in the ADMIN_PANEL_PASSWORD_HASH environment variable.
    """
    return generate_password_hash(plain_password)


def verify_login(username: str, password: str) -> bool:
    if not settings.admin_panel_password_hash:
        return False
    if username != settings.admin_panel_username:
        return False
    return check_password_hash(settings.admin_panel_password_hash, password)


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get(SESSION_KEY):
            return redirect(url_for("auth.login"))
        return view_func(*args, **kwargs)

    return wrapped
