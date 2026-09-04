# RESUME — book_alerter

<!-- ccage budget: keep lean. Update the State sections in place; keep at most
     ~3 ## Session blocks — roll older ones into CHANGELOG. RESUME is auto-read
     into context on every session start, so smaller = cheaper + sharper. -->

## State

### Now
- **Autonomous execution run in progress** (started 2026-09-04 ~15:55). User handed over the
  whole plan and stepped away. Root session = dispatcher + verifier; workers write code.
- Git blocker (F21) is **RESOLVED**: history cloned from `github.com/farhanferoz/book_alerter`
  and installed at `/home/ff235/dev/book_alerter/.git`. Working copy was byte-identical to
  `origin/master` for every tracked file. Working branch: **`wave-execution`** (off `master`).

### Run contract (binding for this autonomous run)
- **In scope:** all 38 tasks of the 2026-09-04 plan (T0.1–T6.6) plus T6.7 (validation harness,
  added 2026-09-04 — see Decisions).
- **DONE means:** every plan checkbox ticked; `uv run pytest -q` green; `uv run ruff check src
  tests scripts` clean; the web pipeline (`tsc --noEmit`, `eslint`, `npm run build`) clean;
  `scripts/smoke_check.py` green against a copy of the production DB; the T3.1 benchmark at
  **≤0.35 s** for 13 books; `git status --short` showing only intended files; every wave's
  review tier run (Tier 2, or Tier 4 for migrations).
- **Pre-declared deferrals — OUT of scope, decided before work started:**
  1. ~~Deploy to the NAS and `git push`~~ — **AMENDED mid-run 2026-09-04** on an explicit user
     instruction: "done means Reviewed, code is ready, everything is pushed, and it's ready for
     deployment". So **`git push` IS authorized** (branch, then merge to `master` so the GHCR
     image builds and the work is genuinely deployment-ready). **Running the NAS deploy is still
     NOT authorized** — the bar the user set is *ready for* deployment. The pre-deploy backup is
     in scope; `docker compose pull && up -d` on the NAS is handed over as a one-line command.
     Also required at the end: a full plan-adherence audit for gaps, then `/checkpoint --final`.
  2. Everything in plan §7 (Telegram/Pushover, Sentry, Go CLIs, proxies, basket-level delivery,
     Amazon PA API) — unchanged.
  3. T6.3 (periodic Keepa refresh) ships **default-off**, as the plan already specifies.

### Validation commands (all fast — measured 2026-09-04)
Run from `/home/ff235/dev/book_alerter` unless stated.
- `uv run pytest -q` — **~26 s**, baseline before this run was 426 passed / 3 skipped.
- `uv run ruff check src tests scripts` — seconds.
- `uv run python scripts/smoke_check.py --db <copy-of-prod.db>` — **~8 s**, 12 real end-to-end
  checks against a copy of the production database (boots the app in-process, hits the real
  endpoints, asserts data invariants). Verified to FAIL correctly on poisoned data (exit 1).
- `uv run python scripts/bench_stats.py <copy-of-prod.db>` — T3.1 benchmark.
- Frontend, from `web/`: `./node_modules/.bin/tsc -b --noEmit` (~3.5 s) ·
  `./node_modules/.bin/eslint .` (~4 s) · `npm run build` (~4.3 s).
  **Gotcha:** `npx tsc` / `npx eslint` silently no-op under npm 12 here — call the binaries in
  `node_modules/.bin/` directly. Install deps with `npm ci --legacy-peer-deps` (the Dockerfile's
  own convention; a plain `npm ci` fails on a TS 6 vs openapi-typescript peer conflict).
- **Migrations:** point alembic at a copy with `BOOK_ALERTER_DATABASE_URL="sqlite:///<path>"`.
  **`alembic -x db_url=...` is silently IGNORED** — `env.py` calls `get_database_url()`, which
  reads only that env var. A round-trip test that forgets this migrates the app's own
  `data/book_alerter.db` instead and looks like a broken migration. (Cost me one false alarm on
  0020; that local dev DB moved 0018→0019 as a result, which is harmless.)
- Measured performance baselines to beat: `GET /api/books` **2101 ms** via the API harness;
  1.455 s for 13 per-book stats queries vs 0.124 s for one all-books query.

### Next
- **Progress: 19 of 40 ticked.** Part-done: T2.4 (estimate flag), T6.1 (banner), T6.2
  (stats/alerts switch), T6.4 (Prime/schedules/VACUUM docs), T6.5 (scheduler registration).
  T1.2 dropped.
