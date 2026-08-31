# Client Portal Demo — Specification

Single-tenant-per-user financial dashboard. The deliverable is a demonstration of
**multi-tenant isolation**, not a dashboard. Every design choice below is made to
serve one sentence you should be able to say out loud in an interview:

> "The organization identity comes from the server-side session and nowhere else,
> and the code is arranged so that writing an unscoped query is an error, not an
> oversight."

Stack: Python 3.12, Flask, SQLite, Jinja, gunicorn on Render free tier. No JS
framework. No CSS framework. No ORM.

---

## 1. Data model

Money is stored as **integer cents**. Never floats. An accounting firm will notice.

### `organizations`
| column | type | constraints |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY |
| `name` | TEXT | NOT NULL, UNIQUE |
| `fiscal_year_end` | TEXT | NOT NULL — `MM-DD`, display only |

### `users`
| column | type | constraints |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY |
| `org_id` | INTEGER | NOT NULL, REFERENCES `organizations(id)` |
| `email` | TEXT | NOT NULL, UNIQUE (case-folded on insert and lookup) |
| `password_hash` | TEXT | NOT NULL |

One user per org in the seed. The schema does not forbid more; nothing in the app
depends on there being exactly one.

### `monthly_figures` — tenant table
| column | type | constraints |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY |
| `org_id` | INTEGER | NOT NULL, REFERENCES `organizations(id)` |
| `month` | TEXT | NOT NULL — `YYYY-MM` |
| `revenue_cents` | INTEGER | NOT NULL |
| `expenses_cents` | INTEGER | NOT NULL |
| | | UNIQUE (`org_id`, `month`) |

### `invoices` — tenant table
| column | type | constraints |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY |
| `org_id` | INTEGER | NOT NULL, REFERENCES `organizations(id)` |
| `number` | TEXT | NOT NULL |
| `counterparty` | TEXT | NOT NULL — who was billed |
| `issued_on` | TEXT | NOT NULL — `YYYY-MM-DD` |
| `amount_cents` | INTEGER | NOT NULL |
| `status` | TEXT | NOT NULL, CHECK IN ('paid','outstanding','overdue') |
| | | UNIQUE (`org_id`, `number`) |

`invoices` exists specifically to give the URL-tampering test something to tamper
with. It is the only route that takes a row id.

### Where the tenant boundary lives

**Every table other than `organizations` and `users` carries `org_id INTEGER NOT
NULL REFERENCES organizations(id)`.** That column is the boundary. There is no
second mechanism — no per-tenant database file, no schema-per-tenant, no row-level
security (SQLite has none).

Three consequences to design around:

1. **`id` is globally unique, not per-org sequential.** Org A's invoices might be
   ids 1–8 and org B's 9–16. This is deliberate: it means a valid id from another
   tenant *exists*, so the tampering test is testing something real. Per-org
   sequential ids would let a broken app "pass" by accident.
2. **`PRAGMA foreign_keys = ON` must be issued on every connection.** SQLite
   defaults it off. Without it the FKs above are decorative.
3. **Index on `(org_id, id)` for `invoices` and `(org_id, month)` for
   `monthly_figures`.** Not for performance at this size — so that the composite
   key that the code always queries by is the one the schema advertises.

---

## 2. Routes

Every route. Method, requirement, response.

| Method | Path | Requires | Returns |
|---|---|---|---|
| GET | `/` | — | 302 → `/dashboard` if session valid, else 302 → `/login` |
| GET | `/login` | — | 200 login form. 302 → `/dashboard` if already logged in |
| POST | `/login` | valid CSRF token, `email`, `password` | 302 → `/dashboard` on success; 200 re-render with one generic error on failure; 400 on bad/missing CSRF token |
| POST | `/logout` | session, valid CSRF token | 302 → `/login`, session cleared |
| GET | `/dashboard` | session | 200 HTML: summary tiles, 12-month table, inline-SVG chart, invoice list — all for `g.org` only |
| GET | `/invoices/<int:invoice_id>` | session | 200 HTML detail if the invoice belongs to `g.org`; **404** otherwise |
| GET | `/healthz` | — | 200 `"ok"`, no DB access — Render health check |

No other routes. No `/orgs/<id>/...`. **No route anywhere takes an organization
identifier.** There is nothing in any URL for a user to edit that would change
which tenant's data is read; `invoice_id` selects a row *within* an already-fixed
tenant scope.

### Why 404 and not 403 for a cross-tenant invoice

