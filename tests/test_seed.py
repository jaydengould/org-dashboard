import db
import seed


def test_seed_creates_three_orgs_with_disjoint_data(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 3
    for (org_id,) in conn.execute("SELECT id FROM organizations"):
        months = conn.execute(
            "SELECT count(*) FROM monthly_figures WHERE org_id = ?", (org_id,)
        ).fetchone()[0]
        invoices = conn.execute(
            "SELECT count(*) FROM invoices WHERE org_id = ?", (org_id,)
        ).fetchone()[0]
        assert months == 12
        assert invoices >= 6


def test_seed_is_idempotent(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    seed.build(path)
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 3


def test_no_tenant_row_has_a_null_or_foreign_org(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    conn = db.connect(path)
    for table in ("monthly_figures", "invoices"):
        orphans = conn.execute(
            f"SELECT count(*) FROM {table} t"
            " LEFT JOIN organizations o ON o.id = t.org_id"
            " WHERE t.org_id IS NULL OR o.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0, table


def test_invoice_numbers_are_visibly_distinct_per_org(tmp_path):
    """If two orgs' data looked alike, a leak could pass a visual check."""
    path = str(tmp_path / "t.db")
    seed.build(path)
    conn = db.connect(path)
    prefixes = {
        row[0]
        for row in conn.execute("SELECT DISTINCT substr(number, 1, 3) FROM invoices")
    }
    assert prefixes == {"AF-", "CH-", "MF-"}
