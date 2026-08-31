# Client Portal

A small multi-tenant client portal: three organizations, one user each, every
user seeing only their own financial figures. Flask, SQLite, server-rendered
Jinja. No ORM, no JavaScript, no CSS framework.

The point of this project is **tenant isolation** — that one client's session can
never reach another client's data, including by editing a URL or replaying a
request. The dashboard exists to give the isolation something to protect.

**Live:** https://client-portal-demo-ahiv.onrender.com

## Demo logins

All data is invented. These credentials are **public on purpose** — there is
nothing here to protect, and nothing resembling a real organization. Addresses
use the `.test` TLD, which RFC 2606 reserves permanently, so none of them can
resolve or receive mail.

| Organization | Email | Password |
|---|---|---|
| Alder & Finch Bookbinding | `owner@alderfinch.test` | `alder-demo-2026` |
| Cobalt Harbour Logistics | `owner@cobaltharbour.test` | `cobalt-demo-2026` |
| Meridian Fern Wellness | `owner@meridianfern.test` | `meridian-demo-2026` |

The free Render instance sleeps when idle; the first request can take ~50s.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
SECRET_KEY=dev .venv/bin/flask --app "app:create_app()" run
```

The database is created and seeded on first start. Tests:

```bash
.venv/bin/pytest
```

## How isolation is enforced

Two chokepoints, both default-deny.

**Authentication** is a single `before_request` gate in `app.py`, keyed off a
module-level `PUBLIC_ENDPOINTS` set. Anything not named there needs a session,
so a route added later is protected the moment it exists and making one public
requires editing a visible line.

**Tenant scoping** is a single helper, `db.query()`, which is the only way
application code reads tenant tables. It raises unless the SQL contains
`org_id = :org_id`, and it binds that value itself from the session-derived
`g.org_id` — callers cannot supply it. No route anywhere accepts an organization
identifier from a URL, form field, header, or cookie; the session stores one
value, `user_id`, and the organization is looked up server-side on every
request.

Cross-tenant reads return **404, not 403** — a 403 would confirm the row exists.
The two responses are byte-identical.

The evidence is in `tests/test_isolation.py`, and in
[`docs/breaking-it.md`](docs/breaking-it.md), which records four deliberate
mutations of the running code and which test caught each.

## Known limitations

- **The `:org_id` check in `db.query` is a tripwire, not a proof.** It catches a
  forgotten `WHERE`; `OR 1=1` walks past it. What actually holds the boundary is
  the small reviewed query surface plus the behavioural and structural tests.
- **The session cookie is signed, not encrypted**, and is a bearer token with no
  server-side revocation. A captured cookie stays valid until it expires,
  including after logout. Bounded to 8 hours; a `session_version` column on
  `users` would fix it.
- **The database is ephemeral.** Render's free tier has no persistent disk, so
  SQLite is rebuilt from `seed.py` on every boot. Production would use managed
  Postgres.
- **Read-only.** No route mutates tenant data. Adding a write path would require
  re-making every scoping argument above for `UPDATE` and `DELETE`.
- **Out of scope by design:** signup, password reset, roles and permissions
  beyond one user per organization, admin views, rate limiting, MFA, and audit
  logging. Rate limiting would be the first addition.
