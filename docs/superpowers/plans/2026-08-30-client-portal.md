# Client Portal Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployed Flask client portal where three seeded organizations each log in and see only their own financial figures, with the tenant boundary enforced at two named chokepoints and proven by a test suite.

**Architecture:** Authentication is default-deny via a single `before_request` gate keyed off a small `PUBLIC_ENDPOINTS` set. Tenant scoping is enforced by a single `db.query()` helper that refuses SQL lacking a `:org_id` placeholder and binds that value itself from `flask.g` — never from request input. The session holds one integer (`user_id`); org identity is looked up server-side on every request.

**Tech Stack:** Python 3.12, Flask 3.x, SQLite (stdlib `sqlite3`), Jinja2, Werkzeug password hashing, gunicorn, pytest. No ORM, no JS framework, no CSS framework, no chart library.

**Spec:** `SPEC.md` (repo root)

## Global Constraints

- Python 3.12.1 locally; `PYTHON_VERSION=3.12.1` on Render.
- Dependencies limited to: `Flask`, `gunicorn`, `pytest`. Nothing else. No Flask-WTF, no Flask-Login, no SQLAlchemy, no chart library.
- Money is stored and computed as **integer cents**. Floats never touch a monetary value, including in display formatting.
- Every table except `organizations` and `users` has `org_id INTEGER NOT NULL REFERENCES organizations(id)`.
- No route anywhere accepts an organization identifier in the URL, form body, header, or cookie.
- Application code reads tenant tables **only** through `db.query()`. Raw SQL is permitted in `db.py` and `seed.py` only.
- All seed organization names, counterparty names and email addresses are fictional. Emails use the RFC 2606 reserved `.test` TLD.
- Server-rendered Jinja only. Vanilla JS only if genuinely needed (it is not needed anywhere in this plan).
- Cross-tenant row access returns **404**, with a body byte-identical to a nonexistent-row 404.
- Commit after every task.

---

### Task 1: Schema and database connection

**Files:**
- Create: `schema.sql`
- Create: `db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_schema.py`
- Create: `.gitignore`
- Create: `requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: `db.connect(path) -> sqlite3.Connection`, `db.init(conn) -> None` (executes `schema.sql`), `db.get_conn() -> sqlite3.Connection` (request-scoped, reads `current_app.config["DB_PATH"]`), `db.close_conn(exc=None) -> None`. Table names `organizations`, `users`, `monthly_figures`, `invoices`.

- [ ] **Step 1: Create `requirements.txt` and `.gitignore`**

`requirements.txt`:
```
Flask==3.0.3
gunicorn==22.0.0
pytest==8.2.2
```

`.gitignore`:
```
__pycache__/
*.pyc
.pytest_cache/
portal.db
.venv/
```

- [ ] **Step 2: Create a virtualenv and install**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 3: Write `schema.sql`**

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE organizations (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    fiscal_year_end TEXT NOT NULL
);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    org_id        INTEGER NOT NULL REFERENCES organizations(id),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE monthly_figures (
    id             INTEGER PRIMARY KEY,
    org_id         INTEGER NOT NULL REFERENCES organizations(id),
    month          TEXT NOT NULL,
    revenue_cents  INTEGER NOT NULL,
    expenses_cents INTEGER NOT NULL,
    UNIQUE (org_id, month)
);

CREATE TABLE invoices (
    id           INTEGER PRIMARY KEY,
    org_id       INTEGER NOT NULL REFERENCES organizations(id),
    number       TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    issued_on    TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('paid', 'outstanding', 'overdue')),
    UNIQUE (org_id, number)
);

CREATE INDEX idx_figures_org_month ON monthly_figures (org_id, month);
CREATE INDEX idx_invoices_org_id   ON invoices (org_id, id);
```

- [ ] **Step 4: Write the failing schema test**

`tests/test_schema.py`:
```python
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
```

`tests/conftest.py` (root on `sys.path` so `import db` works):
```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
```

- [ ] **Step 5: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 6: Write `db.py` (connection half only)**

```python
"""All SQL in this application lives in this file (plus seed.py).

Two chokepoints protect tenant data:
  * app.py's before_request gate decides *who* you are.
  * query() below decides *what you can see*: it refuses any SQL that does not
    filter on :org_id, and binds that value from the session-derived g.org_id.
"""

import pathlib
import sqlite3

from flask import current_app, g

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / "schema.sql"


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # SQLite ships with foreign key enforcement OFF. Without this the REFERENCES
    # clauses in schema.sql are decorative.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def get_conn():
    if "conn" not in g:
        g.conn = connect(current_app.config["DB_PATH"])
    return g.conn


def close_conn(exc=None):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()
```

- [ ] **Step 7: Run the tests and verify they pass**

Run: `.venv/bin/pytest tests/test_schema.py -v`
Expected: 3 passed

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .gitignore schema.sql db.py tests/
git commit -m "feat: schema and sqlite connection with FK enforcement"
```

---

### Task 2: Deploy a skeleton to Render

Deployment is the highest-variance unknown in this project and the one you have
never done. It gets retired now, on a skeleton that serves one string, while
there is still a whole day left — not at the end, where a `$PORT` binding or a
Python version mismatch turns into a lost evening. Every later task ships to a
URL that already works.

**Files:**
- Create: `app.py`
- Create: `render.yaml`
- Create: `.python-version`
- Create: `tests/test_app_config.py`

**Interfaces:**
- Consumes: `db.close_conn` from Task 1.
- Produces: `app.create_app(db_path=None, secret_key=None) -> Flask`, endpoint
  `healthz` (`GET /healthz`). Config keys `DB_PATH`, `SECRET_KEY`,
  `SESSION_COOKIE_SECURE`, `PERMANENT_SESSION_LIFETIME`. Later tasks add routes
  to this same factory.

- [ ] **Step 1: Write the failing config test**

`tests/test_app_config.py`:
```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_app_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write the skeleton `app.py`**

Routes arrive in Tasks 5–10. This file grows; the factory signature does not.