- In flight: **T3.2 heartbeat compaction (Tier 4) + T3.4 + T6.2 rest** (W-T31-stats) ·
  **T2.5 conditional-delivery fix** (W-T11-browser) · **T5.3 + T5.4** (W-T67-smoke) ·
  **adversarial review of the shipping chain, read-only** (W-T01-capture).
- Not started: T1.3, T1.4, T2.2, T2.3, T4.1, T4.2 (hard gate — 7 fixtures with no test),
  T4.4, T5.5, T6.3, T6.6 fixture renames.
- **The orchestrator is the single writer for `enums.py` and `api/sources.py`** — the write-set
  guard holds both after two agents legitimately needed them. Guard's own remedy; do not have a
  worker override it.
- **Endgame:** review tiers, plan-adherence audit, push (recipe above), `/checkpoint --final`.
  NAS deploy is explicitly out of scope.

### Pushing (verified 2026-09-04, dry-run only — nothing pushed yet)
The remote is `github.com/farhanferoz/book_alerter`, but `gh`'s **active** account is
`reviewsenseai`, so a plain `git push` fails with `Permission ... denied to reviewsenseai`.
`farhanferoz` is also authenticated in `gh`. Scope the credential to the one command rather than
running `gh auth switch` (which would change the user's global active account):

```bash
GH_TOKEN=$(gh auth token --user farhanferoz) git push origin wave-execution
```

Verified with `--dry-run`: `* [new branch] wave-execution -> wave-execution`. Note that a push to
**`master`** triggers `.github/workflows/build.yml` and publishes a GHCR image — that is the step
that makes the work deployable, and it is the last thing to do, after review.

### Verified operational facts (root, 2026-09-04)
- **Backup restore path is lossless.** A real 49.9 MB backup compresses to 6.6 MB (86.8%), and
  `gunzip -c <file>.gz > book_alerter.db` yields a **byte-identical** database (md5 match) that
  boots and serves. Satisfies the §8 clause "restore path documented and tested once against a
  copy". The README restore instructions are correct as written.
- **Janitor against real NAS artefacts:** 87.8% of sampled bytes freed in 0.84 s; every deletion
  audited individually (stale keepa charts past the 24 h-TTL horizon, plus one orphaned chart and
  cover for an item no longer tracked).
- **Fixture/test audit:** 7 committed product fixtures currently have no test loading them —
  T4.2 is now a hard gate to fix that (see its plan entry).
- **Logging audit:** no per-item INFO logging in the source layer; §8 clause met.

### Pending gates before the endgame
- **Re-run the Docker e2e from committed HEAD once T3.2 lands.** A run on 2026-09-04 gave
  `test_docker_smoke` FAILED / `test_write_containment_during_scheduler_run` passed — but the
  image was built from a working tree holding the half-finished heartbeat-compaction change
  (`models.py`, `views.py` uncommitted). `docker build` bakes in the working tree, so **never
  judge e2e from a dirty tree**; build from a clean checkout.
- **T6.8 not ticked yet**: still need the worker's negative-case evidence (deliberate stray write
  → assertion fails → removed → passes). The assertion itself reads correctly.
- **T4.2 is a hard gate**: 7 committed product fixtures have no test loading them.

### Expected in-flight churn (do NOT mistake for regressions)
- As of 2026-09-04 ~16:10 the working tree holds the **uncommitted** heartbeat-compaction change
  (`db/models.py`, `db/views.py`, `stats.py`, `config.py`, migration 0020/0021). While it is
  uncommitted, a scoped run shows ~12 failures, all traceable to
  `NOT NULL constraint failed: priceobservation.last_seen_at` — the new required model field.
  **Judge the branch from a clean checkout, never from this tree.**
- Raised with the stats worker: `last_seen_at` should default to `observed_at` on the model
  (39 construction sites otherwise need updating, and a forgetful future caller gets a runtime
  IntegrityError instead of correct behaviour). Their call, but it must be deliberate.
- `tests/integration/test_unknown_shipping_ranking.py` is affected either way.

### Integration status (root-verified on a clean checkout of the branch tip)
- **2026-09-04, commit `31b0b03`: `uv run pytest -q` → 520 passed, 3 skipped, 0 failed** (22 s).
  Baseline before this run was 426 passed. The earlier red state (11 `test_book_stats_view.py`
  failures after migration 0020 dropped the view) is resolved.
- `scripts/smoke_check.py` against a production copy: **12/12 pass in 2.2 s**;
  `GET /api/books` **2101 ms → 453 ms**.
- Checked in an isolated `git worktree` under the session scratchpad so concurrent workers'
  uncommitted edits cannot skew the result — worth repeating that way.