403 confirms the row exists. 404 does not. The response for "someone else's
invoice" and "no such invoice" must be byte-identical, or the app is an existence
oracle for another tenant's invoice numbering. Say this in the interview; it is a
cheap point that shows you thought past the happy path.

### Logout is POST, not GET

A `GET /logout` gets fired by link prefetchers and can be triggered by an
`<img src>` on any other site. It costs nothing to make it a form button.

---

## 3. Where authorization is enforced

Two chokepoints, one for authentication and one for tenant scoping. Both are
**default-deny**: the unsafe thing requires you to opt in explicitly, in a place a
reviewer will see.

### 3a. Authentication: one `before_request` gate

A single `@app.before_request` handler runs before every request:

- If the endpoint is in a small module-level `PUBLIC_ENDPOINTS` set
  (`login`, `healthz`, `static`, `index`), it passes.
- Otherwise: read `session["user_id"]`. If absent → 302 to `/login`. If present,
  load the user row from the DB, and if it is gone → clear session, 302 to
  `/login`.
- On success, set `g.user` and `g.org_id` for the duration of the request.

**Why this is the safe default:** a new route added six months from now is
protected the moment it exists. Nobody has to remember a `@login_required`
decorator. Forgetting the decorator fails open; forgetting to add to
`PUBLIC_ENDPOINTS` fails closed.

**The session stores exactly one value: `user_id`, an integer.** `org_id` is *not*
stored in the session — it is looked up from the `users` row on every request.
One source of truth, and there is no cached tenant identity to go stale. The cost
is one indexed primary-key lookup per request.

### 3b. Tenant scoping: one query helper

`db.py` exposes exactly one way for application code to read tenant tables:

```
rows = db.query(sql, **params)
```

It does two things before executing:

1. **Refuses SQL that does not contain the literal token `:org_id`.** Raises
   immediately if absent. A query with no tenant predicate cannot run.
2. **Binds `:org_id` itself, from `g.org_id`.** Callers cannot pass `org_id` in;
   the helper rejects it if they try. The value that ends up in the WHERE clause
   is unreachable from any request input.

Callers write `... WHERE org_id = :org_id AND id = :invoice_id` and supply only
`invoice_id`. The tenant predicate is the price of admission for running a query
at all.

`db.py` also holds the raw connection, used by exactly two things: the
`before_request` user lookup (which queries `users` by primary key, before any org
is known) and the seed/schema script. Everything else goes through `db.query`.

### Be honest about the limits of this

The `:org_id` check is a **string check, not a proof**. `WHERE org_id = :org_id OR
1=1` would pass it. It is a tripwire for the accident you actually described —
forgetting the WHERE clause — not a defense against someone deliberately writing a
bad query.

What actually provides the guarantee is the combination:

- The query surface is roughly six functions in one file, short enough to read in
  one sitting.
- `db.query` makes the tenant predicate mandatory and its value untouchable.
- Three tests (§5) fail if a new table lacks `org_id`, a new route is unprotected,
  or SQL appears outside `db.py`.

That is a defensible answer. "It's impossible to write an unscoped query" is not,
and a technical evaluator will find the hole in about thirty seconds.

---

## 4. Auth mechanics

**Password hashing.** `werkzeug.security.generate_password_hash` /
`check_password_hash`. Current Werkzeug defaults to scrypt. Do not pick the
algorithm yourself, do not tune the parameters, do not touch the salt — the whole
value of the library call is that those decisions are not yours. Hashes are
generated by the seed script; plaintext passwords exist only in the seed script.

**Failed login response.** Unknown email and wrong password produce the *same*
message ("Incorrect email or password"), the same status code, and the same page.
Two different messages turn the login form into an account-enumeration endpoint.

**Session.** Flask's signed cookie session. Configuration:

| setting | value | why |
|---|---|---|
| `SECRET_KEY` | from env var, **hard fail at boot if missing** | a hardcoded fallback makes sessions forgeable by anyone who reads the repo; a random fallback silently logs everyone out on every Render restart |
| `SESSION_COOKIE_HTTPONLY` | `True` | JS cannot read it |
| `SESSION_COOKIE_SECURE` | `True` in production | HTTPS only; Render terminates TLS |
| `SESSION_COOKIE_SAMESITE` | `"Lax"` | blocks the cross-site POST case |
| `PERMANENT_SESSION_LIFETIME` | 8 hours | bounds the replay window (see below) |

On successful login, call `session.clear()` before setting `user_id`, so a
pre-login session cannot be fixated and carried through.

