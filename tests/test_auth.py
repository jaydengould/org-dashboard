import pytest

import app as app_module
import seed

ALDER_EMAIL, ALDER_PW = seed.CREDENTIALS[0]


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    application = app_module.create_app(db_path=path, secret_key="test-only-secret")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def anon(app):
    return app.test_client()


def test_protected_route_redirects_anonymous_user_to_login(anon):
    resp = anon.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_protected_route_leaks_no_data_to_anonymous_user(anon):
    resp = anon.get("/dashboard")
    assert b"Alder" not in resp.data


def test_login_with_correct_credentials_redirects_to_dashboard(anon):
    resp = anon.post("/login", data={"email": ALDER_EMAIL, "password": ALDER_PW})
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]


def test_logged_in_user_sees_their_own_org_name(anon):
    anon.post("/login", data={"email": ALDER_EMAIL, "password": ALDER_PW})
    resp = anon.get("/dashboard")
    assert resp.status_code == 200
    assert b"Alder &amp; Finch Bookbinding" in resp.data


def test_wrong_password_and_unknown_email_are_indistinguishable(anon):
    bad_pw = anon.post("/login", data={"email": ALDER_EMAIL, "password": "wrong"})
    unknown = anon.post("/login", data={"email": "nobody@nowhere.test", "password": "wrong"})
    assert bad_pw.status_code == unknown.status_code == 200
    assert bad_pw.data == unknown.data


def test_email_lookup_is_case_insensitive(anon):
    resp = anon.post("/login", data={"email": ALDER_EMAIL.upper(), "password": ALDER_PW})
    assert resp.status_code == 302


def test_session_holds_only_the_user_id(anon):
    anon.post("/login", data={"email": ALDER_EMAIL, "password": ALDER_PW})
    with anon.session_transaction() as sess:
        assert set(sess.keys()) <= {"user_id", "_permanent", "csrf_token"}
        assert "org_id" not in sess


def test_healthz_stays_public_now_that_a_gate_exists(anon):
    assert anon.get("/healthz").status_code == 200
