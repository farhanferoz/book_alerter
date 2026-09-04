# RESUME — book_alerter

<!-- ccage budget: keep lean. Update the State sections in place; keep at most
     ~3 ## Session blocks — roll older ones into CHANGELOG. RESUME is auto-read
     into context on every session start, so smaller = cheaper + sharper. -->

## State

### Now
- **RELEASE SHIPPED 2026-09-04.** `master` fast-forwarded to `917996b` and pushed; the GHCR build
  (run `33905984011`) completed **success** in 3m47s and pushed
  `ghcr.io/farhanferoz/book_alerter` at digest `sha256:e82d53fc7e670f4b40f0833ee9d9677d3efc1027b8945f22dc10257ff3830082`,
  tags `latest` / `sha-917996b`, revision label matching the release commit. `master` is now at
  `3857a5a` (a docs-only follow-up; README is outside the workflow's paths filter, so it correctly
  did NOT rebuild — the image still corresponds to `917996b`).
- **All 40 plan tasks resolved** (39 ticked + T1.2 dropped by its own gate, D21) **and all 37
  findings across four reviews resolved**: Tier 4 5/5, backend 10/10, shipping-chain 8/8,
  frontend 14/14.
- **Gates at the release commit, all green:** `pytest -q` **656 passed / 3 skipped** · ruff clean ·
  `tsc -b` + `eslint` + `npm run build` clean · **Docker e2e 2 passed** (image rebuilt from a clean
  checkout at HEAD) · migrations 0019→head on a production copy clean (FK + integrity ok,
  90,172 → 12,337 rows) · `smoke_check` **12/12 in 0.81 s** · `bench_stats` **0.086 s** vs the
  0.35 s gate (D23) · `git status --short` clean.

### Run contract (binding for this autonomous run)
- **In scope:** the 2026-09-04 plan (T0.1–T6.6 + T6.7), and the fix passes for all four reviews.
- **DONE means:** plan checkboxes ticked; `uv run pytest -q` green; `ruff check src tests scripts`
  clean; web `tsc -b --noEmit` / `eslint .` / `npm run build` clean; `smoke_check.py` green against
  a production copy; `bench_stats.py` ≤ **0.35 s** for 13 books (D23); `git status --short` clean;
  every wave's review tier run.
- **`git push` IS authorized** (branch, then merge to `master` so the GHCR image builds).
  **Running the NAS deploy is NOT** — the bar is *ready for* deployment.
- **Out of scope:** plan §7 (Telegram/Pushover, Sentry, Go CLIs, proxies, basket-level delivery,
  Amazon PA API). T6.3 ships default-off.

### Validation commands (all fast — measured 2026-09-04)
Run from `/home/ff235/dev/book_alerter`.
- `uv run pytest -q` — ~36 s. **Latest full-suite green: 656 passed / 3 skipped at `47c0ef6`.**
- `uv run ruff check src tests scripts` — seconds, clean.
- `uv run python scripts/smoke_check.py --db <copy-of-prod.db>` — **12/12 in 0.62 s**.
- `uv run python scripts/bench_stats.py <copy-of-prod.db>` — **0.065 s** vs D23's 0.35 s gate.
- Frontend, from `web/`: `./node_modules/.bin/tsc -b --noEmit` · `./node_modules/.bin/eslint .` ·
  `npm run build`. **The `-b` is mandatory** — this project uses TypeScript project references and
  without it tsc checks nothing and exits 0 with a real type error present (measured).
  Install deps with `npm ci --legacy-peer-deps`.
- **Migrations:** point alembic with `BOOK_ALERTER_DATABASE_URL="sqlite:///<path>"`.
  **`alembic -x db_url=...` is silently IGNORED** — `env.py` reads only that env var.
- Production snapshot for verification work:
  `/tmp/claude-1000/-home-ff235-dev-book-alerter/9afc7be5-.../scratchpad/proddb/book_alerter.db`
  — the **original pre-migration** copy at revision `0019`, 90,172 rows. Copy it; never work in place.