**What the cookie is.** Signed, **not encrypted**. The contents are base64 and
anyone holding the cookie can read `{"user_id": 3}`. The signature is what makes
it unforgeable. Know this cold — claiming it is "encrypted" in an interview is
the kind of error that ends the technical conversation.

**Replay.** You raised this specifically. Precise statement: a signed session
cookie is a **bearer token with no server-side revocation**. If someone captures
org A's cookie, it works until it expires — including after A clicks logout, which
only clears the copy in A's browser. It does *not* let them reach org B, because
the cookie says `user_id: 3` and user 3 belongs to org A. Two options:

- **Accept it**, cap the lifetime at 8 hours, and state the tradeoff. Recommended
  for a one-day demo.
- Add a `session_version` integer on `users`, store it in the cookie alongside
  `user_id`, compare on every request, bump it on logout. About 10 lines.

Pick the first, write the second in the README as "what I'd add next." Naming the
limitation is worth more than half-implementing the fix.

**CSRF.** Not in your scope list; add it. Per-session random token in the session,
hidden input in the login and logout forms, constant-time compare on POST, 400 on
mismatch. About 15 lines, no dependency. Without it your two POST routes are
cross-site triggerable, and login CSRF (logging a victim into *your* account) is a
real class of bug an evaluator may ask about.

**Not in scope, deliberately, and say so in the README:** login rate limiting /
lockout, MFA, password rotation, audit logging. Rate limiting is the first thing
you'd add; the free tier restarts often enough that in-process counters are
theatre and doing it properly needs Redis.

**Logged-out user hitting a protected route:** 302 to `/login`, no flash of
content, no data queried — the gate runs before any view function. No `?next=`
parameter: it is an open-redirect surface, and on a 7-route app it buys nothing.

---

## 5. Test plan for isolation

`pytest`, Flask's `test_client`, a fresh temp SQLite file seeded per test session.
Two logged-in clients, `client_a` and `client_b`, as fixtures. **No ids hardcoded
in tests** — read them from the DB, so the tests keep meaning if seed order
changes.

### Behavioural tests — the boundary itself

1. **Anonymous is refused everywhere.** For each protected route: 302 to `/login`,
   and the response body contains none of the seed figures.
2. **Login rejects bad credentials.** Wrong password and unknown email → same
   status, same body. Assert they are equal to each other.
3. **Dashboard shows only your org.** A's dashboard contains A's org name and at
   least one A-specific figure; assert B's org name and B's distinctive invoice
   numbers are **absent** from the body. The negative assertion is the test.
4. **URL tampering.** A is logged in. `GET /invoices/{id_of_a_row}` → 200.
   `GET /invoices/{id_of_b_row}` → **404**. Then assert the 404 body is identical
   to `GET /invoices/999999`. This is the headline test; name it
   `test_cannot_read_another_orgs_invoice_by_id`.
5. **Cookie replay across tenants.** Extract A's session cookie from `client_a`,
   attach it to a *fresh* client, request B's invoice id → 404. Proves the scope
   travels with the identity, not with the client object.
6. **Tampered cookie is rejected.** Flip one byte in the signature portion of a
   valid cookie → the request is treated as logged out (302 to `/login`), not as a
   different user. Proves the signature is the boundary.
7. **Session fixation.** Session id/contents before login differ from after.
8. **CSRF.** POST `/login` with no token and with a wrong token → 400.

### Structural tests — the ones that stop the bug coming back

These are the tests worth talking about in the interview, because they fail on code
that does not exist yet.

9. **Every route is authenticated unless explicitly public.** Iterate
   `app.url_map`; assert every endpoint is either in `PUBLIC_ENDPOINTS` or returns
   a redirect to `/login` for an anonymous client. Adding a route and forgetting
   to think about auth fails the suite.
10. **Every tenant table has `org_id NOT NULL`.** Introspect
    `PRAGMA table_info(...)` for every table except `organizations` and `users`.
    Adding a table without a tenant column fails the suite.
11. **No SQL outside `db.py`.** Grep the source tree for `.execute(` and raw
    connection use; assert the only hits are in `db.py`. Bypassing the helper
    fails the suite.
12. **`db.query` refuses unscoped SQL.** `db.query("SELECT * FROM invoices")`
    raises. And passing `org_id=` as a caller parameter raises.

Twelve tests, one file, maybe 150 lines. Tests 4, 9, 10 and 11 are the ones to
point at when asked "how do you know it's right."

### What this does not prove

