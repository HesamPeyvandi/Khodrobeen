from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.web.auth import SESSION_KEY, verify_login

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if verify_login(username, password):
            session[SESSION_KEY] = True
            return redirect(url_for("dashboard.index"))
        flash("نام کاربری یا رمز عبور اشتباه است.", "error")
    return render_template("login.html")


@bp.get("/logout")
def logout():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("auth.login"))