### Next
**UI polish — execution plan ready, Task 1 code on disk.** Branch `ui-polish` (from `master` @ `755b822`)
carries four UNCOMMITTED, gate-clean edits (`columns.tsx`, `AlertItem.tsx`, `AlertsSidebar.tsx`,
`KeepaChart.tsx`). Plan, with every remaining step's code and the verification harness:
`/home/ff235/dev/book_alerter/docs/superpowers/plans/2026-09-04-ui-polish-plan.md` (untracked — commit it
first). Read the plan, not this bullet: Task 1 = commit the skeleton; Task 2 = HistoryChart; Task 3 = README
runbook I1/I2; Task 4 = screenshots vs baselines; Task 5 = simplify + handoff (no merge, no push).

**Nothing blocks the release — it is shipped.** What remains is operator work, deliberately out of
scope for the autonomous run (the bar set was *ready for* deployment):

1. **Deploy to the NAS** when you want it live: README → "Deploying to the NAS". Back up first
   (this release carries migration `0021`), then `compose pull && up -d`, then the **mandatory
   `VACUUM`** — 0021 deletes 86% of `priceobservation` and SQLite will not return the pages
   without it.
2. **D39's post-deploy obligation — do NOT skip.** Deploying does not correct stored prices.
   Re-measured at HEAD: **2,780 zero-shipping rows across all 13 books**, so the app will keep
   showing free delivery for every tracked book until scrapes re-run. **Then re-measure the
   cascade**: those legacy zeros stay in the 365-day window and can drag the estimate for
   newly-unknown rows toward £0, reintroducing the same harm by another route. If it does, a
   *dated* exclusion (rows written before T2.5) is the justified fix — a fact about which parser
   wrote them, not a heuristic about the data.
3. **Optional, user's call:** the two tooling gaps under "Tooling gaps found 2026-09-04".

### Pushing
```bash
GH_TOKEN=$(gh auth token --user farhanferoz) git push origin wave-execution
```
`gh`'s ACTIVE account is `reviewsenseai`, so a plain `git push` is denied. Scope the credential to
the one command rather than `gh auth switch` (which changes the global account).

### Post-deploy obligation (D39) — do NOT skip
Deploying does **not** correct stored prices. Re-measured at HEAD: **2,780 zero-shipping rows across
all 13 books**, so the app shows free delivery for every tracked book until scrapes re-run. Values
correct per source as they run. **Then re-measure the cascade** — the legacy zeros stay in the
365-day window and can drag the estimate for newly-unknown rows toward £0, reintroducing the same
harm by another route. Query and reasoning: README → "After the first deploy carrying the shipping
fixes".

### Deployment handover
- Runbook: `README.md` → "Deploying to the NAS" (full docker path required, GHCR build on `master`
  only, pre-migration backup, post-migration `VACUUM`).
- Compose source of truth: `~/dev/workspace-sync/nas/compose/book_alerter/docker-compose.yml`,
  synced one way (repo → NAS) by `nas/deploy_compose.sh`. Live at
  `/share/CACHEDEV1_DATA/Container/book_alerter/docker-compose.yml`. `book_alerter` is deliberately
  excluded from `tools/fleet-update`, so image updates are manual.
- This repo's drifted third copy `docker-compose.nas.yml` was **deleted** (`f92deb3`).

### Judging the branch
- **Always from a clean checkout, never the working tree** — concurrent agents' uncommitted work
  hides real breakage. `git worktree add --detach <tmp> HEAD`.
- **An explicit pathspec does not protect a file you DID name that already holds someone else's
  edits** — run `git diff <path>` first (D25/D28). Zero index collisions this run under that rule.

### Tooling gaps found 2026-09-04 (user's call, not changed)
- **`write_set_guard` matches only `Write|Edit`** (`~/.claude/settings.json:410`), not `Bash`. In
  bypass-permissions mode the harness instructs agents to prefer Bash for edits, so every edit made
  the recommended way skips the guard. It also reported stale locks for agents that had already
  committed and gone idle.