```python
import os
from datetime import timedelta

from flask import Flask

import db


def create_app(db_path=None, secret_key=None):
    app = Flask(__name__)

    key = secret_key or os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set; refusing to start")

    app.config.update(
        DB_PATH=db_path or os.environ.get("DB_PATH", "portal.db"),
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Render terminates TLS; local dev and the test client speak http, so
        # this is gated on the environment rather than hardcoded True.
        SESSION_COOKIE_SECURE=os.environ.get("PORTAL_ENV") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.teardown_appcontext(db.close_conn)

    @app.route("/healthz")
    def healthz():
        return "ok"

    return app
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 6 passed

- [ ] **Step 5: Pin the Python version**

`.python-version`:
```
3.12.1
```

- [ ] **Step 6: Write `render.yaml`**

There is no seed step in `startCommand` yet — `seed.py` does not exist until
Task 4, which adds it. `--workers 1` because SQLite on a single ephemeral file
does not want concurrent writers, and this app never writes at runtime.

```yaml
services:
  - type: web
    name: client-portal-demo
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn "app:create_app()" --bind 0.0.0.0:$PORT --workers 1
    healthCheckPath: /healthz
    envVars:
      - key: SECRET_KEY
        generateValue: true
      - key: PORTAL_ENV
        value: production
      - key: PYTHON_VERSION
        value: 3.12.1
      - key: DB_PATH
        value: /tmp/portal.db
```

- [ ] **Step 7: Run the production command locally before trusting it to Render**

```bash
SECRET_KEY=local-test DB_PATH=/tmp/portal.db \
  .venv/bin/gunicorn "app:create_app()" --bind 127.0.0.1:8000 --workers 1
```
Then `curl -s localhost:8000/healthz` → `ok`.

`PORTAL_ENV=production` is deliberately **not** set on that local line. With it
set, the session cookie is marked `Secure` and will not be sent over plain
http, so local sign-in would silently appear broken later. On Render, where TLS
terminates at the edge, it must be set. This is the single most common way this
deployment wastes an hour.

- [ ] **Step 8: Commit and deploy**

```bash
git add app.py render.yaml .python-version tests/test_app_config.py
git commit -m "feat: app factory and render deployment skeleton"
git push
```
Create the Render service from the repository as a Blueprint (`render.yaml`).
Confirm in the dashboard that `SECRET_KEY` was generated and `PORTAL_ENV` is
`production`.

- [ ] **Step 9: Verify the public URL**

`curl -s https://<your-service>.onrender.com/healthz` → `ok`.

**Gate 1 is now closed.** Do not continue until that curl returns `ok`. If the
build fails, the fix is here, in a four-line file, and not tangled up with
authentication code. Record the public URL — Task 11's README needs it.

---

### Task 3: The tenant scoping helper

**Files:**
- Modify: `db.py` (append)
- Create: `tests/test_query_guard.py`

**Interfaces:**
- Consumes: `db.get_conn()` from Task 1.
- Produces:
  - `db.query(sql: str, **params) -> list[sqlite3.Row]` — the only tenant-table accessor. Raises `ValueError` if `sql` lacks the literal `:org_id`, or if a caller passes `org_id` as a parameter.
  - `db.get_user(user_id) -> sqlite3.Row | None` — columns `id, org_id, email`.
  - `db.get_user_by_email(email) -> sqlite3.Row | None` — columns `id, password_hash`.
  - `db.get_org(org_id) -> sqlite3.Row | None` — columns `id, name, fiscal_year_end`.

- [ ] **Step 1: Write the failing guard test**

`tests/test_query_guard.py`:
```python
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
        with pytest.raises(AttributeError):
            db.query("SELECT id FROM invoices WHERE org_id = :org_id")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_query_guard.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'query'`

- [ ] **Step 3: Append the helper and the three identity lookups to `db.py`**

```python
def query(sql, **params):
    """The only way application code reads tenant tables.

    Refuses SQL with no tenant predicate, and binds :org_id itself from the
    session-derived g.org_id so that no request input can reach it.
    """
    if ":org_id" not in sql:
        raise ValueError(
            "unscoped query: SQL passed to db.query must filter on org_id = :org_id"
        )
    if "org_id" in params:
        raise ValueError("org_id comes from the session, not from the caller")
    params["org_id"] = g.org_id  # AttributeError here means no authenticated org: fail closed
    return get_conn().execute(sql, params).fetchall()


# --- Identity lookups -------------------------------------------------------
# These three deliberately bypass query(). They run *before* an org is known
# (get_user, get_user_by_email) or read the organizations table itself
# (get_org), so there is no org_id to scope by. They are the only unscoped
# reads in the application and they never touch a tenant table.

def get_user(user_id):
    return get_conn().execute(
        "SELECT id, org_id, email FROM users WHERE id = :id", {"id": user_id}
    ).fetchone()


def get_user_by_email(email):
    return get_conn().execute(
        "SELECT id, password_hash FROM users WHERE email = :email", {"email": email}
    ).fetchone()


def get_org(org_id):
    return get_conn().execute(
        "SELECT id, name, fiscal_year_end FROM organizations WHERE id = :id",
        {"id": org_id},
    ).fetchone()
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_query_guard.py
git commit -m "feat: db.query refuses unscoped SQL and binds org_id from session"
```

---

### Task 4: Seed data for three fictional organizations

**Files:**
- Create: `seed.py`
- Create: `tests/test_seed.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init` from Task 1.
- Produces: `seed.build(path) -> None` — idempotent; creates the schema and inserts data only if `organizations` is empty. `seed.CREDENTIALS: list[tuple[str, str]]` of `(email, password)` for the README and tests.

- [ ] **Step 1: Write the failing seed test**

`tests/test_seed.py`:
```python
import db
import seed


def test_seed_creates_three_orgs_with_disjoint_data(tmp_path):
    path = str(tmp_path / "t.db")
    seed.build(path)
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM organizations").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 3
    # every org has a full year of figures and at least six invoices
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seed'`

- [ ] **Step 3: Write `seed.py`**