### Threads
- Shipping inconsistency (F1–F7) — Wave 1 (browser identity, postcode pinning) + Wave 2 (WoB
  rule, Prime toggle, effective-total ranking). Wave 0 captures settle Q1.
- Products never tracked (F7–F12) — Wave 4 then Wave 5 parity.
- Bot blocking (F13–F16) — root driver. Wave 1.
- Performance (F17–F20) — Wave 3. Baseline re-measured 2026-09-04 on the prod copy:
  **1.455 s** for 13 per-book stats queries vs **0.124 s** for one all-books query; 90,172
  observation rows of which 77,835 (86%) are heartbeats. Plan's evidence base confirmed.
- **T3.1 view-equivalence evidence (root-verified 2026-09-04):** on the production copy, taking
  `MIN(total_minor)` from the new `book_live_offers` view with the documented tie-break
  (source, condition, `COALESCE(seller,'')` ascending) reproduces the old
  `book_stats.current_best_*` for **all 13 books, 0 mismatches**, and no book lost its
  current-best. Migration 0020 also round-trips clean (upgrade, downgrade, `foreign_key_check`).
- **Open gap found 2026-09-04 (not yet a numbered task):** plan §8 requires "the Docker e2e test
  asserts that the only new paths after a scheduler run are under `/app/data`". No such
  assertion exists — `tests/e2e/test_smoke.py` does not check write containment. Deliberately
  deferred rather than written blind, because verifying it needs a Docker build and the
  Dockerfile area was being edited concurrently. **Address in the final plan-adherence audit.**
- Cleanup standard — plan §8 binds every task; janitor T6.5; repo tidy T6.6.

### Decisions
- See `DECISIONS.md` (D1–D19 in force; Q1–Q4 open). Run-local decisions taken autonomously:
- **D20** (2026-09-04) Git history restored by cloning the GitHub remote and moving `.git` into
  the existing working copy, rather than relocating work to a fresh clone. Reversible; the
  working tree was already identical to `origin/master`. Continuity files (`CHANGELOG.md`,
  `DECISIONS.md`, ccage pid files) are excluded via `.git/info/exclude` so no task commit can
  sweep them in. `RESUME.md` is tracked upstream already — left tracked, never staged by tasks.
- **D21** (2026-09-04) Waves 0 and 3 run **concurrently**, deviating from the plan's stated
  `0 → 1 → (2 ∥ 3)` ordering. Rationale: their write sets are provably disjoint (Wave 0 =
  `scripts/`, `tests/fixtures/`, the probe-results doc; Wave 3 = `stats.py`, `db/views.py`,
  migrations, `api/`), and T0.2 has an irreducible ≥2 h wall-clock requirement that would
  otherwise idle the run. No Wave 3 task consumes a Wave 0 output.
- **D22** (2026-09-04) Added **T6.7: validation harness** (`scripts/smoke_check.py`) — boots the
  real app against a copy of the production DB and exercises the real endpoints, asserting
  structural invariants and timing. Rationale: the user's explicit instruction that validation
  must be real ("not just unit tests") and fast (<1 min). Built early so it captures baseline
  behaviour and can then catch regressions from every later wave. It asserts health/shape/timing
  invariants, never the buggy pricing behaviour Wave 2 is fixing.

### Plan
- `/home/ff235/dev/book_alerter/docs/superpowers/plans/2026-09-04-review-and-optimisation-plan.md`
  — now carries a checkbox per task (39 incl. T6.7); **the checkboxes are the authoritative
  progress record**. Wave order as executed: (0 ∥ 3) → 1 → 2 → 4 → 5 → 6.

### Live jobs & tasks
- Prod DB read-only snapshot in the session scratchpad (`proddb/book_alerter.db` + WAL), pulled
  2026-09-04 15:54. Not in the repo. No probe rows written to production.

## Session 2026-09-04 (review)
Read-only review of the whole codebase against a production DB copy and the live NAS instance.
Established that shipping is now rarely missing but the same Amazon offer flips between £0 and
£2.80 across scrapes (337 of 2,952 offers), WoB shipping is hard-coded to zero, unknown shipping
ranks as free, and there is no Prime setting; that no product has ever existed in production
although the add → refetch → stats flow works when Amazon isn't serving a bot challenge; that
the browser presents `HeadlessChrome` from a fresh profile per item; and that the dashboard runs
the stats view once per book. Plan written with 38 tasks in six waves plus a binding cleanup
standard; decisions D10–D19 and open questions Q1–Q4 recorded.
