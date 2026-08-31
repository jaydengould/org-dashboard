import pytest

import app as app_module
import seed
from helpers import csrf_token, login

ALDER_EMAIL, ALDER_PW = seed.CREDENTIALS[0]


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    application = app_module.create_app(db_path=path, secret_key="test-only-secret")
    application.config["TESTING"] = True
    return application


def test_login_post_without_a_csrf_token_is_rejected(app):
    client = app.test_client()
    resp = client.post("/login", data={"email": ALDER_EMAIL, "password": ALDER_PW})
    assert resp.status_code == 400


def test_login_post_with_a_wrong_csrf_token_is_rejected(app):
    client = app.test_client()
    csrf_token(client)  # establish a session token
    resp = client.post(
        "/login",
        data={"email": ALDER_EMAIL, "password": ALDER_PW, "csrf_token": "not-the-token"},
    )
    assert resp.status_code == 400


def test_empty_token_does_not_match_an_absent_session_token(app):
    """The compare must not succeed on '' == ''."""
    client = app.test_client()
    resp = client.post(
        "/login", data={"email": ALDER_EMAIL, "password": ALDER_PW, "csrf_token": ""}
    )
    assert resp.status_code == 400


def test_logout_clears_the_session(app):
    client = app.test_client()
    login(client, ALDER_EMAIL, ALDER_PW)
    resp = client.post("/logout", data={"csrf_token": csrf_token(client, "/dashboard")})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert client.get("/dashboard").status_code == 302


def test_logout_is_not_reachable_by_get(app):
    client = app.test_client()
    login(client, ALDER_EMAIL, ALDER_PW)
    assert client.get("/logout").status_code == 405
    assert client.get("/dashboard").status_code == 200  # still signed in


def test_login_rotates_the_session(app):
    client = app.test_client()
    csrf_token(client)
    with client.session_transaction() as sess:
        before = sess.get("csrf_token")
    login(client, ALDER_EMAIL, ALDER_PW)
    with client.session_transaction() as sess:
        assert sess.get("csrf_token") != before or sess.get("csrf_token") is None
