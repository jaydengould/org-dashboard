import pytest
from flask import Flask, g

import db


def _app(tmp_path):
    app = Flask(__name__)
    app.config["DB_PATH"] = str(tmp_path / "t.db")
    with app.app_context():
        db.init(db.get_conn())
    return app


def test_query_rejects_sql_without_org_id_placeholder(tmp_path):
    with _app(tmp_path).test_request_context():
        g.org_id = 1
        with pytest.raises(ValueError):
            db.query("SELECT * FROM invoices")


def test_query_rejects_caller_supplied_org_id(tmp_path):
    with _app(tmp_path).test_request_context():
        g.org_id = 1
        with pytest.raises(ValueError):
            db.query("SELECT * FROM invoices WHERE org_id = :org_id", org_id=2)


def test_query_binds_org_id_from_g(tmp_path):
    app = _app(tmp_path)
    with app.app_context():
        conn = db.get_conn()
        conn.execute("INSERT INTO organizations (id, name, fiscal_year_end) VALUES (1, 'A', '12-31')")
        conn.execute("INSERT INTO organizations (id, name, fiscal_year_end) VALUES (2, 'B', '12-31')")
        conn.execute(
            "INSERT INTO invoices (org_id, number, counterparty, issued_on, amount_cents, status)"
            " VALUES (2, 'B-1', 'Someone', '2026-01-01', 100, 'paid')"
        )
        conn.commit()
    with app.test_request_context():
        g.org_id = 1
        assert db.query("SELECT id FROM invoices WHERE org_id = :org_id") == []


def test_query_without_org_id_in_g_raises(tmp_path):
    """A public endpoint that reaches for tenant data must fail, not leak."""
    with _app(tmp_path).test_request_context():
        # match= matters: a bare pytest.raises(AttributeError) also passes when
        # db.query itself is missing, which is a vacuous pass in a security test.
        with pytest.raises(AttributeError, match="org_id"):
            db.query("SELECT id FROM invoices WHERE org_id = :org_id")
