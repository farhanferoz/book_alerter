# RESUME — book_alerter

<!-- ccage budget: keep lean. Update the State sections in place; keep at most
     ~3 ## Session blocks — roll older ones into CHANGELOG. RESUME is auto-read
     into context on every session start, so smaller = cheaper + sharper. -->

## State

### Now
- **All 40 plan tasks resolved** (39 ticked + T1.2 dropped by its own gate, D21) **and all four
  reviews' findings resolved**, bar a frontend tail in flight (see `### Next`).
  - Tier 4 Wave 3: F-A, F-B, F-C, F-D, F-E — **5/5**.
  - Backend review: F-B1…F-B10 — **10/10**. Cross-wave items → D41 (deferred, pre-existing)
    and D44 (refuted by measurement).
  - Shipping-chain review: S1…S8 — **8/8**.
  - Frontend review: F1–F6, F11 fixed, F13 partial; **F7/F8/F9/F10/F12/F13-rest/F14 in flight.**
- Branch `wave-execution` at `e12b88f`. **`master` is an ancestor of HEAD → the merge is a clean
  fast-forward**, 140 commits, no conflicts possible.
- **The merge to `master` is the only remaining release action.** It triggers the GHCR build
  (verified: `.github/workflows/build.yml` fires on push to `master` for `src/**`, `web/**`,
  `Dockerfile`, `pyproject.toml`, `alembic.ini`, `docker-entrypoint.sh`; `workflow_dispatch`
  is the manual fallback).

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
1. **Wait for `W-T23-prime` to commit** the frontend tail (F7/D40 dead-hook removal + `useItems`
   repoint, F8 `ScrapeHealthBanner` + `ProductsDashboard`, F9, F10, F12, F13 remainder, F14).
2. **Full-suite gate on a clean tree.** The `test_scope_guard` hook blocks whole-suite runs while
   the working tree is dirty; it allows everything once clean, so this resolves itself after (1).
   Its documented release escapes (`CCLAUDE_TESTSCOPE_RANGE`, `.claude-testscope-range`) did **not**
   take effect from an isolated worktree — the hook resolves the repo root from the main tree.
   Do **not** use `CCLAUDE_TESTSCOPE_MODE=off`: the hook states that hatch is human-only.
3. **Frontend gate** (`tsc -b`, `eslint`, `npm run build`) — needs prime's commit first.
4. **Merge to `master`** (fast-forward), push, confirm the GHCR build starts.
5. `/checkpoint --final`.

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
- `W-T23-prime` — frontend tail, uncommitted at last check.
- Idle and available: `W-T31-stats`, `W-T01-capture`, `W-fix-browser`, `W-fix-scheduler`,
  `W-fix-amazon`.
