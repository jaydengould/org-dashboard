import hmac
import os
import secrets
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

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        """Registered before require_login so it also covers the public POST /login."""
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        stored = session.get("csrf_token")
        sent = request.form.get("csrf_token", "")
        # `stored` must be truthy first: hmac.compare_digest("", "") is True,
        # which would wave through a request with no token when the session has
        # none either.
        if not stored or not hmac.compare_digest(sent, stored):
            abort(400)
        return None

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

    @app.route("/invoices/<int:invoice_id>")
    def invoice_detail(invoice_id):
        rows = db.query(
            "SELECT id, number, counterparty, issued_on, amount_cents, status"
            " FROM invoices WHERE org_id = :org_id AND id = :invoice_id",
            invoice_id=invoice_id,
        )
        if not rows:
            # 404, not 403: a 403 would confirm that another org's invoice
            # exists. This response is identical to a genuinely missing row.
            # There is deliberately no custom 404 handler, so both cases render
            # Flask's default page, which carries no session or org context.
            abort(404)
        return render_template("invoice.html", invoice=rows[0])

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        # These two queries are the entire tenant-data surface of the
        # application. Both carry `org_id = :org_id`; db.query supplies the
        # value, from the session, and refuses to run without it.
        figures = db.query(
            "SELECT month, revenue_cents, expenses_cents FROM monthly_figures"
            " WHERE org_id = :org_id ORDER BY month"
        )
        invoices = db.query(
            "SELECT id, number, counterparty, issued_on, amount_cents, status"
            " FROM invoices WHERE org_id = :org_id ORDER BY issued_on DESC"
        )
        total_revenue = sum(f["revenue_cents"] for f in figures)
        total_expenses = sum(f["expenses_cents"] for f in figures)
        outstanding = sum(
            i["amount_cents"] for i in invoices
            if i["status"] in ("outstanding", "overdue")
        )
        # Tallest bar in the chart. `or [1]` keeps an empty org from dividing
        # by zero rather than special-casing it in the template.
        chart_max = max(
            [f["revenue_cents"] for f in figures]
            + [f["expenses_cents"] for f in figures]
            or [1]
        )
        return render_template(
            "dashboard.html",
            chart_max=chart_max,
            figures=figures,
            invoices=invoices,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net=total_revenue - total_expenses,
            outstanding=outstanding,
        )

    return app
