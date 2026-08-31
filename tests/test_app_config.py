import pytest

import app as app_module


def test_create_app_refuses_to_start_without_a_secret_key(tmp_path, monkeypatch):
    """A random fallback key silently logs everyone out on every restart.
    A hardcoded one makes sessions forgeable by anyone who reads the repo."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        app_module.create_app(db_path=str(tmp_path / "t.db"))


def test_healthz_is_reachable_without_a_session(tmp_path):
    app = app_module.create_app(db_path=str(tmp_path / "t.db"), secret_key="test-only")
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.data == b"ok"


def test_session_cookie_is_secure_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_ENV", "production")
    app = app_module.create_app(db_path=str(tmp_path / "t.db"), secret_key="test-only")
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
