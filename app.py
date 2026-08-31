import os
from datetime import timedelta

from flask import Flask

import db


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

    @app.route("/healthz")
    def healthz():
        return "ok"

    return app
