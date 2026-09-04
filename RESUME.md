# RESUME — book_alerter

<!-- ccage budget: keep lean. Update the State sections in place; keep at most
     ~3 ## Session blocks — roll older ones into CHANGELOG. RESUME is auto-read
     into context on every session start, so smaller = cheaper + sharper. -->

## State

### Now
- **All 40 plan tasks are RESOLVED** (39 ticked + T1.2 dropped by its own gate, D21).
  Final gate at HEAD from an isolated worktree: **633 passed**, ruff clean, frontend
  tsc/eslint/build clean, `smoke_check` 12/12, `bench_stats` **0.093 s** vs a 0.35 s gate.
  Branch `wave-execution` is **pushed**; working branch off `master`.
- **THE MERGE TO `master` IS BLOCKED.** That merge is what ships (it triggers the GHCR
  build), and two reviews returned blocking findings after the plan was complete. Nothing
  in the plan remains — the entire remaining path to release is those findings.
- Four agents were live at checkpoint: stats (Wave 3 Tier 4 fixes), prime (frontend fixes),
  capture (simplify pass), review-backend (verdict not yet returned).
- `/keepwarm` armed: 55 min interval, 6 pings, auto-stops ~23:57.

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
  **CORRECTED 2026-09-04 — the previous entry here was WRONG.** It claimed `npx tsc` / `npx
  eslint` "silently no-op under npm 12"; that is false and was propagated into several worker
  briefs as measured fact without being checked. `npx` works fine (it just prints npm notices).
  **The real defect is the missing `-b`.** Measured, by breaking `web/src/lib/format.ts` with a
  real type error in a throwaway worktree and running all four combinations:
  - `tsc --noEmit` (no `-b`) → **exit 0, error NOT reported** — via `npx` AND via the direct
    binary. This project uses TypeScript **project references**, so without `-b` tsc checks
    nothing.
  - `tsc -b --noEmit` → **exit 2, error reported** — again via `npx` and directly, identically.
  So: **always `-b`**; `npx` vs the direct binary is irrelevant. ⚠️ **Plan §4's conventions
  paragraph prescribes `npx tsc --noEmit`, without `-b` — that gate passes regardless of type
  errors.** No harm done in practice: every frontend task this session was briefed with
  `tsc -b --noEmit` and used it.
  Install deps with `npm ci --legacy-peer-deps` (the Dockerfile's own convention; a plain
  `npm ci` fails on a TS 6 vs openapi-typescript peer conflict).
- **Migrations:** point alembic at a copy with `BOOK_ALERTER_DATABASE_URL="sqlite:///<path>"`.
  **`alembic -x db_url=...` is silently IGNORED** — `env.py` calls `get_database_url()`, which
  reads only that env var. A round-trip test that forgets this migrates the app's own
  `data/book_alerter.db` instead and looks like a broken migration. (Cost me one false alarm on
  0020; that local dev DB moved 0018→0019 as a result, which is harmless.)
- Measured performance baselines to beat: `GET /api/books` **2101 ms** via the API harness;
  1.455 s for 13 per-book stats queries vs 0.124 s for one all-books query.

### Next — IN PRIORITY ORDER, all block the merge
1. **Wave 3 Tier 4 FAIL — with the stats worker.** Report:
   `<scratchpad>/tier4-wave3-review.md`. The forward backfill was CERTIFIED SOUND (0
   mismatches over 12,337 rows; every consumer output identical for all 13 books). Three
   items stand: **F-A HIGH** — `downgrade→upgrade` silently wrecks the live-offer view
   (212 rows → **24**, 11 of 13 current-bests change, **7 signal flips**, integrity+FK
   clean throughout); **F-B MED** — 0021 keeps `last_seen_at` but **discards `current_url`**,
   reverting migration 0019 (187 rows stale, worst case an Amazon *help page*); **F-C MED,
   latent** — D14's raw-`total_minor` rank still in SQL, zero production reachability.
   **Both round-trip tests run on an EMPTY DB and the property test's table has no `url`
   column, so neither could ever have caught F-A or F-B** — fix the tests too.
2. **Frontend review — 2 HIGH + 4 MED, with the prime worker, NONE FIXED.** Report:
   `<scratchpad>/review-web.md`. **F1 HIGH, unrecoverable**: the add-product debounce race
   creates a product carrying a *different* ASIN's title/image; backend stamps it `"ok"` and
   all repair paths filter on `PENDING`, so only Delete escapes — which also destroys the
   Keepa history. **F2 HIGH**: `SignalCard` reads the raw total while its own pill uses the
   effective one, and `02bdc1e` made the two cards visibly disagree. F3/F5/F6/F4 in the report.
   **F2+F5+F3 are ONE rule** (D34: no user-facing price comparison reads
   `current_best_total_minor`; unknown never ranks cheapest) that has leaked to **six** sites
   and been fixed piecemeal each time — grep `web/src` for the field, don't fix one line.
3. **W-review-backend's verdict has not returned** — chase it before merging.
4. Then: merge to `master` (recipe below), which triggers the GHCR build, then
   `/checkpoint --final`. **NAS deploy stays out of scope.**
5. **D39 post-deploy obligation** — deploying does NOT fix stored prices; see README
   → "After the first deploy carrying the shipping fixes".

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

### Pushing — **BRANCH IS PUSHED** (2026-09-04, 122 commits at `8fbe828`)
`wave-execution` is on `github.com/farhanferoz/book_alerter`. Deliberately the branch only:
a branch push does NOT trigger `.github/workflows/build.yml`, which fires on **`master`**
and publishes the GHCR image. **The merge to `master` is the remaining step and is the one
that makes a release**, so it waits until the review verdicts are in — a review finding
could still change what ships. Re-push with the same recipe as more commits land.

