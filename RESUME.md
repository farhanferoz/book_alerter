# RESUME — book_alerter

<!-- ccage budget: keep lean. Update the State sections in place; keep at most
     ~3 ## Session blocks — roll older ones into CHANGELOG. RESUME is auto-read
     into context on every session start, so smaller = cheaper + sharper. -->

## State

### Now
- **Autonomous execution run in progress** (started 2026-09-04 ~15:55). User handed over the
  whole plan and stepped away. Root session = dispatcher + verifier; workers write code.
- **Two shipping bugs found and fixed TODAY beyond the plan**: D33 (Amazon's spend-threshold
  "free over £35" promise recorded as free) and D35's replacement of guessed English markers
  with Amazon's own `CONDITIONALLY_FREE` attribute. A third, **S1/D34**, is the most
  user-visible defect in the project and is being fixed now.
- Performance work is DONE and proven: `GET /api/books` 2101 ms → 453 ms → **63.5 ms** (~33×);
  D23's ≤0.35 s gate closed at 0.059–0.068 s.
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
- **Progress: 33 of 40 ticked** (+T1.2 dropped on evidence = 34 resolved). Root landed this
  session: T1.3, T1.4, T3.2/T4.2/T5.5/T6.1/T6.2/T6.3/T6.5/T6.6 verification+ticks, S4's frontend
  half, the janitor's missing `product-images` sweep, the e2e `last_seen_at` break, and a RUF036
  lint break that only a current ruff sees.
- **Six open, ALL with workers or blocked behind them:** T2.2 + T2.4 + **S1/D34** (stats worker,
  in flight) → T2.3 (Prime UI) and T6.4 (Prime docs) unblock when it lands; T4.1 (capture worker,
  in flight — migration 0023 + property test already on disk); T4.4 (needs `stats.py`).
- **S1/D34 is still the most user-visible open bug.** `TARGET_HIT` fires on a total that omits
  shipping — reproduced end-to-end twice, target £8.00 vs effective £10.79. Fix must move BOTH
  `alerts.py:37` and `compute_signal`'s two target branches together; they interact through
  `prev_signal`, so a half fix lets the dedup suppress the corrected alert next run.
- Review findings status: **S3, S4, S5 fixed. S1 in flight. S2 queued behind T2.2** (cascade
  imputes £0.00 because missingness is not random, and tier 1 shadows tier 2 — production
  magnitude UNMEASURED, SQL in the report). **S6/S7/S8 with the browser worker.**
- **Expect 3 red `_merge_offers` tests** while the browser worker holds `amazon.py` for S8 —
  that is exactly the behaviour S8 changes, not a regression.
- **Orchestrator is single writer for `enums.py`, `api/sources.py`, `scheduler.py`** (write-set
  guard). Its remedy is that the orchestrator writes them — never have a worker override it.
- **Endgame still owed:** review tiers, full plan-adherence audit, push (recipe below),
  `/checkpoint --final`. NAS deploy stays out of scope.
- **Unpinned ruff is a live reproducibility hole**: `pyproject` says `ruff>=0.8` and there is no
  `uv.lock`, so "ruff clean" depends on when your venv was made (0.15.7 here, 0.16.6 fresh).
  Two real errors were invisible on this machine. Pinning is the user's call — flag it.
- Minor litter for the endgame tidy: a stale `git worktree` registered at
  `/tmp/claude-1000/-home-ff235-dev-book-alerter/0c646d24-.../scratchpad/clean-check`.

### Where the container lives (answered 2026-09-04, verified by diff)
- Live: `/share/CACHEDEV1_DATA/Container/book_alerter/docker-compose.yml` on the NAS.
- **Source of truth: `~/dev/workspace-sync/nas/compose/book_alerter/docker-compose.yml`** —
  byte-identical to live, synced one way (repo → NAS) by `nas/deploy_compose.sh`, drift-checked.
  `book_alerter` is deliberately excluded from `tools/fleet-update`, so image updates are manual.
- ⚠️ **TODO (deferred at checkpoint):** this repo's `docker-compose.nas.yml` is a **third copy
  that nothing syncs and has already drifted** (missing `labels: ["lifecycle=service"]`). Delete
  it — README's new "Deploying to the NAS" section now carries the real instructions, and only a
  historical `docs/CHANGELOG.md` line references the file.
- Full runbook: `README.md` → "Deploying to the NAS".

### Deployment handover
- **The runbook now lives in `README.md` → "Deploying to the NAS"** (verified 2026-09-04:
  full docker path required, GHCR build triggers on `master` only, pre-migration backup,
  post-migration `VACUUM`). Deploying is **out of scope** for this run — "ready for
  deployment" is the bar the user set.

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

### Pending gates before the endgame
- ~~Re-run the Docker e2e from committed HEAD~~ — **DONE and GREEN 2026-09-04**: built from an
  isolated `git worktree` at HEAD, `2 passed`. It first caught a real break — `test_docker_smoke`
  injects a `PriceObservation` and had never been ported to the required `last_seen_at`, invisible
  to every green suite because the e2e tests are marked and skipped by default. Fixed in `16b8b57`,
  re-run green. **Always build from a clean checkout; `docker build` bakes in the working tree.**
- **T6.8 not ticked**: still needs negative-case evidence (deliberate stray write → assertion
  fails → removed → passes). Root re-read the assertion 2026-09-04: it is correct, and it
  already guards against the always-passes trap by asserting at least one new path appeared.
  D27's `/home/pwuser/{.cache,.config,.local}` exclusion is present and carries its evidence.
