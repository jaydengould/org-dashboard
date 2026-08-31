# CLAUDE.md — org-dashboard

## What this is

A demo multi-tenant client portal for a junior-developer interview at a small
accounting firm. Three fictional organizations, one user each, each sees only
their own financial figures.

**The point of the project is tenant isolation, not the dashboard.** Visual
polish is near-worthless here; correct, explainable authorization is the whole
deliverable. Optimize every decision for "can I explain this to a technical
evaluator from memory."

## Working conventions

- Boring over clever. Single small codebase, readable end to end.
- Ponytail mode: laziest solution that actually works. Never lazy about security,
  input validation, or understanding the problem.
- Be a sparring partner, not a yes-man. Push back on flawed premises.
- Update `CLAUDE.md` and `TODO.md` every session. Touch `README.md` only if it is
  actually wrong for a stranger cloning the repo.

## Decisions made (2026-08-30)

**Stack.** Python 3.12, Flask 3.0.3, stdlib `sqlite3`, Jinja, gunicorn, pytest.
Dependencies capped at Flask + gunicorn + pytest. No ORM, no Flask-Login, no
Flask-WTF, no chart library, no CSS framework, no JS.

**Two chokepoints, both default-deny.** This is the architecture:
1. **Authentication** — one `@app.before_request` gate in `app.py` keyed off a
   module-level `PUBLIC_ENDPOINTS = {"login", "healthz", "static", "index"}`.
   A new route is protected the moment it exists; making it public requires
   editing that one visible line.
2. **Tenant scoping** — `db.query(sql, **params)` in `db.py` raises unless the
   SQL contains the literal `:org_id`, and binds that value itself from
   `g.org_id`. Callers cannot pass `org_id`. Raw SQL is permitted only in
   `db.py` and `seed.py`, enforced by `tests/test_structure.py`.

**Session holds one value: `user_id`.** `org_id` is *not* stored in the session;
it is looked up from the `users` row on every request. One source of truth, no
stale cached tenant identity.

**Honest limit, stated deliberately.** The `:org_id` check is a string tripwire
for a forgotten `WHERE`, not a proof — `OR 1=1` defeats it. What actually holds
the boundary is the small reviewed query surface plus three structural tests
(every route authenticated, every tenant table has `org_id NOT NULL`, no SQL
outside `db.py`). Never claim more than this.

**Cross-tenant access returns 404, not 403.** A 403 confirms the row exists.
The cross-tenant 404 body must be byte-identical to a nonexistent-row 404, so
there is deliberately no custom 404 handler.

**Money is integer cents everywhere**, including in the display filter. No floats
touch a monetary value.

**Deploy first, not last (2026-08-30, revised).** `render.yaml` and a skeleton
`app.py` serving only `/healthz` ship as Task 2, before any feature. Deployment
is the highest-variance unknown here and the one that eats first-timers' days;
retiring it on a four-line app means a build failure is diagnosable in
isolation. `seed.py` joins the boot command in Task 4.

**Three gates, distinct from tasks.** A task is done when its tests pass; a gate
is done when a person is satisfied. Gate 1: the public URL answers `/healthz`.
Gate 2: `/security-review` over the boundary before any cosmetic work — the
author of a security guard is the worst reviewer of it. Gate 3: explain the
design out loud from memory, laptop closed. Gate 3 is the one that decides
whether the project worked.

**CSRF added to scope** (was not in the original brief). Per-session token,
hidden field, `hmac.compare_digest`, 400 on mismatch, ~15 lines, no dependency.
The compare must reject an absent session token — `compare_digest("", "")` is
True.

**Session cookie is signed, not encrypted**, and is a bearer token with no
server-side revocation. Bounded to 8 hours. Accepted and documented rather than
half-fixed; the fix would be a `session_version` column on `users`.

**Render free tier has no persistent disk.** SQLite is rebuilt from an
idempotent `seed.py` on every boot. `--workers 1`. Free instances sleep — first
request after idle takes ~50s, so wake the URL before any live demo.

## Schema

`organizations(id, name, fiscal_year_end)`,
`users(id, org_id, email, password_hash)`,
`monthly_figures(id, org_id, month, revenue_cents, expenses_cents)`,
`invoices(id, org_id, number, counterparty, issued_on, amount_cents, status)`.

Every table except `organizations` and `users` carries
`org_id INTEGER NOT NULL REFERENCES organizations(id)`. Row ids are globally
unique, not per-org sequential — deliberately, so the URL-tampering test is
testing something real. `PRAGMA foreign_keys = ON` on every connection.

## Seed organizations (all fictional, `.test` emails)

Alder & Finch Bookbinding (`AF-`), Cobalt Harbour Logistics (`CH-`),
Meridian Fern Wellness (`MF-`). Deliberately different magnitudes and invoice
prefixes so a leak cannot pass a visual check and negative test assertions bite.

## Documents

- `SPEC.md` — the specification, including pushback and open assumptions (§8).
- `docs/superpowers/plans/2026-08-30-client-portal.md` — the 10-task
  implementation plan — 11 tasks and 3 gates, TDD, one commit per task.
- `docs/breaking-it.md` — (Task 8) the four deliberate mutations and which test
  caught each. The most useful page in the repo for an evaluator.

## Next

See `TODO.md`.
