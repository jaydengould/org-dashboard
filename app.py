import os
from datetime import timedelta

from flask import (
    Flask, abort, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db

# Endpoints reachable without a session. Everything not listed here is
# protected by require_login below. Adding a route makes it protected by
# default; making it public requires editing this line, in this file, where a
# reviewer will see it.
PUBLIC_ENDPOINTS = {"login", "healthz", "static", "index"}

# Compared against when the email is unknown, so that a wrong password and a
# nonexistent account take the same amount of time.
_DUMMY_HASH = generate_password_hash("this-account-does-not-exist")


def money(cents):
    """Format integer cents. No floats: this is an accounting demo."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def create_app(db_path=None, secret_key=None):
    app = Flask(__name__)

    key = secret_key or os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set; refusing to start")

    app.config.update(
        DB_PATH=db_path or os.environ.get("DB_PATH", "portal.db"),
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Render terminates TLS; local dev and the test client speak http, so
        # this is gated on the environment rather than hardcoded True.
        SESSION_COOKIE_SECURE=os.environ.get("PORTAL_ENV") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.teardown_appcontext(db.close_conn)
    app.jinja_env.filters["money"] = money

    @app.before_request
    def require_login():
        """Chokepoint 1 of 2: authentication. Default deny.

        Everything that is not explicitly listed in PUBLIC_ENDPOINTS needs a
        session. A route added later is protected the moment it exists; nobody
        has to remember a decorator.
        """
        g.user = None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("login"))
        user = db.get_user(user_id)
        if user is None:  # account deleted underneath a live session
            session.clear()
            return redirect(url_for("login"))
        g.user = user
        g.org_id = user["org_id"]
        g.org = db.get_org(user["org_id"])
        return None

    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if session.get("user_id") else "login"))

    @app.route("/healthz")
    def healthz():
        return "ok"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("user_id"):
                return redirect(url_for("dashboard"))
            return render_template("login.html")

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = db.get_user_by_email(email)
        if row is None:
            # Spend the same time as a real check, so the response does not
            # reveal whether the account exists.
            check_password_hash(_DUMMY_HASH, password)
            return render_template("login.html", error="Incorrect email or password.")
        if not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")

        session.clear()  # no session fixation: a fresh session on every login
        session["user_id"] = row["id"]
        session.permanent = True
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html")

    return app