State it in the README rather than letting an evaluator find it: the tests cover
the routes that exist. They do not prove the absence of a tenant leak in code
nobody has written. Tests 9–11 exist to narrow that gap to "someone edited
`db.py` or `PUBLIC_ENDPOINTS` deliberately."

---

## 6. Seed data

Three invented organizations. Names checked to not resemble any real firm. Emails
use the `.test` TLD, which RFC 2606 permanently reserves — it can never resolve to
a real domain, so nothing can accidentally send mail anywhere.

| Org | Character | Login |
|---|---|---|
| Alder & Finch Bookbinding | small manufacturer, seasonal, thin margins | `owner@alderfinch.test` |
| Cobalt Harbour Logistics | mid-size, high revenue, high expenses, lumpy months | `owner@cobaltharbour.test` |
| Meridian Fern Wellness | services, steady growth, small invoices | `owner@meridianfern.test` |

Distinct passwords per org, defined in the seed script, printed by it, and listed
in the README. They are demo credentials on a public URL holding entirely
fictional data; say exactly that in the README so nobody mistakes it for a leak.

Make the three orgs' numbers **visibly different in shape** — different magnitudes,
different seasonality, different invoice-number prefixes (`AF-`, `CH-`, `MF-`).
This is a testing decision, not a presentation one: if all three orgs looked
similar, a leak could pass a visual check, and your negative assertions in test 3
would be weaker.

12 months of `monthly_figures` per org (`2025-09` through `2026-08`). 6–10
invoices per org, mixed statuses.

### What the dashboard shows

- **Four summary tiles:** 12-month revenue, 12-month expenses, net, and total
  outstanding + overdue invoice value.
- **A 12-row table:** month, revenue, expenses, net. Right-aligned, formatted from
  cents to `$X,XXX.XX` in one Jinja filter.
- **One chart:** monthly revenue vs expenses, as **inline SVG generated in the
  Jinja template** from the same rows as the table. No chart library, no CDN, no
  JS. Bar heights are a ratio against the max value — about 15 lines of template.
  It is server-rendered, so it is covered by the same tenant scoping as everything
  else, which is a better story than a JSON endpoint feeding a JS chart.
- **Invoice list:** number, counterparty, date, amount, status, each linking to
  `/invoices/<id>`.

The org name appears in the page header on every page. Free visual evidence during
a live demo that you are seeing one tenant.

---

## 7. Build order

Each step ends in something you can run and check. Do not start the next until the
current one is verified.

**Step 1 — Login end to end, no dashboard.**
`app.py`, `db.py`, `schema.sql`, `seed.py`, `templates/base.html`,
`templates/login.html`. One org, one user, hashed password. `POST /login` sets
`session["user_id"]`; success redirects to a placeholder page that prints the org
name from `g.org_id`. `before_request` gate and `PUBLIC_ENDPOINTS` exist from this
step — they are the architecture, not a later hardening pass.
*Verify:* log in, see the org name; wrong password shows the generic error; hit the
placeholder page in a private window and get bounced to `/login`.

**Step 2 — Logout, CSRF, session config.**
POST logout, CSRF token in both forms, cookie flags, `SECRET_KEY` from env with a
hard failure if unset.
*Verify:* logging out bounces you; POSTing the login form with the token stripped
gives 400; app refuses to boot with no `SECRET_KEY`.

**Step 3 — `db.query` and the guard.**
Move all reads to the helper. Write test 12 first — the helper should reject
unscoped SQL before anything depends on it.
*Verify:* `pytest` green on the guard tests.

**Step 4 — Full seed, three orgs.**
`monthly_figures` and `invoices` for all three. Seed script is idempotent: creates
the DB and populates only if empty.
*Verify:* `sqlite3` the file, confirm row counts per org and that no tenant row has
a NULL `org_id`.

**Step 5 — Dashboard: tiles and table.**
No chart yet.
*Verify:* log in as each of the three orgs in three private windows; the numbers
differ and match what the seed inserted.

**Step 6 — Invoice detail route.**
`/invoices/<int:id>` with the 404-on-foreign-row behaviour.
*Verify:* by hand, before writing the test — log in as A, paste one of B's ids into
the URL bar, get a 404. This is the moment the project's thesis becomes real; do it
manually so you have seen it with your own eyes.

**Step 7 — The test suite.**
All twelve tests. Expect tests 9–11 to fail first and to tell you something true
about the code.
*Verify:* `pytest -v` green; then deliberately break it — delete the `org_id`
predicate from the invoice query and confirm test 4 goes red. **A test suite you
have never seen fail is not evidence of anything.** Do this for tests 4, 9 and 10,
and be ready to describe it.

