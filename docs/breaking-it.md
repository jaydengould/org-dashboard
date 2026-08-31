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

---

# Gate 2: adversarial security review

An automated security review was run over the whole branch by a reviewer with no
prior context on the code, explicitly instructed not to trust the code's own
comments about its security model. It probed a live instance rather than only
reading.

**Result: no HIGH or MEDIUM findings at the confidence bar.** The bypasses it
tried and failed to land: `request.endpoint` being `None` on unmatched URLs,
`//dashboard`, `/dashboard/`, `/DASHBOARD`, `OPTIONS`, `TRACE`, CSRF tokens
moved to the query string and to a JSON body, `X-HTTP-Method-Override: GET`,
`HEAD/CONNECT/PROPFIND/LINK` on `/logout`, `/static/..%2f..%2fapp.py`, and
`' OR '1'='1` as an email.

It independently reached the same conclusion recorded in mutation 2 above: the
`":org_id" in sql` check is a substring tripwire that `OR 1=1` defeats.
Independent confirmation of a known limitation is worth more than independent
confirmation of a strength.

## Changed as a result

**1. `/invoices/<huge number>` returned 500.** Flask's `<int:>` converter
accepts arbitrary-length digit strings; SQLite integers are 64-bit, so binding
one raised `OverflowError` out of `db.query`. Fixed in `db.py`, not in the
route: every present and future caller passing an `<int:>` parameter has the
same bug, and one guard in the shared function is a smaller change than a range
check in each caller.

```python
except OverflowError:
    # A value too wide to store cannot match any stored row, so the honest
    # answer is "no rows" rather than a 500.
    return []
```

The route's existing `if not rows: abort(404)` then handles it, which means the
oversized-id 404 is byte-identical to every other 404 for free, rather than by a
second code path that could drift. Covered by
`test_an_out_of_range_invoice_id_is_a_plain_404` and
`test_query_treats_an_unstorable_integer_as_no_match`.

**2. `gunicorn` 22.0.0 → 23.0.0** (CVE-2024-6827, request smuggling). Not
introduced by this branch and not exploitable behind Render's edge, but it is
what a routine `pip-audit` would flag first.

**3. A flaky security test, found while fixing the above.**
`test_a_tampered_session_cookie_is_rejected_outright` flipped the *last* base64
character of the session signature. A 32-byte HMAC encodes to 43 base64
characters and the final one carries only two significant bits, so several
different trailing characters decode to identical signature bytes — `a` and `b`
both decode to `…06`, `c` and `d` both to `…07`. When the signature happened to
end in `a` or `b`, the "tampered" cookie was byte-identical after decoding and
remained valid, and the test failed for a reason unrelated to the property it
claimed to test. Roughly a 3% failure rate; it passed 15 consecutive runs before
showing itself.

Replaced with two deterministic tests: one mutating a character in the *middle*
of the signature, where every base64 character is fully significant, and one
that rewrites the cookie payload to claim another tenant's `user_id` while
keeping the original signature. The second is the stronger test — it asserts the
actual property, that you cannot promote yourself across the tenant boundary by
editing your cookie. Both verified stable over 20 consecutive runs.

This one was not a reviewer finding. It surfaced because running the suite
repeatedly during an unrelated fix exposed a nondeterminism nobody had looked
for. A security test that passes 97% of the time is worse than no test, because
it buys confidence it has not earned.

## Deliberately not changed

- **Demo credentials in `seed.py`, live on a public URL, printed to the deploy
  log.** The credentials and the data they unlock are fictional by design and
  published deliberately, so there is nothing to protect. Noted in the README
  rather than fixed.
- **No server-side session revocation.** A stolen cookie stays valid for the
  full 8 hours even after logout. Real, bounded, requires prior cookie theft, and
  documented with the fix named (`session_version` column on `users`).
- **`SESSION_COOKIE_SECURE` gated on `PORTAL_ENV == "production"`.**
  Environment-conditional `Secure` is a footgun in general, but the variable is
  set in `render.yaml` itself and asserted by `tests/test_app_config.py`. The
  failure mode is a misconfigured *future* deployment.
- **`flask.g` is app-context-scoped, not request-scoped.** `g.org_id` could
  persist across requests if an outer app context wrapped several. Under
  `gunicorn "app:create_app()"` each request pushes its own context and no code
  path wraps requests in an outer `app_context()`. Not reachable as deployed, but
  it is the one structural assumption in the design worth knowing about.
- **No login rate limiting.** Out of scope for a one-day demo, and in-process
  counters are theatre on an instance that restarts freely. First thing to add.
