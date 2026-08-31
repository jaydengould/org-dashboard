import pytest

import app as app_module
import db
import seed
from helpers import login

A_EMAIL, A_PW = seed.CREDENTIALS[0]
B_EMAIL, B_PW = seed.CREDENTIALS[1]


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    application = app_module.create_app(db_path=path, secret_key="test-only-secret")
    application.config["TESTING"] = True
    return application


def _invoice_ids(app, email):
    """Read real ids from the database. Never hardcode them in a test."""
    conn = db.connect(app.config["DB_PATH"])
    return [
        row[0]
        for row in conn.execute(
            "SELECT i.id FROM invoices i JOIN users u ON u.org_id = i.org_id"
            " WHERE u.email = ? ORDER BY i.id",
            (email,),
        )
    ]


@pytest.fixture
def client_a(app):
    client = app.test_client()
    login(client, A_EMAIL, A_PW)
    return client


def test_can_read_own_invoice(app, client_a):
    own = _invoice_ids(app, A_EMAIL)[0]
    resp = client_a.get(f"/invoices/{own}")
    assert resp.status_code == 200
    assert b"AF-" in resp.data


def test_cannot_read_another_orgs_invoice_by_id(app, client_a):
    """The headline test: URL tampering across the tenant boundary."""
    foreign_ids = _invoice_ids(app, B_EMAIL)
    assert foreign_ids, "fixture is broken: org B has no invoices to attack"
    for foreign in foreign_ids:
        resp = client_a.get(f"/invoices/{foreign}")
        assert resp.status_code == 404, f"invoice {foreign} leaked across orgs"


def test_foreign_invoice_is_indistinguishable_from_a_nonexistent_one(app, client_a):
    """404, not 403: the response must not confirm that the row exists."""
    foreign = _invoice_ids(app, B_EMAIL)[0]
    real_miss = client_a.get("/invoices/999999")
    cross_tenant = client_a.get(f"/invoices/{foreign}")
    assert cross_tenant.status_code == real_miss.status_code == 404
    assert cross_tenant.data == real_miss.data


def test_a_replayed_session_cookie_still_cannot_cross_the_boundary(app, client_a):
    """Capture A's cookie, use it from a fresh client, ask for B's row."""
    cookie = client_a.get_cookie("session")
    replay = app.test_client()
    replay.set_cookie("session", cookie.value)
    foreign = _invoice_ids(app, B_EMAIL)[0]
    assert replay.get("/dashboard").status_code == 200         # cookie is valid
    assert replay.get(f"/invoices/{foreign}").status_code == 404  # and still scoped


def test_a_tampered_session_cookie_is_rejected_outright(app, client_a):
    """Flipping one byte of the signature logs you out; it does not promote you."""
    value = client_a.get_cookie("session").value
    tampered = value[:-1] + ("a" if value[-1] != "a" else "b")
    forged = app.test_client()
    forged.set_cookie("session", tampered)
    resp = forged.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anonymous_cannot_reach_any_invoice(app):
    anon = app.test_client()
    for invoice_id in _invoice_ids(app, A_EMAIL):
        resp = anon.get(f"/invoices/{invoice_id}")
        assert resp.status_code == 302
        assert b"AF-" not in resp.data


def test_every_get_route_is_public_by_declaration_or_protected(app):
    """Fails when a route is added and nobody thought about authentication."""
    anon = app.test_client()
    adapter = app.url_map.bind("localhost")
    for rule in app.url_map.iter_rules():
        if rule.endpoint in app_module.PUBLIC_ENDPOINTS:
            continue
        if "GET" not in rule.methods:
            continue
        url = adapter.build(rule.endpoint, {arg: 1 for arg in rule.arguments})
        resp = anon.get(url)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], (
            f"{rule.endpoint} ({url}) is neither declared public nor protected"
        )