**Step 8 — The chart.**
Inline SVG in the dashboard template.
*Verify:* bars match the table for all three orgs.

**Step 9 — Deploy to Render.**
`requirements.txt`, gunicorn, `SECRET_KEY` as an env var, seed on boot if the DB
file is absent, `/healthz` as the health check.
*Verify:* public URL, log in as all three orgs, repeat the step 6 tampering check
against production.

**Step 10 — README.**
What it is, how to run it locally, how to run the tests, the demo credentials with
the fictional-data disclaimer, a short "how isolation is enforced" section naming
the two chokepoints, and an honest "not in scope / what I'd add next" list.

Budget: steps 1–8 are a solid working day. Step 9 will take longer than you expect
the first time — build the whole thing locally first, deploy once at the end.

---

## 8. Pushback and assumptions

### Things I think you have wrong or have not accounted for

**1. Render's free tier has no persistent disk. Your SQLite file is deleted on
every restart and redeploy, and free instances sleep after ~15 minutes of
inactivity.** This is the single biggest practical risk to your demo and it is not
mentioned anywhere in your brief. Consequences: any data written during the demo
vanishes; a cold start takes ~50 seconds, so the interviewer clicking your link
sees a blank tab for the better part of a minute. Mitigations: seed at boot so the
app is always self-healing; **open the URL yourself five minutes before the
interview** to wake it; put a line in the README saying the DB is ephemeral by
design and production would use managed Postgres. Do not attach a paid disk to fix
this — being able to explain the constraint is worth more than engineering around
it.

**2. "Visual polish is close to worthless here" is right about polish and wrong
about legibility.** A dashboard that looks broken makes the evaluator doubt the
parts they cannot see. Spend thirty minutes on one small stylesheet — readable
font, right-aligned numbers, adequate whitespace — and stop. Right-aligned money
columns specifically: an accounting firm reads that as competence.

**3. Your scope omits CSRF, and I think that is a mistake.** See §4. Fifteen lines,
no dependency, and it closes a bug class an evaluator may specifically probe. I
would add it. If you disagree, still be ready to name it as a known gap.

**4. "Prove that isolation is right" is a claim about a system, and a test suite
alone will not carry it.** The evaluator needs to be able to find your evidence in
under a minute. That means: test names that read as sentences
(`test_cannot_read_another_orgs_invoice_by_id`), a README section that names the
two chokepoints, and the fact that you deliberately broke the scoping to watch the
test fail. That last one is the strongest thing you can say in the interview, and
it costs two minutes in step 7.

**5. Watch your language about the session cookie.** Signed, not encrypted; a
bearer token with no server-side revocation. §4 gives you the precise phrasing.
Over-claiming here is a much bigger credibility hit than a missing feature.

**6. One day is realistic for this scope, and it stops being realistic the moment
you touch the chart.** The chart is the tarpit — it is the part that looks like
progress and produces none. Inline SVG, bars, done. If you find yourself adding
axis labels or tooltips, stop.

**7. A stats background is an asset here; do not hide it.** The strongest version
of your interview answer is not "I learned Flask." It is "I treated tenant
isolation as a property to be tested rather than a feature to be implemented, so I
wrote tests that fail on code I haven't written yet." That is a testing-methodology
argument, and it is the thing your background actually gives you an edge on.

### Assumptions I have made — confirm or correct

- **The evaluator reads the repo, not just the running app.** Everything above
  weights tests and README over UI. If they only ever click the link, this is the
  wrong allocation.
- **One user per organization, forever, with no roles.** No `is_admin`, no
  cross-org viewer, no "accountant sees all clients" role — even though a real
  accounting firm portal would certainly need that last one. Worth a sentence in
  the README so it reads as a scoping decision, not an oversight.
- **Read-only.** No route mutates tenant data. If you add any write path, every
  scoping argument above has to be re-made for writes (an UPDATE with a WHERE on
  `id` alone is the same bug in a worse costume), and the test count roughly
  doubles. I have assumed you will not.
- **No real data ever touches this deployment**, including during the interview.
  The credentials are public.
- **Python 3.12 locally** (confirmed) **and pinned on Render.** Pin the version in
  `runtime.txt` or Render will pick one for you.
- **Fictional invoice counterparty names count as seed data** and are subject to
  the same invented-names rule as the org names.
- **`.test` email addresses are acceptable.** They cannot receive mail, which is
  the point, but they do look unusual. Say why in the README.