### Post-deploy obligation (D39) — do NOT skip
Deploying does not correct the prices already stored. Measured: all 13 books carry an
observed `shipping_minor = 0` written by the pre-fix parser, so the app shows free delivery
for every one. Values correct themselves per source as scrapes run. **Then re-measure the
cascade** — the estimate for newly-unknown rows is a median over data still dominated by
those old zeros, so it can land near £0.00 and reintroduce the same harm by another route.
Query and full reasoning: README → "After the first deploy carrying the shipping fixes".

### Pushing recipe (verified 2026-09-04)
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

### Review-tier tracker (plan §5; a DONE criterion nothing else tracks)
- **Wave 0** n/a · **Wave 3** Tier 4: property-tests-first ✅, fresh-session review **DONE →
  FAIL** (see `### Next` item 1) · **Waves 1/2/4/5/6** Tier 2 **NOT RUN as specified**.
- **DEVIATION, stated not silent:** §5's Tier 2 is `simplify` → `find-bugs` →
  `/second-opinion` → `fp-check` **per wave**. Run instead: two branch-wide adversarial
  reviews (backend, frontend) plus a `simplify` pass. Reason: the waves interleave in the
  same files, so a per-wave slice re-reviews the same code while missing the cross-wave
  interactions where every real bug has lived. **This is a substitution, not the tier** — it
  does not discharge `/second-opinion` or `fp-check`. Judge it from the reports.

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

### Judging the branch
- **Always from a clean checkout, never the working tree** — other agents' uncommitted work
  hides real breakage. Root broke HEAD once today by committing `app.py` while it held
  another worker's edits. **The lesson D25 does NOT cover: an explicit pathspec does not
  protect a file you DID name that already holds someone else's edits — run
  `git diff <path>` first.** Verify with `git worktree add --detach <tmp> HEAD`.

### Integration status (root-verified on a clean checkout of the branch tip)
- **FINAL GATE, 2026-09-04, HEAD `de78ec5`, isolated worktree — ALL 40 PLAN TASKS RESOLVED**
  (39 ticked + T1.2 dropped by its own gate, D21):
  `uv run pytest -q` → **633 passed, 3 skipped** (run baseline was 426) ·
  `ruff check src tests scripts` → clean · frontend `tsc -b --noEmit` / `eslint .` /
  `npm run build` → clean · against a migrated production copy:
  **`smoke_check.py` 12/12 in 0.74 s**, **`bench_stats.py` 0.093 s** for 13 books against
  D23's ≤0.35 s gate. `git status --short` clean.
- Janitor verified end-to-end against a COPY of the real `data/` (never the live one): sweeps
  run, 28 files / 0.66 MB reclaimed. `data/debug` holding at exactly its 20-file cap per
  source — **correct, not a leak**; note it is a COUNT cap while browser profiles use a SIZE
  cap, so a source dumping 2 MB pages legitimately holds ~44 MB.
- **2026-09-04, HEAD `fc433ba`, from an isolated worktree (never this dirty tree):**
  `uv run pytest -q` → **605 passed, 3 skipped**; `import book_alerter.app` OK.
- **Against a real production copy, same clean HEAD:** migration 0019→head clean
  (**90,172 → 12,337** `priceobservation` rows), `PRAGMA foreign_key_check` clean,
  **`smoke_check.py` 12/12 PASS in 1.76 s**, `GET /api/books` **96 ms** (was 2101 ms),
  **`bench_stats.py` 0.163 s** for 13 books — inside D23's ≤0.35 s gate with room to spare.
  smoke_check's invariants include the T6.2 one ("signal is a member of Signal").
  **These are the run-contract DONE criteria, met at this commit.** Re-run after T2.2/T4.1 land.
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
- See `DECISIONS.md` — **D1–D37** in force, Q2/Q4 open (Q1 and Q3 resolved). New today:
  D32 (variant_asin dropped, F26's guard wins), D33 (spend-threshold promise), D34 (**S1** — every
  target comparison reads the effective total), D35 (key on Amazon's `CONDITIONALLY_FREE` attribute,
  English markers kept as fallback), D36 (S7's shape: threshold ≠ charge, never reorder precedence),
  D37 (S8 deferred — `None` now means two things and telling them apart needs a signal the candidate
  doesn't carry; revisit when dp and AOD share a (seller, condition, price)).
  Run-local rulings from this execution are D20–D37 there; nothing decision-shaped lives here.
### Plan
- `/home/ff235/dev/book_alerter/docs/superpowers/plans/2026-09-04-review-and-optimisation-plan.md`
  — now carries a checkbox per task (39 incl. T6.7); **the checkboxes are the authoritative
  progress record**. Wave order as executed: (0 ∥ 3) → 1 → 2 → 4 → 5 → 6.

### Live jobs & tasks
<!-- /clear wipes these. Agents do NOT survive; re-dispatch from ### Next. -->
- Agents live at checkpoint (all dead after a clear — their COMMITTED work is safe in git,
  their in-flight work is not): stats (Wave 3 F-A/F-B/F-C), prime (frontend F1–F6),
  capture (`simplify` pass), review-backend (verdict never returned — **re-run it**).
- `/keepwarm` armed 55 min × 6, auto-stops ~23:57. Re-arm only if stepping away again.
- Reports to read before acting: `<scratchpad>/tier4-wave3-review.md`,
  `<scratchpad>/review-web.md` — both survive in the session scratchpad.
