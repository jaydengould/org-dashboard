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
    try:
        return get_conn().execute(sql, params).fetchall()
    except OverflowError:
        # SQLite integers are 64-bit. A value too wide to store cannot match any
        # stored row, so the honest answer is "no rows" rather than a 500. Keeps
        # /invoices/<huge> indistinguishable from any other miss.
        return []


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