All names are invented. Emails use `.test`, an RFC 2606 reserved TLD that can
never resolve, so nothing can accidentally send mail anywhere.

```python
"""Seed the demo database with entirely fictional organizations.

Run directly:  python seed.py
Idempotent: does nothing if the organizations table is already populated.
"""

import os
import sys

from werkzeug.security import generate_password_hash

import db

MONTHS = [
    "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
    "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
]

# (name, fiscal_year_end, email, password, invoice_prefix,
#  revenue_dollars_by_month, expenses_dollars_by_month)
ORGS = [
    (
        "Alder & Finch Bookbinding", "03-31",
        "owner@alderfinch.test", "alder-demo-2026", "AF-",
        [41200, 46800, 78400, 91500, 38900, 40100,
         44600, 47200, 45800, 43300, 39700, 42500],
        [35900, 39400, 62100, 71800, 34200, 35600,
         38800, 40900, 39600, 38100, 35400, 37200],
    ),
    (
        "Cobalt Harbour Logistics", "12-31",
        "owner@cobaltharbour.test", "cobalt-demo-2026", "CH-",
        [612000, 489000, 731000, 902000, 410000, 528000,
         664000, 587000, 713000, 845000, 476000, 690000],
        [548000, 462000, 623000, 742000, 401000, 470000,
         571000, 519000, 604000, 688000, 438000, 578000],
    ),
    (
        "Meridian Fern Wellness", "06-30",
        "owner@meridianfern.test", "meridian-demo-2026", "MF-",
        [61400, 64200, 67900, 71100, 74800, 78200,
         82600, 86300, 90100, 95400, 101200, 108700],
        [43800, 45100, 47600, 49300, 51900, 54100,
         57200, 59400, 61800, 64900, 68300, 72400],
    ),
]

# (prefix_index, number_suffix, counterparty, issued_on, amount_cents, status)
# One non-round amount per org, so the cents path is exercised by real data.
INVOICES = [
    (0, "0141", "Quillmark Stationers", "2026-03-04", 812450, "paid"),
    (0, "0142", "Verdant Press Co-op", "2026-04-11", 1240000, "paid"),
    (0, "0143", "Hollowbrook Archive", "2026-05-02", 356000, "outstanding"),
    (0, "0144", "Pemberton Rare Books", "2026-06-19", 978300, "outstanding"),
    (0, "0145", "Saltmarsh Bindery Supply", "2026-07-08", 214000, "overdue"),
    (0, "0146", "Tolliver Paper Mill", "2026-08-01", 655000, "outstanding"),
    (1, "2207", "Drayfield Container Line", "2026-02-27", 18450075, "paid"),
    (1, "2208", "Northgate Freight Union", "2026-03-30", 9820000, "paid"),
    (1, "2209", "Pellowe Cold Storage", "2026-05-14", 27300000, "outstanding"),
    (1, "2210", "Kestrel Bay Terminals", "2026-06-06", 14150000, "outstanding"),
    (1, "2211", "Ashgrove Haulage Group", "2026-07-21", 6740000, "overdue"),
    (1, "2212", "Larkin Shoreline Depot", "2026-08-12", 21980000, "outstanding"),
    (1, "2213", "Windward Crate Services", "2026-08-25", 8305000, "outstanding"),
    (2, "0518", "Bramblewood Retreat", "2026-03-12", 428900, "paid"),
    (2, "0519", "Cedar Lantern Studio", "2026-04-23", 316000, "paid"),
    (2, "0520", "Halcyon Fields Clinic", "2026-05-29", 592000, "outstanding"),
    (2, "0521", "Rowan Terrace Spa", "2026-06-30", 274500, "outstanding"),
    (2, "0522", "Juniper Hollow Wellness", "2026-07-17", 461000, "overdue"),
    (2, "0523", "Marisol Grove Therapy", "2026-08-09", 733200, "outstanding"),
]

CREDENTIALS = [(org[2], org[3]) for org in ORGS]


def build(path):
    conn = db.connect(path)
    existing = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'organizations'"
    ).fetchone()[0]
    if not existing:
        db.init(conn)
    if conn.execute("SELECT count(*) FROM organizations").fetchone()[0]:
        return

    org_ids = []
    for name, fye, email, password, _prefix, revenue, expenses in ORGS:
        cur = conn.execute(
            "INSERT INTO organizations (name, fiscal_year_end) VALUES (?, ?)",
            (name, fye),
        )
        org_id = cur.lastrowid
        org_ids.append(org_id)
        conn.execute(
            "INSERT INTO users (org_id, email, password_hash) VALUES (?, ?, ?)",
            (org_id, email, generate_password_hash(password)),
        )
        for month, rev, exp in zip(MONTHS, revenue, expenses):
            conn.execute(
                "INSERT INTO monthly_figures (org_id, month, revenue_cents, expenses_cents)"
                " VALUES (?, ?, ?, ?)",
                (org_id, month, rev * 100, exp * 100),
            )

    for idx, suffix, counterparty, issued_on, amount_cents, status in INVOICES:
        conn.execute(
            "INSERT INTO invoices (org_id, number, counterparty, issued_on, amount_cents, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (org_ids[idx], ORGS[idx][4] + suffix, counterparty, issued_on, amount_cents, status),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "portal.db")
    build(target)
    print(f"seeded {target}")
    for email, password in CREDENTIALS:
        print(f"  {email} / {password}")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 14 passed

- [ ] **Step 5: Build the real database by hand and eyeball it**

```bash
.venv/bin/python seed.py portal.db
sqlite3 portal.db "SELECT o.name, count(*) FROM invoices i JOIN organizations o ON o.id = i.org_id GROUP BY o.name;"
```
Expected: three rows, counts 6 / 7 / 6.

- [ ] **Step 6: Add the seed step to `render.yaml` and redeploy**

`seed.py` now exists, so the deployed instance can rebuild its database on every
boot. This is what makes Render's ephemeral filesystem survivable. Change
`startCommand` to:

```yaml
    startCommand: python seed.py && gunicorn "app:create_app()" --bind 0.0.0.0:$PORT --workers 1
