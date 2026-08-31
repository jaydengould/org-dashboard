# TODO

Ordered by the build sequence in
`docs/superpowers/plans/2026-08-30-client-portal.md`. Target: deployed and
demoable within one working day.

## Blocking on the user

- [ ] Confirm the assumptions in `SPEC.md` §8: read-only (no write routes),
      one user per org with no roles, evaluator reads the repo rather than only
      clicking the link, `.test` email addresses acceptable.
- [ ] Confirm CSRF is in scope. It is currently planned in (Task 6). It was not
      in the original brief.

## Build

Three gates punctuate the eleven tasks. A task is done when its tests pass; a
gate is done when a person is satisfied.

- [ ] Task 1 — schema, `db.connect/init/get_conn`, FK pragma, schema tests
- [ ] Task 2 — app factory + `/healthz`, `render.yaml`, **deploy the skeleton**
- [ ] **Gate 1** — public URL answers `/healthz`. Do not continue until it does.
- [ ] Task 3 — `db.query` guard + identity lookups, guard tests
- [ ] Task 4 — `seed.py`, three fictional orgs, idempotent; add seed to boot
- [ ] Task 5 — `before_request` gate, login end to end, templates
- [ ] Task 6 — POST logout, per-session CSRF, session cookie config
- [ ] Task 7 — dashboard tiles + monthly figures table + invoice list
- [ ] Task 8 — `/invoices/<id>` with 404-on-foreign-row, isolation suite
- [ ] Task 9 — structural guard test, then **break the app four ways on purpose**
      and record it in `docs/breaking-it.md`
- [ ] **Gate 2** — `/security-review` over the boundary; record what you changed
      and what you dismissed, with reasons
- [ ] Task 10 — inline SVG chart (hard stop: no axes, no tooltips)
- [ ] Task 11 — verify tampering against the production URL, write README
- [ ] **Gate 3** — explain the design out loud, from memory, laptop closed

## Before the interview

- [ ] Re-run the URL-tampering check by hand against the public URL
- [ ] Open the Render URL ~5 minutes ahead so the free instance is awake
- [ ] Be able to say from memory: the two chokepoints; why 404 not 403; why the
      session holds only `user_id`; that the cookie is signed, not encrypted;
      and what the `:org_id` string check does *not* catch

## Explicitly not doing

Signup, password reset, email, roles, admin views, rate limiting, MFA, audit
logging, any write path, mobile layouts, CSS or JS frameworks, persistent disk
on Render.
