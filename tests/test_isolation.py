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


def test_a_tampered_session_signature_is_rejected_outright(app, client_a):
    """Altering the signature logs you out; it does not promote you.

    The mutated character is in the middle of the signature, not at the end:
    base64's final character carries only a couple of significant bits, so
    several different trailing characters decode to the same signature bytes.
    Flipping the last character is a coin toss, not a test.
    """
    value = client_a.get_cookie("session").value
    head, _, signature = value.rpartition(".")
    mid = len(signature) // 2
    swapped = "A" if signature[mid] != "A" else "B"
    forged = app.test_client()
    forged.set_cookie("session", f"{head}.{signature[:mid]}{swapped}{signature[mid + 1:]}")
    resp = forged.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_a_rewritten_cookie_payload_cannot_promote_you_to_another_tenant(app, client_a):
    """Edit the cookie to claim org B's user id, keep org A's signature."""
    import base64
    import json

    conn = db.connect(app.config["DB_PATH"])
    b_user_id = conn.execute(
        "SELECT id FROM users WHERE email = ?", (B_EMAIL,)
    ).fetchone()[0]
    raw = json.dumps({"user_id": b_user_id}, separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    _old_payload, _, signature_part = client_a.get_cookie("session").value.partition(".")
    forged = app.test_client()
    forged.set_cookie("session", f"{payload}.{signature_part}")
    resp = forged.get("/dashboard")
    assert resp.status_code == 302, "a rewritten payload was accepted"
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


def test_an_out_of_range_invoice_id_is_a_plain_404(app, client_a):
    """SQLite integers are 64-bit. A larger id cannot match any row, so it must
    behave like any other miss rather than raising a 500."""
    huge = client_a.get("/invoices/99999999999999999999")
    miss = client_a.get("/invoices/999999")
    assert huge.status_code == 404
    assert huge.data == miss.data