```

- [ ] **Step 7: Commit and push**

```bash
git add seed.py tests/test_seed.py render.yaml
git commit -m "feat: seed three fictional organizations with a year of figures"
git push
```
Wait for the redeploy, then `curl -s https://<your-service>.onrender.com/healthz`
→ `ok`. A failure here means the seed step broke boot; fix it now, while the app
has no routes to confuse the diagnosis.

---

### Task 5: Login end to end with a default-deny gate

This is the milestone from `SPEC.md` §7 step 1: a working login before any
dashboard exists. The `before_request` gate is built here, not retrofitted
later — it is the architecture.

**Files:**
- Modify: `app.py` — grows from the Task 2 skeleton. The full file after this
  task is given in Step 3; paste it over the skeleton rather than diffing.
- Create: `templates/base.html`
- Create: `templates/login.html`
- Create: `static/style.css`
- Create: `tests/test_auth.py`

**Interfaces:**
- Consumes: `db.get_user`, `db.get_user_by_email`, `db.get_org`, `db.close_conn`, `seed.CREDENTIALS`.
- Produces:
  - `app.create_app(db_path=None, secret_key=None) -> Flask`
  - `app.PUBLIC_ENDPOINTS: set[str]` — `{"login", "healthz", "static", "index"}`
  - Endpoints: `index` (`/`), `login` (`/login`), `healthz` (`/healthz`), `dashboard` (`/dashboard`).
  - Request globals set by the gate: `g.user` (row), `g.org_id` (int), `g.org` (row).

- [ ] **Step 1: Write the failing auth test**

`tests/test_auth.py`:
```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Grow `app.py` into the full login app**

```python
import os
from datetime import timedelta

