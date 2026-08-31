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
