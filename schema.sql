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