from flask import (
    Flask, abort, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db

# Endpoints reachable without a session. Everything not listed here is
# protected by require_login below. Adding a route makes it protected by
# default; making it public requires editing this line, in this file, where a
# reviewer will see it.
PUBLIC_ENDPOINTS = {"login", "healthz", "static", "index"}

# Compared against when the email is unknown, so that a wrong password and a
# nonexistent account take the same amount of time.
_DUMMY_HASH = generate_password_hash("this-account-does-not-exist")


def money(cents):
    """Format integer cents. No floats: this is an accounting demo."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def create_app(db_path=None, secret_key=None):
    app = Flask(__name__)

    key = secret_key or os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set; refusing to start")

    app.config.update(
        DB_PATH=db_path or os.environ.get("DB_PATH", "portal.db"),
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Render terminates TLS; local dev and the test client speak http.
        SESSION_COOKIE_SECURE=os.environ.get("PORTAL_ENV") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.teardown_appcontext(db.close_conn)
    app.jinja_env.filters["money"] = money

    @app.before_request
    def require_login():
        """Chokepoint 1 of 2: authentication. Default deny."""
        g.user = None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("login"))
        user = db.get_user(user_id)
        if user is None:  # account deleted underneath a live session
            session.clear()
            return redirect(url_for("login"))
        g.user = user
        g.org_id = user["org_id"]
        g.org = db.get_org(user["org_id"])
        return None

    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if session.get("user_id") else "login"))

    @app.route("/healthz")
    def healthz():
        return "ok"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("user_id"):
                return redirect(url_for("dashboard"))
            return render_template("login.html")

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        row = db.get_user_by_email(email)
        if row is None:
            check_password_hash(_DUMMY_HASH, password)
            return render_template("login.html", error="Incorrect email or password.")
        if not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Incorrect email or password.")

        session.clear()  # no session fixation: a fresh session on every login
        session["user_id"] = row["id"]
        session.permanent = True
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html")

    return app
```

Note: emails are lower-cased on both insert and lookup, so `seed.py`'s
addresses must already be lowercase (they are).

- [ ] **Step 4: Write the templates**

`templates/base.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Client Portal{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header>
    <span class="brand">Client Portal</span>
    {% if g.user %}<span class="org">{{ g.org.name }}</span>{% endif %}
  </header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`templates/login.html`:
```html
{% extends "base.html" %}
{% block title %}Sign in{% endblock %}
{% block content %}
<h1>Sign in</h1>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="{{ url_for('login') }}">
  <label>Email <input type="email" name="email" required autofocus></label>
  <label>Password <input type="password" name="password" required></label>
  <button type="submit">Sign in</button>
</form>
{% endblock %}
```

`templates/dashboard.html` (placeholder for now, replaced in Task 7):
```html
{% extends "base.html" %}
{% block content %}<h1>{{ g.org.name }}</h1>{% endblock %}
```

`static/style.css` — deliberately small. Readable type, right-aligned money.
```css
:root { --ink: #1c1c1c; --rule: #d8d4cc; --bg: #faf9f6; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.5 ui-sans-serif, system-ui, "Helvetica Neue", sans-serif; }
header { display: flex; justify-content: space-between; align-items: baseline;
         padding: 12px 24px; border-bottom: 1px solid var(--rule); }
.brand { font-weight: 600; }
.org { color: #5a5a5a; }
main { max-width: 900px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 20px; }
label { display: block; margin-bottom: 12px; }
input { display: block; width: 260px; padding: 6px; margin-top: 4px;
        border: 1px solid var(--rule); border-radius: 3px; }
button { padding: 7px 16px; border: 1px solid var(--ink); background: var(--ink);
         color: #fff; border-radius: 3px; cursor: pointer; }
.error { color: #a3352a; }
table { border-collapse: collapse; width: 100%; margin-bottom: 28px; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--rule); text-align: left; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.tiles { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }
.tile { border: 1px solid var(--rule); border-radius: 4px; padding: 12px 16px; min-width: 170px; }
.tile .label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #5a5a5a; }
.tile .value { font-size: 20px; font-variant-numeric: tabular-nums; }
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 21 passed

- [ ] **Step 6: Verify by hand in a browser**

```bash
SECRET_KEY=dev-only DB_PATH=portal.db .venv/bin/flask --app "app:create_app()" run
```
Check all four: `/dashboard` in a private window bounces to `/login`; a wrong
password shows the generic error; a correct login lands on the org name; `/`
redirects correctly both signed in and out.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/ static/ tests/test_auth.py
git commit -m "feat: session login with default-deny before_request gate"
```

---

### Task 6: Logout and CSRF protection

**Files:**
- Modify: `app.py`
- Modify: `templates/base.html`
- Create: `tests/test_csrf.py`
- Modify: `tests/test_auth.py` (add a CSRF-token login helper)

**Interfaces:**
- Consumes: everything from Task 5.
- Produces:
  - Endpoint `logout` (`POST /logout`).
  - Jinja global `csrf_token()` returning the per-session token.
  - `tests/helpers.py`: `login(client, email, password) -> Response` which fetches
    the token from `GET /login` and posts it. Every later test uses this.

- [ ] **Step 1: Write the failing CSRF and logout tests**

`tests/helpers.py`:
```python
import re

TOKEN_RE = re.compile(rb'name="csrf_token" value="([^"]+)"')


def csrf_token(client, path="/login"):
    match = TOKEN_RE.search(client.get(path).data)
    assert match, f"no csrf_token field found on {path}"
    return match.group(1).decode()


def login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_token(client)},
    )
```

`tests/test_csrf.py`:
```python
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
    resp = client.post("/login", data={"email": ALDER_EMAIL, "password": ALDER_PW,
                                       "csrf_token": ""})
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
```

Add to `tests/conftest.py` so `from helpers import ...` resolves:
```python
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_csrf.py -v`
Expected: FAIL — the no-token POST returns 302, not 400.

- [ ] **Step 3: Add CSRF and logout to `app.py`**

Add the imports `import hmac`, `import secrets` at the top. Inside
`create_app`, register the CSRF check **before** `require_login` so it also
covers the public `POST /login`:

```python
    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        stored = session.get("csrf_token")
        sent = request.form.get("csrf_token", "")
        # `stored` must be truthy: hmac.compare_digest("", "") is True, which
        # would let a request with no token through when the session has none.
        if not stored or not hmac.compare_digest(sent, stored):
            abort(400)
        return None
```

Register order matters — `check_csrf` must be defined above `require_login` in
the function body. And the logout route:

```python
    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))
```

- [ ] **Step 4: Add the logout form to `templates/base.html`**

Replace the `{% if g.user %}` line in the header with:
```html
    {% if g.user %}
      <span class="org">{{ g.org.name }}</span>
      <form method="post" action="{{ url_for('logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit">Sign out</button>
      </form>
    {% endif %}
```

And add the hidden field to `templates/login.html`'s form:
```html
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

- [ ] **Step 5: Update `tests/test_auth.py` to use the helper**

Replace every bare `anon.post("/login", data={...})` with
`login(anon, EMAIL, PW)` from `helpers`, importing it at the top. The
"indistinguishable failures" test needs both posts to carry a valid token:

```python
def test_wrong_password_and_unknown_email_are_indistinguishable(anon):
    bad_pw = login(anon, ALDER_EMAIL, "wrong")
    unknown = login(anon, "nobody@nowhere.test", "wrong")
    assert bad_pw.status_code == unknown.status_code == 200
    assert bad_pw.data == unknown.data
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: 27 passed

- [ ] **Step 7: Commit**

```bash
git add app.py templates/ tests/
git commit -m "feat: POST logout and per-session CSRF tokens"
```

---

### Task 7: Dashboard tiles and figures table

**Files:**
- Modify: `app.py` (the `dashboard` view)
- Modify: `templates/dashboard.html`
- Create: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `db.query` (Task 3), the `money` filter (Task 5).
- Produces: `dashboard.html` context — `figures` (list of rows: `month`,
  `revenue_cents`, `expenses_cents`), `invoices` (list of rows: `id`, `number`,
  `counterparty`, `issued_on`, `amount_cents`, `status`), and the integers
  `total_revenue`, `total_expenses`, `net`, `outstanding`.

- [ ] **Step 1: Write the failing dashboard test**

`tests/test_dashboard.py`:
```python
import pytest

import app as app_module
import db
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL — the placeholder dashboard has no months or totals.

- [ ] **Step 3: Replace the `dashboard` view in `app.py`**

The two queries below are the entire tenant-data surface of the application.
Both carry `org_id = :org_id`; `db.query` supplies the value.

```python
    @app.route("/dashboard")
    def dashboard():
        figures = db.query(
            "SELECT month, revenue_cents, expenses_cents FROM monthly_figures"
            " WHERE org_id = :org_id ORDER BY month"
        )
        invoices = db.query(
            "SELECT id, number, counterparty, issued_on, amount_cents, status"
            " FROM invoices WHERE org_id = :org_id ORDER BY issued_on DESC"
        )
        total_revenue = sum(f["revenue_cents"] for f in figures)
        total_expenses = sum(f["expenses_cents"] for f in figures)
        outstanding = sum(
            i["amount_cents"] for i in invoices if i["status"] in ("outstanding", "overdue")
        )
        return render_template(
            "dashboard.html",
            figures=figures,
            invoices=invoices,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net=total_revenue - total_expenses,
            outstanding=outstanding,
        )
```

- [ ] **Step 4: Replace `templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}{{ g.org.name }}{% endblock %}
{% block content %}
<h1>{{ g.org.name }}</h1>
<p>Financial year ends {{ g.org.fiscal_year_end }}. Figures cover the last twelve months.</p>

<div class="tiles">
  <div class="tile"><div class="label">Revenue</div><div class="value">{{ total_revenue | money }}</div></div>
  <div class="tile"><div class="label">Expenses</div><div class="value">{{ total_expenses | money }}</div></div>
  <div class="tile"><div class="label">Net</div><div class="value">{{ net | money }}</div></div>
  <div class="tile"><div class="label">Unpaid invoices</div><div class="value">{{ outstanding | money }}</div></div>
</div>

<h2>Monthly figures</h2>
<table>
  <thead><tr><th>Month</th><th class="num">Revenue</th><th class="num">Expenses</th><th class="num">Net</th></tr></thead>
  <tbody>
  {% for f in figures %}
    <tr>
      <td>{{ f.month }}</td>
      <td class="num">{{ f.revenue_cents | money }}</td>
      <td class="num">{{ f.expenses_cents | money }}</td>
      <td class="num">{{ (f.revenue_cents - f.expenses_cents) | money }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<h2>Invoices</h2>
<table>
  <thead><tr><th>Number</th><th>Counterparty</th><th>Issued</th><th class="num">Amount</th><th>Status</th></tr></thead>
  <tbody>
  {% for i in invoices %}
    <tr>
      <td><a href="{{ url_for('invoice_detail', invoice_id=i.id) }}">{{ i.number }}</a></td>
      <td>{{ i.counterparty }}</td>
      <td>{{ i.issued_on }}</td>
      <td class="num">{{ i.amount_cents | money }}</td>
      <td>{{ i.status }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

The `url_for('invoice_detail', ...)` reference requires Task 8. Do Task 8's
Step 3 route stub in the same working session, or temporarily render the number
as plain text and restore the link in Task 8.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 32 passed

- [ ] **Step 6: Verify all three orgs by hand**

Sign in as each of the three in three private windows. Confirm the totals differ
by an order of magnitude between Alder & Finch and Cobalt Harbour, and that the
header org name is correct each time.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/dashboard.html tests/test_dashboard.py
git commit -m "feat: dashboard with scoped totals, figures table and invoice list"
```

---

### Task 8: Invoice detail and the isolation suite

The centrepiece. `SPEC.md` §5 tests 4, 5, 6 live here.

**Files:**
- Modify: `app.py` (add `invoice_detail`)
- Create: `templates/invoice.html`
- Create: `tests/test_isolation.py`

**Interfaces:**
- Consumes: `db.query`, `app.PUBLIC_ENDPOINTS`.
- Produces: endpoint `invoice_detail` at `GET /invoices/<int:invoice_id>` —
  200 for an invoice belonging to `g.org_id`, 404 for any other id.

- [ ] **Step 1: Write the failing isolation tests**

`tests/test_isolation.py`:
```python
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
    for foreign in _invoice_ids(app, B_EMAIL):
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
    assert replay.get("/dashboard").status_code == 200        # cookie is valid
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
```

Note: `get_cookie` / `set_cookie(key, value)` require Werkzeug ≥ 2.3, which
Flask 3.0.3 pins. If a test errors on those, check the installed Werkzeug
version before changing the test.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.venv/bin/pytest tests/test_isolation.py -v`
Expected: FAIL — `werkzeug.routing.exceptions.BuildError: invoice_detail`

- [ ] **Step 3: Add the route to `app.py`**

```python
    @app.route("/invoices/<int:invoice_id>")
    def invoice_detail(invoice_id):
        rows = db.query(
            "SELECT id, number, counterparty, issued_on, amount_cents, status"
            " FROM invoices WHERE org_id = :org_id AND id = :invoice_id",
            invoice_id=invoice_id,
        )
        if not rows:
            # 404, not 403: a 403 would confirm that another org's invoice
            # exists. This response is identical to a genuinely missing row.
            abort(404)
        return render_template("invoice.html", invoice=rows[0])
```

Do **not** add a custom 404 error handler. Flask's default 404 page carries no
session or org context, which is exactly what makes the two 404s identical.

- [ ] **Step 4: Write `templates/invoice.html`**

```html
{% extends "base.html" %}
{% block title %}{{ invoice.number }}{% endblock %}
{% block content %}
<h1>Invoice {{ invoice.number }}</h1>
<table>
  <tr><th>Counterparty</th><td>{{ invoice.counterparty }}</td></tr>
  <tr><th>Issued</th><td>{{ invoice.issued_on }}</td></tr>
  <tr><th>Amount</th><td class="num">{{ invoice.amount_cents | money }}</td></tr>
  <tr><th>Status</th><td>{{ invoice.status }}</td></tr>
</table>
<p><a href="{{ url_for('dashboard') }}">Back to dashboard</a></p>
{% endblock %}
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 39 passed

- [ ] **Step 6: Reproduce the attack by hand, in a browser**

Sign in as Alder & Finch. Note an invoice id from the URL of one of your own
invoices. Sign in as Cobalt Harbour in a second private window, note one of
theirs. Now paste Cobalt Harbour's id into the Alder & Finch window. You must
get a 404. Do this manually even though the test covers it — you need to have
seen it with your own eyes to describe it in an interview.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/invoice.html tests/test_isolation.py
git commit -m "feat: invoice detail scoped to the session org, with isolation suite"
```

---

### Task 9: Structural guard test, and deliberately breaking the app

**Files:**
- Create: `tests/test_structure.py`

**Interfaces:**
- Consumes: the repository layout.
- Produces: nothing importable. A test that fails if raw SQL appears outside `db.py` and `seed.py`.

- [ ] **Step 1: Write the failing structure test**

`tests/test_structure.py`:
```python
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
```

- [ ] **Step 2: Run it and verify it passes**

Run: `.venv/bin/pytest tests/test_structure.py -v`
Expected: 2 passed. (This test is written to pass immediately; its value is
future regression, not present discovery.)

- [ ] **Step 3: Break the app on purpose and watch the suite catch it**

Do all four. A test suite you have never seen fail is not evidence of anything.

1. In `app.py`'s `invoice_detail`, delete `org_id = :org_id AND ` from the SQL.
   Run `.venv/bin/pytest -v`. Expected: `db.query` raises `ValueError`, so the
   isolation tests fail. Restore.
2. Change that predicate to `org_id = :org_id OR 1=1`. Run the suite. Expected:
   `test_cannot_read_another_orgs_invoice_by_id` fails. **This is the case the
   string guard does not catch** — note it; it is the honest limit of the design
   and worth stating in an interview. Restore.
3. Add `"dashboard"` to `PUBLIC_ENDPOINTS`. Run the suite. Expected:
   `test_protected_route_redirects_anonymous_user_to_login` and
   `test_public_endpoints_list_is_small_and_explicit` fail. Restore.
4. Add `CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);` to
   `schema.sql`. Run the suite. Expected:
   `test_every_tenant_table_has_org_id_not_null` fails. Restore.

- [ ] **Step 4: Record what you saw**

Add a short `docs/breaking-it.md` listing the four mutations and which test
caught each. Four bullet points. This is the single most useful page in the repo
for a technical evaluator, and the thing to talk through in the interview.

- [ ] **Step 5: Commit**

```bash
git add tests/test_structure.py docs/breaking-it.md
git commit -m "test: structural guards plus a record of deliberate breakage"
```

---

## Gate 2: Adversarial review of the boundary

The author of a security guard is the worst possible reviewer of it. Before the
chart — before anything cosmetic — get an outside pass on the code that decides
who sees what.

- [ ] **Step 1: Run the security review over the whole diff**

In Claude Code: `/security-review`

- [ ] **Step 2: Read every finding against this list, and resolve or dismiss in writing**

Expect noise. The findings that matter for this project are:
authentication bypass, tenant scoping, session handling, CSRF, and anything
touching `db.query` or `PUBLIC_ENDPOINTS`. Anything else — logging, headers,
dependency pinning — is out of scope for a one-day demo; note it and move on.

- [ ] **Step 3: Append the outcome to `docs/breaking-it.md`**

A short section: what was flagged, what you changed, what you deliberately did
not change and why. A dismissed finding you can justify is worth as much in an
interview as a fixed one.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: record adversarial review of the tenant boundary"
```

---


### Task 10: The chart

Inline SVG generated in Jinja from the rows already on the page. No library, no
CDN, no JavaScript — so the chart is covered by the same tenant scoping as the
table, which is a better story than a JSON endpoint feeding a JS chart.

**Files:**
- Modify: `app.py` (add `chart_max` to the dashboard context)
- Modify: `templates/dashboard.html`
- Modify: `static/style.css`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `figures` from Task 7.
- Produces: `chart_max: int` in the dashboard context — the largest of all
  revenue and expense values, or `1` when there are no figures.

- [ ] **Step 1: Write the failing chart test**

Append to `tests/test_dashboard.py`:
```python
def test_dashboard_renders_a_chart_with_one_bar_pair_per_month(client_a):
    body = client_a.get("/dashboard").data.decode()
    assert "<svg" in body
    assert body.count('class="bar-revenue"') == 12
    assert body.count('class="bar-expenses"') == 12


def test_chart_does_not_require_javascript(client_a):
    body = client_a.get("/dashboard").data.decode()
    assert "<script" not in body
```

- [ ] **Step 2: Run and verify it fails**

Run: `.venv/bin/pytest tests/test_dashboard.py -k chart -v`
Expected: FAIL — no `<svg` in the body.

- [ ] **Step 3: Add `chart_max` to the dashboard view in `app.py`**

Inside `dashboard()`, after the queries:
```python
        chart_max = max(
            [f["revenue_cents"] for f in figures] + [f["expenses_cents"] for f in figures]
            or [1]
        )
```
and pass `chart_max=chart_max` to `render_template`.

- [ ] **Step 4: Add the SVG block to `templates/dashboard.html`**

Insert between the tiles and the "Monthly figures" heading:
```html
<h2>Revenue and expenses by month</h2>
<div class="chart">
  <svg viewBox="0 0 720 200" role="img"
       aria-label="Monthly revenue and expenses for {{ g.org.name }}">
    {% for f in figures %}
      {% set x = loop.index0 * 60 + 12 %}
      {% set rh = f.revenue_cents * 140 // chart_max %}
      {% set eh = f.expenses_cents * 140 // chart_max %}
      <rect class="bar-revenue"  x="{{ x }}"      y="{{ 160 - rh }}" width="22" height="{{ rh }}"></rect>
      <rect class="bar-expenses" x="{{ x + 24 }}" y="{{ 160 - eh }}" width="22" height="{{ eh }}"></rect>
      <text x="{{ x + 23 }}" y="176" text-anchor="middle">{{ f.month[-2:] }}</text>
    {% endfor %}
  </svg>
  <p class="legend"><span class="swatch revenue"></span> Revenue
     <span class="swatch expenses"></span> Expenses</p>
</div>
```

- [ ] **Step 5: Add the chart styles to `static/style.css`**

```css
.chart { margin-bottom: 28px; }
.chart svg { width: 100%; height: auto; }
.bar-revenue { fill: #2f6f4f; }
.bar-expenses { fill: #a34b3c; }
.chart text { font-size: 10px; fill: #5a5a5a; }
.legend { font-size: 13px; color: #5a5a5a; }
.swatch { display: inline-block; width: 10px; height: 10px; margin: 0 4px 0 12px; }
.swatch.revenue { background: #2f6f4f; }
.swatch.expenses { background: #a34b3c; }
```

- [ ] **Step 6: Run the whole suite and check the page**

Run: `.venv/bin/pytest -v`
Expected: 43 passed. Then load the dashboard for all three orgs and confirm the
tallest bar in each chart corresponds to the largest row in that org's table.

**Stop here.** No axis labels, no gridlines, no tooltips, no hover states. The
chart is the part of this project that looks like progress and produces none.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/dashboard.html static/style.css tests/test_dashboard.py
git commit -m "feat: server-rendered inline SVG chart, no javascript"
```

---

### Task 11: Verify production and write the README

Deployment itself was Task 2. What is left is proving the boundary holds on the
real URL and writing the one document a stranger will actually read.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: a public URL a stranger can use, and a README that does not overclaim.

- [ ] **Step 1: Push the finished app and wait for the redeploy**

```bash
git push
```
`curl -s https://<your-service>.onrender.com/healthz` → `ok`.

- [ ] **Step 2: Re-run the tampering check against production, by hand**

Sign in as Alder & Finch in one private window and as Cobalt Harbour in another.
Take a real invoice id from each. Paste Cobalt Harbour's id into the Alder &
Finch window. You must get a 404, and it must look identical to
`/invoices/999999`. The test suite already asserts this against a temp database;
this step asserts it against the thing you will hand someone a link to.

- [ ] **Step 3: Confirm the production session cookie flags**

In the browser devtools, on the deployed site, inspect the `session` cookie:
`Secure` set, `HttpOnly` set, `SameSite=Lax`. If `Secure` is missing,
`PORTAL_ENV` is not set to `production` on Render.

- [ ] **Step 4: Write `README.md`**

It must contain, and contain little else:

1. One paragraph: what this is and why it exists.
2. The public URL, and the three demo logins, each labelled explicitly as
   **public credentials for entirely fictional data**.
3. Run locally: `pip install -r requirements.txt`, `SECRET_KEY=dev python seed.py`,
   `SECRET_KEY=dev flask --app "app:create_app()" run`.
4. Run the tests: `pytest`.
5. **How isolation is enforced** — the two chokepoints, named, in five sentences:
   the `before_request` gate with `PUBLIC_ENDPOINTS`, and `db.query()` refusing
   SQL without `:org_id` and binding that value from the session. Point at
   `tests/test_isolation.py` and `docs/breaking-it.md`.
6. **Known limitations**, stated plainly, not buried:
   - The `:org_id` string check is a tripwire for a forgotten `WHERE`, not a
     proof; `OR 1=1` defeats it. What actually holds is the small reviewed query
     surface plus the structural tests.
   - The session cookie is **signed, not encrypted**, and is a bearer token with
     no server-side revocation: a captured cookie stays valid until it expires,
     including after logout. Bounded to 8 hours. A `session_version` column would
     fix it.
   - SQLite on Render's free tier is ephemeral; the database is rebuilt from
     `seed.py` on every boot. Production would use managed Postgres.
   - The free instance sleeps; the first request after idle takes ~50 seconds.
   - Out of scope by design: signup, password reset, roles, rate limiting, MFA,
     audit logging, and any write path. Rate limiting is the first thing I'd add.
7. Nothing else. No feature tour, no screenshots, no architecture diagram.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: readme with isolation notes and known limitations"
```

---

## Gate 3: Explain it without the code

Not a coding task, and the only one that decides whether the project worked.
Close the laptop. Out loud, from memory, in under three minutes:

- [ ] Name the two chokepoints and what each one denies by default.
- [ ] Say why the session stores `user_id` and not `org_id`.
- [ ] Say why a cross-tenant invoice returns 404 and not 403.
- [ ] Say what the `:org_id` check does **not** catch, and what actually holds
      the boundary instead.
- [ ] Say what the session cookie is: signed, not encrypted; a bearer token with
      no server-side revocation, bounded to eight hours.
- [ ] Name one mutation from `docs/breaking-it.md` and the test that caught it.

Any of those you cannot say without looking is a place where you have shipped
code you do not own. Go back and read that file until you do. This matters more
than any remaining feature.


## Self-Review

**Spec coverage.** `SPEC.md` §1 data model → Task 1. §2 routes → Tasks 2, 5, 6,
7, 8 (all seven: `index`, `login` GET+POST, `logout`, `dashboard`,
`invoice_detail`, `healthz`). §3 authorization chokepoints → Tasks 3 and 5.
§4 auth mechanics → Tasks 2, 5, 6 (cookie config and `SECRET_KEY` hard fail in
Task 2; hashing, generic failures and dummy-hash timing in Task 5; CSRF in
Task 6). §5 tests 1–12 → test 1 in Tasks 5 and 8; 2 in Task 5; 3 in Task 7;
4, 5, 6 in Task 8; 7 and 8 in Task 6; 9 in Task 8; 10 in Task 1; 11 in Task 9;
12 in Task 3. §6 seed → Task 4; dashboard content → Tasks 7 and 10.
§7 build order → Tasks 1–11 plus three gates.

**Deviations from `SPEC.md` §7, both deliberate:**
1. Deployment moves from last to **Task 2**, on a skeleton serving only
   `/healthz`. Deployment is the highest-variance unknown and the one the
   engineer has never done; retiring it while the app is four lines long means a
   build failure is diagnosable in isolation, and every later task ships to a
   working URL. `render.yaml` gains its seed step in Task 4 Step 6, once
   `seed.py` exists.
2. The full seed (spec step 4) moves ahead of login (spec step 1), because
   logging in requires a seeded user and seeding twice is waste.

**Three gates, distinct from tasks.** Gate 1 closes at the end of Task 2 (public
URL answers `/healthz`). Gate 2 sits between Tasks 9 and 10 — an adversarial
`/security-review` pass over the boundary code, before any cosmetic work. Gate 3
follows Task 11 and is not code: explaining the design from memory. A task is
done when its tests pass; a gate is done when a person is satisfied.

**Placeholder scan.** No TBDs. Every code step carries real code. Two forward
references, both called out where they occur: `templates/dashboard.html` links
`invoice_detail` before Task 8 defines it (workaround given in Task 7 Step 4),
and Task 2's `render.yaml` has no seed step until Task 4 Step 6 adds it.

**Type consistency.** `create_app(db_path=None, secret_key=None)` is introduced
in Task 2 and grown, never re-signed, in Task 5. `db.query(sql, **params)` is
used identically in Tasks 7 and 8. `money()` is module-level in Task 5 and
imported as `app_module.money` in Task 7's test. `seed.CREDENTIALS` is
`list[tuple[str, str]]` in Task 4 and unpacked as such in Tasks 5–8.
`seed.ORGS[n][5]` is the revenue dollar list, matching the tuple layout in
Task 4 and used in Task 7's totals test. `PUBLIC_ENDPOINTS` is a module-level
`set[str]` in Task 5, asserted equal in Task 9, read as
`app_module.PUBLIC_ENDPOINTS` in Task 8.

**Cumulative test counts** (each task's "Expected: N passed" is the whole suite):
Task 1 → 3, Task 2 → 6, Task 3 → 10, Task 4 → 14, Task 5 → 21, Task 6 → 27,
Task 7 → 32, Task 8 → 39, Task 9 → 41, Task 10 → 43.