- ~~Plan §8's write-containment assertion is missing~~ — **RESOLVED**: it exists at
  `tests/e2e/test_smoke.py:243`. Only the negative-case evidence above is outstanding.
- ~~T4.2 hard gate (7 fixtures with no test)~~ — **CLEARED**, all 8 product fixtures load.

### Expected in-flight churn (do NOT mistake for regressions)
- `stats.py`, `api/books.py`, `api/products.py`, `app.py`, `scheduler.py` carry the stats
  worker's uncommitted **T2.2 + S1 + T3.4** work. `app.py`'s `MediansCache` import is
  meaningless without `stats.py` — they MUST land in one commit.
- `sources/amazon.py` carries the browser worker's D35 work.
- **Judge the branch from a clean checkout, never from this tree.** Root broke HEAD once today
  by committing `app.py` while it held another worker's edits: HEAD imported a symbol HEAD
  didn't define, and the dirty tree hid it completely. Amended and verified via an isolated
  `git worktree`. **The lesson D25 does not cover: an explicit pathspec does NOT protect a file
  you DID name that already holds someone else's edits — run `git diff <path>` first.**

### Integration status (root-verified on a clean checkout of the branch tip)
- **2026-09-04, commit `31b0b03`: `uv run pytest -q` → 520 passed, 3 skipped, 0 failed** (22 s).
  Baseline before this run was 426 passed. The earlier red state (11 `test_book_stats_view.py`
  failures after migration 0020 dropped the view) is resolved.
- `scripts/smoke_check.py` against a production copy: **12/12 pass in 2.2 s**;
  `GET /api/books` **2101 ms → 453 ms**.
- Checked in an isolated `git worktree` under the session scratchpad so concurrent workers'
  uncommitted edits cannot skew the result — worth repeating that way.

### Threads (only what is still open — settled ones are in CHANGELOG.md)
- **Shipping correctness is the live thread and it got WORSE before better**: the parse-layer
  fix (F1/D20) is real, but the review proved the persist and consume layers reintroduce the
  same class downstream (S1–S4). Fixes in flight.
- Products parity (Wave 5) — **T4.1 is the biggest unstarted item.**
- Bot blocking — T1.1 done (5 commits, incl. D24's per-profile lock and the live canary passing
  against real amazon.co.uk for the first time). T1.3 half done, T1.4 unstarted.
- Cleanup standard — plan §8 binds every task; T6.5 and T6.6 both done.

### Decisions
- See `DECISIONS.md` — D1–D31 in force, Q1/Q2/Q4 open (Q1 and Q3 resolved).
  Run-local rulings from this execution are D20–D31 there; nothing decision-shaped lives here.
### Plan
- `/home/ff235/dev/book_alerter/docs/superpowers/plans/2026-09-04-review-and-optimisation-plan.md`
  — now carries a checkbox per task (39 incl. T6.7); **the checkboxes are the authoritative
  progress record**. Wave order as executed: (0 ∥ 3) → 1 → 2 → 4 → 5 → 6.

### Live jobs & tasks
- Prod DB read-only snapshot in the session scratchpad (`proddb/book_alerter.db` + WAL), pulled
  2026-09-04 15:54. Not in the repo. No probe rows written to production.

## Session 2026-09-04
Two phases in one day. **Morning:** read-only review of the whole codebase against a production
copy, producing the 38-task plan and findings F1–F25.

**Afternoon — autonomous execution of that plan**, four workers under a dispatcher. 22 of 40
tasks ticked (T6.7 and T6.8 were added mid-run; T1.2 was dropped on evidence). The plan's one
stated blocker was cleared first: git history was cloned back from the GitHub remote.

The headline result is that **F1, the shipping bug the plan was written around, is fixed and
proven**. It was not what the plan hypothesised: Amazon serves a cookieless visitor a
*conditional* "FREE delivery … on your first order" promise, and the parser recorded £0.00 —
8 of 9 offers wrong on a live capture. Postcode pinning (the plan's theory) does not work and
made no difference, so T1.2 was dropped and T2.5 now maps a conditional promise to unknown
shipping. Verified on one real 10-offer page: 8 → unknown, the genuinely-free row still free,
the paid row untouched. Also fixed: F3 (unknown shipping ranked as free), F8 (product alerts
written but never shown), F17 (`GET /api/books` 2101 ms → 453 ms), F2, F12, F26.

Three findings corrected the plan itself (`channel="chromium"` does not clear the
`HeadlessChrome` UA; the `#twister` marker is stale; F12 was wrong in a worse way — "Delete"
warned about a cascading delete and silently archived). A new S1, **F26**: Amazon can serve a
*different ASIN's* page with no bot markers, attributing one product's prices to another.

Not finished: the heartbeat-compaction commit (verified but uncommitted — see `### Next`), the
Prime toggle, add-product reliability, and the endgame (review tiers, plan audit, push).

### Unstopped agents at last context boundary
Entries below are SNAPSHOTS from a past boundary, not liveness claims. Before stopping one,
check its subagents/agent-<id>.jsonl mtime; if TaskStop replies 'No task found', the agent is
ALREADY dead — that is SUCCESS, not an error to retry. Delete handled lines.
- (none recorded yet this session — four workers were live at the last update: W-T31-stats
  on T2.2+S1, W-T11-browser on D35, W-T01-capture on S3, plus a finished shipping review.)
