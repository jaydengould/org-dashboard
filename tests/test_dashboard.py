import pytest

import app as app_module
import seed
from helpers import login

A_EMAIL, A_PW = seed.CREDENTIALS[0]   # Alder & Finch
B_EMAIL, B_PW = seed.CREDENTIALS[1]   # Cobalt Harbour


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    application = app_module.create_app(db_path=path, secret_key="test-only-secret")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client_a(app):
    client = app.test_client()
    login(client, A_EMAIL, A_PW)
    return client


def test_dashboard_shows_your_own_org(client_a):
    body = client_a.get("/dashboard").data
    assert b"Alder &amp; Finch Bookbinding" in body


def test_dashboard_does_not_show_another_orgs_name_or_invoices(client_a):
    body = client_a.get("/dashboard").data
    assert b"Cobalt Harbour" not in body
    assert b"Meridian Fern" not in body
    assert b"CH-" not in body
    assert b"MF-" not in body


def test_dashboard_lists_twelve_months(client_a):
    body = client_a.get("/dashboard").data.decode()
    for month in seed.MONTHS:
        assert month in body


def test_totals_match_the_seed_data(app, client_a):
    expected_revenue = sum(seed.ORGS[0][5]) * 100
    body = client_a.get("/dashboard").data.decode()
    assert app_module.money(expected_revenue) in body


def test_money_formatting_uses_no_floats():
    assert app_module.money(812450) == "$8,124.50"
    assert app_module.money(5) == "$0.05"
    assert app_module.money(-1234) == "-$12.34"
    assert app_module.money(0) == "$0.00"