- **`test_scope_guard`** escalates as pytest artifacts dirty the tree, and scores relevance from the
  main tree even when the command runs in an isolated worktree.

### Plan
- `/home/ff235/dev/book_alerter/docs/superpowers/plans/2026-09-04-review-and-optimisation-plan.md`
  — checkboxes are the authoritative progress record. The single open box is T1.2, dropped by D21.

### Decisions
- See `DECISIONS.md` (auto-loaded) — **D1–D45**. New 2026-09-04 in this session: D40 (F7 closed by
  removing the second shape, not splitting query keys), D41 (three pre-existing defects deferred),
  D42 (F-B5 counts a failed retry, not a successful one), D43 (FAILED metadata retried on a fixed
  24 h cadence), D44 (backend reviewer's percentile-window regression claim **refuted** by two
  independent measurements), D45 (standing property: a flat-priced offer contributes nothing to its
  own percentile window, pre-existing and harmless).

### Review reports (survive in the 9afc7be5 session scratchpad)
`tier4-wave3-review.md` · `review-backend.md` · `review-web.md` · `shipping-chain-review.md`.
**Note:** `review-backend.md`'s F-B6 fixture counts (9/48) are **wrong** — measured twice as
**10 `CONDITIONALLY_FREE` / 47 empty / 57 total**. Its cross-wave percentile claim is refuted (D44).

### Live jobs & tasks
<!-- /clear does NOT necessarily kill agents — verified 2026-09-04, several survived a clear and
     were still writing files. Check subagents/agent-<id>.jsonl mtime before assuming one is dead. -->
- none. All six workers stopped cleanly at end of run; no background jobs armed; no worktrees left
  (`git worktree list` shows only the main checkout).

## Session 2026-09-04
Resumed after a `/clear` that had **not** killed the running agents — three were still live and
writing files, so the plan became "query and coordinate" rather than "re-dispatch", which avoided
colliding in the shared working tree. The blocking set was larger than the checkpoint recorded: the
backend review's verdict had in fact returned after the checkpoint was taken, carrying 3 HIGH +
4 MEDIUM + 3 LOW. Dispatched three fix workers onto disjoint write sets and coordinated the three
survivors; 37 findings across four reviews were resolved, with zero index collisions under the
explicit-pathspec rule. Verified the two highest-risk fixes myself against the real pre-migration
production snapshot rather than trusting reports, and refuted the one claim that would have blocked
the merge (D44) by measurement, confirmed independently by a second agent. Shipped: `master` at
`917996b`, GHCR build success, image published and verified by digest. Remaining work is operator
work only — the NAS deploy, the post-0021 `VACUUM`, and D39's post-deploy re-measurement.

### Final review 2026-09-04 (session 6e5ea529, after the release)
Fresh-eyes review of the whole delta from a clean worktree: gates re-run green (656/3 skipped, ruff,
tsc -b/eslint/build, smoke 12/12, bench 0.165 s under load, migration 0019→0024 1.5 s, VACUUM 0.12 s
51→5.7 MB). Three reviewers (backend fix commits, frontend delta, plan adherence): **0 code defects**;
migration 0021 round trip re-verified on real prod data. New findings, all operator/docs: the README
runbook's `sqlite3` VACUUM step cannot run on the NAS (no `sqlite3` on host or in the container —
verified) and its `cp` backup is lossy under WAL (use `VACUUM INTO` via `docker exec … python`);
Wave 5's `tests/e2e/test_products_ui.py` was never built; three undocumented minor deviations.
Reports: `/tmp/claude-1000/-home-ff235-dev-book-alerter/6e5ea529-3b4e-438a-a9db-8081f283488c/scratchpad/R-{backend,frontend,adherence}/findings.md`.
Open questions answered in chat (Q2: £35 / £10-of-books, secondary sources; Q4: recommend not wiring
Sentry; T6.3 Keepa: weekly refresh ≤ existing on-view load). No agents left running.
