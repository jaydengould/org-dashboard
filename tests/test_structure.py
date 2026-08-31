import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL_IS_ALLOWED_IN = {"db.py", "seed.py"}


def test_no_sql_outside_the_db_module():
    """All tenant reads go through db.query. This test enforces that."""
    offenders = []
    for path in ROOT.glob("*.py"):
        if path.name in SQL_IS_ALLOWED_IN:
            continue
        source = path.read_text()
        if ".execute(" in source or "sqlite3.connect" in source:
            offenders.append(path.name)
    assert not offenders, f"raw SQL outside db.py/seed.py: {offenders}"


def test_public_endpoints_list_is_small_and_explicit():
    """A growing public list is the most likely way this app springs a leak."""
    import app

    assert app.PUBLIC_ENDPOINTS == {"login", "healthz", "static", "index"}
