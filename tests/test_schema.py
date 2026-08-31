import sqlite3

import db

IDENTITY_TABLES = {"organizations", "users"}


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def test_schema_creates_expected_tables(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init(conn)
    assert set(_tables(conn)) == {"organizations", "users", "monthly_figures", "invoices"}


def test_every_tenant_table_has_org_id_not_null(tmp_path):
    """Fails if someone adds a table without a tenant column."""
    conn = db.connect(str(tmp_path / "t.db"))
    db.init(conn)
    for table in _tables(conn):
        if table in IDENTITY_TABLES:
            continue
        cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "org_id" in cols, f"{table} has no org_id column"
        assert cols["org_id"][3] == 1, f"{table}.org_id must be NOT NULL"


def test_foreign_keys_are_enforced(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init(conn)
    try:
        conn.execute(
            "INSERT INTO invoices (org_id, number, counterparty, issued_on, amount_cents, status)"
            " VALUES (999, 'X-1', 'Nobody', '2026-01-01', 100, 'paid')"
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError("foreign keys are not being enforced; check PRAGMA foreign_keys")
