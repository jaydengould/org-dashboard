# Breaking it on purpose

A test suite you have never seen fail is not evidence of anything. Before
trusting these tests, I broke the application four ways and recorded which test
caught each. Every mutation was reverted; `pytest` is green at 42 passed.

Reproduce any row by making the edit and running `pytest -q --tb=no`.

---

## 1. Drop the tenant predicate entirely

`app.py`, `invoice_detail`:

```diff
- " FROM invoices WHERE org_id = :org_id AND id = :invoice_id",
+ " FROM invoices WHERE id = :invoice_id",
```

**Caught by:** `db.query`'s own guard, as a `ValueError`, before SQLite ran.
4 failures, including `test_can_read_own_invoice`.

The interesting part is *which* tests failed. The positive test failed too —
the app could not read its own invoices either. The forgotten `WHERE` does not
produce a leaky application; it produces one that refuses to answer. That is
fail-closed, and it is the behaviour I wanted from the guard.

## 2. Keep the predicate but neuter it

```diff
- " FROM invoices WHERE org_id = :org_id AND id = :invoice_id",
+ " FROM invoices WHERE (org_id = :org_id OR 1=1) AND id = :invoice_id",
```

**Caught by:** `test_cannot_read_another_orgs_invoice_by_id` and two other
behavioural isolation tests. 3 failures.

**Not caught by `db.query`.** The SQL still contains `:org_id`, so the string
check passed and the query ran. This is the honest limit of that guard: it is a
tripwire for a forgotten `WHERE`, not a proof of correct scoping. What caught
this was a test that logs in as one tenant and asks for another tenant's row.

## 3. Quietly make the dashboard public

```diff
- PUBLIC_ENDPOINTS = {"login", "healthz", "static", "index"}
+ PUBLIC_ENDPOINTS = {"login", "healthz", "static", "index", "dashboard"}
```

**Caught by:** 12 tests, including
`test_public_endpoints_list_is_small_and_explicit`, which exists precisely
because a growing public list is the most likely way this app ever springs a
leak.

The failure mode is worth reading. Most of those 12 failed with
`AttributeError`, not with leaked data — because the authentication gate never
ran, `g.org_id` was never set, and `db.query` refused to bind a tenant it did
not have. Breaking the authentication chokepoint does not expose another
tenant's rows; it makes the page error out. The two chokepoints fail
independently, so one of them being wrong is not sufficient to leak.

## 4. Add a table with no tenant column

`schema.sql`:

```sql
CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);
```

**Caught by:** `test_every_tenant_table_has_org_id_not_null`, which introspects
`PRAGMA table_info` for every table except `organizations` and `users`.
2 failures.

This is the guard against future work rather than present bugs: any table added
later without `org_id INTEGER NOT NULL` fails the suite on the next run.

---

## What this exercise did not prove

These tests cover the routes that exist. They cannot prove the absence of a leak
in code nobody has written. Tests 3 and 4 above exist to narrow that gap: the
route-coverage test and the schema test both fail on *new* code that forgets the
boundary. What remains is someone deliberately editing `db.py` or
`PUBLIC_ENDPOINTS`, which is a code-review problem, not a test problem.
