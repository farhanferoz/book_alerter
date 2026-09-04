# UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Author: Farhan Feroz. Status: ratified 2026-09-04, execution started (Task 1 code is on disk, uncommitted).

**Goal:** Make the shipped dashboard, alerts rail and detail charts read correctly at laptop width and in dark mode, without redesigning anything — five contained changes plus the two operator-runbook defects found by the 2026-09-04 final review.

**Architecture:** Plan shape is **skeleton-in-repo + thin plan**: Task 1's code already exists on branch `ui-polish` in the working tree (uncommitted, `tsc -b` and `eslint` clean); the remaining tasks carry complete code here. All changes are frontend (`web/src/…`) plus one README edit; no backend, API or schema change anywhere. Verification is the frontend gate plus real screenshots against a production-database copy, compared with the pre-change screenshots that motivated each item.

**Tech Stack:** React 19 / TypeScript / Tailwind v4 / shadcn/ui / Recharts 3.8 / TanStack Query 5 (frontend); FastAPI + SQLite backend used only as a fixture server for screenshots; Playwright (Python, already installed: `~/.cache/ms-playwright/chromium-1234`).

## Global Constraints

- Work only inside `/home/ff235/dev/book_alerter/`, on branch **`ui-polish`** (exists; based on `master` @ `755b822`). Never touch `master` directly.
- **Frontend gate, run from `web/`:** `./node_modules/.bin/tsc -b --noEmit` · `./node_modules/.bin/eslint .` · `npm run build`. **The `-b` is mandatory** — without it `tsc` checks nothing and exits 0 with real type errors present (measured 2026-09-04). Deps install with `npm ci --legacy-peer-deps` if `node_modules` is missing.
- **No frontend unit-test runner exists** (`web/package.json` has no vitest/jest). Do not add one for this plan (YAGNI). Verification = the gate above + Task 4's screenshots.
- **Commit with an explicit pathspec, never bare** — `git commit -- <paths>` (D25/D28). Check `git show --stat HEAD` after each commit.
- No AI/tool provenance in commit messages, comments or docs. Author is the user.
- Temporary files (screenshots, harness DB copies, scripts) go to the session scratchpad, never the repo. `git status --short` must show only intended files before every commit.
- Money display stays `formatMoneyMinor`; signals, prime/estimate flags are read from the backend only — never re-derived client-side (D10, D34). Nothing in this plan changes a number, only where and how it is shown.
- Dark mode is the `<html class="dark">` class (`web/src/hooks/useIsDark.ts`); Tailwind's `dark:` variant follows it. Chart surface colour token is `--card` (`web/src/index.css:54` light, `:89` dark).
- Deliberately **out of scope**: the "Save…" button label (the ellipsis correctly signals "opens a dialog" — `DiffPreviewDialog`); the 602 kB main chunk (pre-existing, Recharts and the detail pages are already lazy); any backend `summary` field for alerts (the client-side prefix strip in Task 1 is sufficient and reversible).

---

## Evidence (why each item exists)

Screenshots taken 2026-09-04 against a production copy, in
`/tmp/claude-1000/-home-ff235-dev-book-alerter/9afc7be5-3c95-4c58-9594-26dfbdb205da/scratchpad/manualcheck/shots/`:

| Item | Evidence |
|---|---|
| Signal column off-screen | `f5_dashboard.png` (1400 px): columns visible = Title, Best price, Shipping. `f8_books_dashboard.png` (1100 px): Title only. `web/src/components/ui/table.tsx:9` is `overflow-x-auto`, so the Signal/Percentile/Days/Last seen columns exist but need a horizontal scroll. Cause: the title cell had only `min-w-[12rem]` and grew with long titles; the 320 px alerts rail (`AppShell.tsx:158`, `w-80`) is always open. Products (`f8_products_dashboard.png`) show Signal at 1100 px only because their titles are short. |
| Alert text repeats the badge and leaks the enum | Every rail card: badge `PERCENTILE` + text `[PERCENTILE_CROSS] Fatherland — total 3.50 GBP …`. The text is the ntfy push string (`src/book_alerter/notifications/dispatcher.py:381`, `f"[{kind.upper()}] {item.title} — …"`), rendered verbatim by `AlertItem.tsx`. |
| Rail dominated by one item | `f5_dashboard.png`: 20 cards, six of them "Neptune's Fortune". |
| Red dot = hover-only meaning | `columns.tsx:132-139` — an 8 px `bg-red-500` dot with a `title` tooltip; 8 of 13 titles carry it. |
| Chart: a marker on every point; diagonal moves | `book3.png`, Price history 90 days: `dot={{ r: 2 }}` on every breakpoint makes a flat line a bead string; `type="monotone"` draws a diagonal between two scrapes at different prices, a transition that never happened (prices are step functions). Dashed grid is a listed anti-pattern in the `dataviz` skill. |
| Keepa PNG glares in dark mode | `KeepaChart.tsx:25-31` renders the white PNG bare; dark surface is `oklch(0.205 0 0)`. |
| Runbook I1/I2 | `README.md:100-102` backs up with `cp` of the main file only while the DB is in WAL mode (`db/session.py:55`); on the NAS the main file was 6 h older than the 4 MB `-wal`. `README.md:122` runs `sqlite3 … 'VACUUM'`; verified 2026-09-04 by ssh that **no `sqlite3` binary exists on the NAS host or in the container** (only Python's module, SQLite 3.45.1). |

---

### Task 1: Dashboard, alert rail and Keepa mat (code already on disk)

**Files:**
- Modify: `web/src/components/books/columns.tsx` (header comment; title cell; scrape-failed chip; `signal` column moved before `best_price`)
- Modify: `web/src/components/alerts/AlertItem.tsx` (new `alertBody()`; message `<p>`)
- Modify: `web/src/components/alerts/AlertsSidebar.tsx` (group by item; footer)
- Modify: `web/src/components/books/detail/KeepaChart.tsx` (white mat wrapper)

**Interfaces:**
- Consumes: `Item` (`@/lib/item`), `Alert` (`@/hooks/useAlerts`), `AlertItem` props unchanged.
- Produces: nothing new for later tasks. `AlertItem` still accepts `{alert, onDismiss, dismissing, compact}`.

**Orchestration:** parallel: no · deps: none · tier: haiku · scale: static · shape: execution · verify: `cd web && ./node_modules/.bin/tsc -b --noEmit && ./node_modules/.bin/eslint . && npm run build`

The four edits are complete on disk on branch `ui-polish`. This task reviews and commits them; it does not rewrite them. What each edit does, so the reviewer can check intent against diff:

1. `columns.tsx` — `signal` column definition moved to immediately after `title`; title wrapper `min-w-[12rem] max-w-[24rem]`; title `<Link>` gains `line-clamp-2` and `title={item.title}`; the red-dot `<span role="img">` becomes a text chip `Scrape failed` using the same classes as the products `Failed` chip (`bg-destructive/10 … text-destructive`), keeping the `title` tooltip with the error text; header comment updated to the new column order with the reason.
2. `AlertItem.tsx` — `alertBody(alert)` strips exactly `` `[${alert.kind.toUpperCase()}] ${alert.title} — ` `` from the front of `alert.message` when present, capitalises the first letter, and is what the `<p>` renders; the full raw message is kept in the `<p>`'s `title` in compact mode.
3. `AlertsSidebar.tsx` — `groups` (a `useMemo` keyed on `alertsQuery.data`) reduces the 20-item page to one entry per `${item_kind}-${item_id}` holding `{newest, older}`; renders one `AlertItem` per group plus a `+N older alert(s) for this item` link to `/alerts` when `older > 0`; footer reads `20+ active across 9 items`.
4. `KeepaChart.tsx` — `<img>` wrapped in `<div className="rounded bg-white dark:p-1">` with a comment saying why.

- [ ] **Step 1: Confirm the branch and the diff are what this task describes**

Run: `cd /home/ff235/dev/book_alerter && git branch --show-current && git status --short && git diff --stat`
Expected: `ui-polish`; exactly the four files above modified (plus `RESUME.md`, which is NOT part of this task); `git diff` content matches items 1–4.

- [ ] **Step 2: Run the frontend gate**

Run: `cd web && ./node_modules/.bin/tsc -b --noEmit; echo tsc=$?; ./node_modules/.bin/eslint .; echo eslint=$?; npm run build 2>&1 | tail -3`
Expected: `tsc=0`, `eslint=0` with **0 warnings** (an `exhaustive-deps` warning on `AlertsSidebar.tsx` was already fixed by keying the memo on `page`), build `✓ built`.

- [ ] **Step 3: Commit with pathspec**

```bash
git commit -- web/src/components/books/columns.tsx \
  web/src/components/alerts/AlertItem.tsx \
  web/src/components/alerts/AlertsSidebar.tsx \
  web/src/components/books/detail/KeepaChart.tsx \
  -m "feat(web): signal-first dashboard row, grouped alert rail, Keepa dark-mode mat

- Signal column moves to directly after the title and the title cell is
  width-capped: with the alerts rail open, a 1280-1400 px viewport showed
  Title / Best price / Shipping and scrolled the Signal column off-screen.
- The scrape-error red dot becomes a 'Scrape failed' text chip (same style
  as the products 'Failed' chip); the error stays in the tooltip.
- Alert cards no longer repeat the kind badge and title from the push text
  ('[PERCENTILE_CROSS] Title - ...'); the rail shows one card per item with
  a '+N older' link, so one drifting book cannot fill it.
- The Keepa PNG sits on a white mat so it does not glare in dark mode."
git show --stat HEAD
```
Expected: 4 files changed, no others.

---

### Task 2: Price-history chart — markers at price changes, step line, hairline grid

**Files:**
- Modify: `web/src/components/books/detail/HistoryChart.tsx:244-292` (grid, `<Line>` props) and add two helpers above `HistoryChart`

**Interfaces:**
- Consumes: `ChartRow` (`{ ts: number } & Record<string, number | null>`), `rows`/`series` from `buildSeries`, `SERIES_COLORS` — all already in the file.
- Produces: `changeIndices(rows: ChartRow[], key: string): Set<number>` and `renderChangeDot(changes: Set<number>, color: string)` — file-private, no consumers elsewhere.

**Orchestration:** parallel: yes · deps: none · tier: sonnet · scale: static · shape: execution · verify: `cd web && ./node_modules/.bin/tsc -b --noEmit && ./node_modules/.bin/eslint src/components/books/detail/HistoryChart.tsx && npm run build`

Rules applied (from the `dataviz` skill, loaded 2026-09-04): markers ≥ 8 px (r ≥ 4) with a 2 px ring in the surface colour, placed selectively — not on every point; gridlines solid hairline, recessive; the form matches the data — a "cheapest live price" envelope is a step function, so the line is `stepAfter`.

- [ ] **Step 1: Add the two helpers** — insert directly above `function TooltipContent(` (currently line ~150):

```tsx
// A marker only where the envelope actually moves. With one dot per
// breakpoint the 90-day view drew ~60 identical markers per source and a
// flat price read as a bead string; the change points ARE the information.
function changeIndices(rows: ChartRow[], key: string): Set<number> {
  const out = new Set<number>();
  let last: number | null = null;
  rows.forEach((row, i) => {
    const v = row[key];
    if (v == null) return;
    if (last === null || v !== last) out.add(i);
    last = v;
  });
  return out;
}

type DotRenderProps = {
  cx?: number;
  cy?: number;
  index?: number;
  key?: string | number;
};

// Recharts 3 calls a function `dot` with `{cx, cy, index, key, …}` for every
// point and expects an element back; an empty <g> is the "no marker" answer.
// r=4 (8 px) with a 2 px ring in the card colour so overlapping series stay
// separable (dataviz mark spec).
function renderChangeDot(changes: Set<number>, color: string) {
  return function ChangeDot({ cx, cy, index, key }: DotRenderProps) {
    if (index === undefined || !changes.has(index) || cx === undefined || cy === undefined) {
      return <g key={key} />;
    }
    return (
      <circle
        key={key}
        cx={cx}
        cy={cy}
        r={4}
        fill={color}
        stroke="var(--card)"
        strokeWidth={2}
      />
    );
  };
}
```

- [ ] **Step 2: Compute the change sets once per data change** — inside `HistoryChart`, directly after the existing `useMemo` that builds `{ rows, series }`:

```tsx
  const changeSets = useMemo(
    () => new Map(series.map((key) => [key, changeIndices(rows, key)])),
    [rows, series],
  );
```

- [ ] **Step 3: Replace the grid and the `<Line>`** — in the JSX:

Replace
```tsx
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
```
with
```tsx
              {/* Solid hairline, horizontal only: recessive, and a dashed
                  grid is the noisiest thing on a chart (dataviz anti-pattern). */}
              <CartesianGrid stroke="currentColor" opacity={0.08} vertical={false} />
```

Replace the `<Line …>` block
```tsx
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  activeDot={{ r: 4 }}
                  connectNulls
                  hide={hidden.has(key)}
                  isAnimationActive={false}
                />
```
with
```tsx
                <Line
                  key={key}
                  // A price is a step function: it holds until the next
                  // observation changes it. `monotone` drew a diagonal
                  // between two scrapes, a transition that never happened.
                  type="stepAfter"
                  dataKey={key}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  strokeWidth={2}
                  dot={renderChangeDot(
                    changeSets.get(key) ?? new Set<number>(),
                    SERIES_COLORS[i % SERIES_COLORS.length],
                  )}
                  activeDot={{ r: 5 }}
                  connectNulls
                  hide={hidden.has(key)}
                  isAnimationActive={false}
                />
```

- [ ] **Step 4: Gate**

Run: `cd web && ./node_modules/.bin/tsc -b --noEmit; echo tsc=$?; ./node_modules/.bin/eslint .; echo eslint=$?; npm run build 2>&1 | tail -3`
Expected: `tsc=0`, `eslint=0` (0 warnings), build ok. If `tsc` rejects the `dot` prop type, the fix is to widen `DotRenderProps` with the reported missing optional field — do not cast to `any`.

- [ ] **Step 5: Commit**

```bash
git commit -- web/src/components/books/detail/HistoryChart.tsx \
  -m "feat(web): price-history markers only at price changes; step line; hairline grid

A dot on every breakpoint turned a flat 90-day line into a bead string,
and the monotone curve drew diagonal moves between scrapes that never
happened. Markers now sit only where the cheapest live price changes
(r=4, ringed in the card colour), the line is a step function, and the
grid is a solid horizontal hairline."
git show --stat HEAD
```

---

### Task 3: README runbook — consistent backup and a VACUUM that can run on the NAS

**Files:**
- Modify: `README.md:100-102` (step 1) and `README.md:120-122` (step 4), plus one sentence after "Step 4 is not optional…" (line 125)

**Interfaces:** none (docs).

**Orchestration:** parallel: yes · deps: none · tier: haiku · scale: static · shape: execution · verify: `grep -n "VACUUM INTO\|python -c" README.md`

Facts this rests on (verified 2026-09-04): the app's own weekly backup is `VACUUM INTO` via Python's `sqlite3` (`src/book_alerter/scheduler.py:110-140`); the NAS host and the container have no `sqlite3` binary; the container is named `book_alerter` and mounts the data dir at `/app/data` (`~/dev/workspace-sync/nas/compose/book_alerter/docker-compose.yml:22`); migration 0019→0024 takes 1.5 s and `VACUUM` 0.12 s on the production copy, shrinking 51 MB → 5.7 MB.

- [ ] **Step 1: Replace step 1**

Replace
```bash
# 1. Back up first if the release carries a migration.
ssh nasff235 "cd /share/CACHEDEV1_DATA/Container/book_alerter/data && \
  cp book_alerter.db book_alerter.db.pre-$(date +%Y%m%d)"
```
with
```bash
# 1. Back up first if the release carries a migration. The database runs in
#    WAL mode, so a plain `cp` of book_alerter.db misses every write still in
#    book_alerter.db-wal (hours of scrapes, measured). `VACUUM INTO` writes a
#    consistent snapshot — the same call the app's weekly backup job makes —
#    and needs only the container's Python: there is no sqlite3 binary on the
#    NAS host or in the image.
ssh nasff235 "$NASDOCKER exec book_alerter python -c \"import sqlite3; \
  c = sqlite3.connect('/app/data/book_alerter.db', isolation_level=None); \
  c.execute(\\\"VACUUM INTO '/app/data/backups/pre-deploy-$(date +%Y%m%d).db'\\\"); c.close()\""
```

- [ ] **Step 2: Replace step 4**

Replace
```bash
# 4. After a row-deleting migration, reclaim the freed space.
ssh nasff235 "cd /share/CACHEDEV1_DATA/Container/book_alerter/data && \
  sqlite3 book_alerter.db 'VACUUM'"
```
with
```bash
# 4. After a row-deleting migration, reclaim the freed space. Same route as
#    the backup: through the container's Python, because no sqlite3 binary
#    exists on the host. `timeout=60` waits out a scrape holding the write lock
#    rather than failing with "database is locked".
ssh nasff235 "$NASDOCKER exec book_alerter python -c \"import sqlite3; \
  c = sqlite3.connect('/app/data/book_alerter.db', isolation_level=None, timeout=60); \
  c.execute('VACUUM'); c.close()\""
```

- [ ] **Step 3: Add the measured numbers** — after the sentence ending "…during a quiet period — `VACUUM` rewrites the whole file and takes an exclusive lock for the duration." append:

```markdown
Measured on a copy of production (2026-09-04): the 0019→0024 migration chain
runs in 1.5 s and the `VACUUM` in 0.12 s, taking the file from 51 MB to 5.7 MB.
```

- [ ] **Step 4: Check the shell quoting actually parses** — the nested quotes are the risk in this task:

Run: `bash -n <(sed -n '/^```bash/,/^```/p' README.md | sed '/^```/d')`
Expected: no output (exit 0). Then eyeball: `sed -n 96,135p README.md`.

- [ ] **Step 5: Commit**

```bash
git commit -- README.md -m "docs: deploy runbook backs up and VACUUMs through the container's Python

The NAS host and the image have no sqlite3 binary, so step 4 failed as
written; and a cp of the main file under WAL mode misses the un-checkpointed
writes in the -wal file. Both steps now use VACUUM INTO / VACUUM via
docker exec python, which is what the app's own backup job does."
git show --stat HEAD
```

---

### Task 4: Visual verification against a production copy

**Files:**
- Create (scratchpad, never the repo): `<SCRATCH>/ui_shots.py`, `<SCRATCH>/harness/` (DB copy, config.yaml, covers dir, screenshots)

Where `<SCRATCH>` is the current session's scratchpad directory (given in the system prompt). Production copy to start from (pre-migration, revision 0019 — migrate a COPY, never this file):
`/tmp/claude-1000/-home-ff235-dev-book-alerter/9afc7be5-3c95-4c58-9594-26dfbdb205da/scratchpad/proddb/book_alerter.db` (+ `-wal`, `-shm`).

**Interfaces:**
- Consumes: the built SPA (`web/dist`, served by the backend when `BOOK_ALERTER_WEB_DIST` points at it — `src/book_alerter/app.py:213`), the harness config writer `scripts/smoke_check.py::_write_harness_config(config_cls, cfg_path)` (disables every source, the backup and the janitor — reuse it rather than hand-writing a config).
- Produces: PNGs under `<SCRATCH>/harness/shots/` and a pass/fail against the acceptance list below.

**Orchestration:** parallel: no · deps: Task 1, Task 2 · tier: sonnet · scale: static · shape: discovery · verify: `ls <SCRATCH>/harness/shots/*.png | wc -l` (expect 6) plus the acceptance checklist

- [ ] **Step 1: Prepare the harness DB and config**

```bash
SCR=<SCRATCH>; mkdir -p $SCR/harness/shots $SCR/harness/covers
cp /tmp/claude-1000/-home-ff235-dev-book-alerter/9afc7be5-3c95-4c58-9594-26dfbdb205da/scratchpad/proddb/book_alerter.db* $SCR/harness/
cd /home/ff235/dev/book_alerter
BOOK_ALERTER_DATABASE_URL="sqlite:///$SCR/harness/book_alerter.db" uv run alembic upgrade head 2>&1 | tail -1
uv run python -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
import smoke_check
from book_alerter.config import Config
smoke_check._write_harness_config(Config, Path('$SCR/harness/config.yaml'))
print('config written')"
```
Expected: last alembic line names `0024_live_offers_deterministic_tiebreak`; `config written`.

- [ ] **Step 2: Build the SPA and start the backend against the harness**

```bash
cd /home/ff235/dev/book_alerter/web && npm run build 2>&1 | tail -1 && cd ..
BOOK_ALERTER_DATABASE_URL="sqlite:///$SCR/harness/book_alerter.db" \
BOOK_ALERTER_CONFIG_PATH="$SCR/harness/config.yaml" \
BOOK_ALERTER_COVER_DIR="$SCR/harness/covers" \
BOOK_ALERTER_WEB_DIST="/home/ff235/dev/book_alerter/web/dist" \
uv run uvicorn book_alerter.app:create_app --factory --host 127.0.0.1 --port 8123 \
  > $SCR/harness/uvicorn.log 2>&1 &
sleep 3; curl -sf http://127.0.0.1:8123/api/health | head -c 200
```
Expected: JSON with `"status":"ok"` (or equivalent healthy body). Run the server in the background (the harness forbids long foreground jobs); note its PID to stop it in Step 5.

- [ ] **Step 3: Write and run the screenshot script**

`<SCRATCH>/ui_shots.py`:
```python
"""Screenshots of the polished UI against a production copy.

Widths: 1280 (laptop, rail open) and 1600. Dark mode = <html class="dark">.
Book 3 = 'From Kargil to the Coup' (the detail page the baseline shots used).
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"
OUT = Path(sys.argv[1])
SHOTS = [
    ("dashboard_1280", "/", 1280, False),
    ("dashboard_1600", "/", 1600, False),
    ("dashboard_1280_dark", "/", 1280, True),
    ("book3_1280", "/books/3", 1280, False),
    ("book3_1280_dark", "/books/3", 1280, True),
    ("alerts_1280", "/alerts", 1280, False),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for name, path, width, dark in SHOTS:
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        if dark:
            ctx.add_init_script("document.documentElement.classList.add('dark')")
        page = ctx.new_page()
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_selector("table, article, h1", timeout=15000)
        page.wait_for_timeout(800)  # charts finish laying out
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        ctx.close()
    browser.close()
print("done")
```
Run: `cd /home/ff235/dev/book_alerter && uv run python $SCR/ui_shots.py $SCR/harness/shots`
Expected: `done`; six PNGs.

- [ ] **Step 4: Check each acceptance item by looking at the PNGs** (Read tool on each file), against the baselines in `…/9afc7be5-…/scratchpad/manualcheck/shots/`:

| # | Screenshot | Must be true |
|---|---|---|
| A1 | `dashboard_1280.png` | With the rail open, the **Signal** pill column is visible without horizontal scroll (baseline `f5_dashboard.png` at 1400 px did not show it). Long titles wrap to at most two lines. |
| A2 | `dashboard_1280.png` | Rows with a scrape error show a small `SCRAPE FAILED` chip after the title; no bare red dot anywhere. |
| A3 | `dashboard_1280.png` | Rail: no card text starts with `[`; no two consecutive cards share a title; at least one `+N older alerts for this item` line; footer reads `N+ active across M items`. |
| A4 | `book3_1280.png` | Price history (90 days): markers appear only at price changes (baseline `book3.png` had one on every point); the line is stepped, not diagonal; grid lines are solid and horizontal only. |
| A5 | `book3_1280_dark.png` | The Keepa image sits on a white rounded mat with a 4 px inset; the rest of the page is dark. |
| A6 | `alerts_1280.png` | The full Alerts page still renders every alert (grouping is rail-only) and the message text has no `[KIND]` prefix. |

Any failure → fix in the owning task's file, re-run Steps 2–3 (rebuild is required: the backend serves `web/dist`), re-check. Record the outcome in the plan tick for this task.

- [ ] **Step 5: Stop the server; leave nothing in the repo**

```bash
kill %1 2>/dev/null || pkill -f "uvicorn book_alerter.app:create_app --factory --host 127.0.0.1 --port 8123"
cd /home/ff235/dev/book_alerter && git status --short
```
Expected: only `RESUME.md` (and this plan file until it is committed) — `web/dist` is gitignored; the harness lives in the scratchpad.

---

### Task 5: Review and handoff

**Files:** none new. Modify: this plan's checkboxes; `RESUME.md` `### Now` / `### Next`.

**Orchestration:** parallel: no · deps: Task 1–4 · tier: sonnet · scale: static · shape: execution · verify: `git log --oneline master..ui-polish | wc -l` (expect 3)

- [ ] **Step 1: Tier 1 review** — three commits on a safe surface → `simplify` only (per the main-session doctrine's tier table). Run the `simplify` skill over `git diff master..ui-polish`; apply anything it finds with a further pathspec commit.
- [ ] **Step 2: Full frontend gate once more from a clean worktree** — `git worktree add --detach <SCRATCH>/wt ui-polish && cd <SCRATCH>/wt/web && ln -s /home/ff235/dev/book_alerter/web/node_modules node_modules && ./node_modules/.bin/tsc -b --noEmit && ./node_modules/.bin/eslint . && npm run build; cd /home/ff235/dev/book_alerter && git worktree remove --force <SCRATCH>/wt`. Expected: all clean.
- [ ] **Step 3: Tick this plan's tasks** with the evidence (commit SHAs, screenshot names) and commit the plan: `git commit -- docs/superpowers/plans/2026-09-04-ui-polish-plan.md -m "docs: UI polish plan with execution evidence"`.
- [ ] **Step 4: Update `RESUME.md`** `### Now` with: branch `ui-polish` (N commits), the screenshot directory, gate results. Merge and deploy are Tasks 6–7.

> **Scope change, 2026-09-04 (user instruction, mid-execution):** this task originally ended at
> "handoff, not merge — do not push". The user then handed over for autonomous execution with an
> explicit end state: *"final result has to be deployment… we have a container running, you should
> replace it, and the final application should be running there."* Merging to `master`, pushing
> (which triggers the GHCR build), and deploying to the NAS are therefore **authorised** and are
> Tasks 6 and 7 below.

---

### Task 6: Merge to master and publish the image

**Files:** none in the repo (git operations only).

**Interfaces:**
- Consumes: branch `ui-polish` with Tasks 1–3 committed and Task 4's verification passed.
- Produces: `master` at the merge commit; a GHCR image tagged `latest` + `sha-<merge>`.

**Orchestration:** parallel: no · deps: Task 5 · tier: sonnet · scale: static · shape: execution · verify: `gh run list --repo farhanferoz/book_alerter --limit 1` shows `success`

Facts: pushing to `master` is what triggers `.github/workflows/build.yml`; pushing a branch does not. `gh`'s active account is `reviewsenseai`, which has **no write access** — scope the credential to the one command (`GH_TOKEN=$(gh auth token --user farhanferoz) git push …`) rather than `gh auth switch`, which changes the global account for every repo. Reading runs needs no scoping. The workflow has a paths filter: frontend and backend sources are in it, README alone is not — this release touches `web/src`, so it will build.

- [ ] **Step 1: Merge**

```bash
cd /home/ff235/dev/book_alerter
git checkout master && git merge --no-ff ui-polish -m "merge: UI polish — signal-first dashboard, grouped alert rail, chart and runbook fixes"
git log --oneline -1
```

- [ ] **Step 2: Push**

```bash
GH_TOKEN=$(gh auth token --user farhanferoz) git push origin master
```
Expected: `master -> master`. A `Permission … denied to reviewsenseai` error means the credential scoping was dropped.

- [ ] **Step 3: Watch the build to completion**

```bash
gh run watch --repo farhanferoz/book_alerter $(gh run list --repo farhanferoz/book_alerter --limit 1 --json databaseId -q '.[0].databaseId') --exit-status
gh run list --repo farhanferoz/book_alerter --limit 1
```
Expected: `success` (the previous release built in 3m47s). On failure, read the log, fix on a branch, and repeat — do not deploy a failed build.

- [ ] **Step 4: Record the published digest**

```bash
gh api /users/farhanferoz/packages/container/book_alerter/versions --jq '.[0] | {tags: .metadata.container.tags, digest: .name}' 2>/dev/null \
  || echo "digest lookup unavailable — take it from the run log's push step"
```

---

### Task 7: Deploy to the NAS and verify the running application

**Files:** none in the repo. Touches the **live NAS**: `/share/CACHEDEV1_DATA/Container/book_alerter/`.

**Interfaces:**
- Consumes: the GHCR image from Task 6.
- Produces: the running container on the new image, the database migrated 0019 → 0024 and vacuumed.

**Orchestration:** parallel: no · deps: Task 6 · tier: sonnet · scale: static · shape: discovery · verify: `curl -sf http://100.115.46.9:8090/api/health` returns 200 **and** the dashboard screenshot from the live host shows the new layout

**This is the one irreversible task in the plan.** The running container is on the July image
(`revision 6d8139a`) and the production database is at alembic revision **0019**; the entrypoint runs
`alembic upgrade head` on boot, so starting the new image migrates 0019 → 0024 in one step, including
`0021_heartbeat_compaction`, which deletes ~78k rows. Measured on a copy: migration 1.5 s, `VACUUM`
0.12 s, file 51 MB → 5.7 MB. **The backup in Step 1 is not optional and must be verified to exist and
be non-trivial in size before Step 2 runs.**

- [ ] **Step 1: Consistent backup (WAL-safe, via the container's Python)**

```bash
NASDOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
ssh nasff235 "$NASDOCKER exec book_alerter python -c \"import sqlite3; \
  c = sqlite3.connect('/app/data/book_alerter.db', isolation_level=None); \
  c.execute(\\\"VACUUM INTO '/app/data/backups/pre-ui-polish-$(date +%Y%m%d).db'\\\"); c.close()\""
ssh nasff235 "ls -la /share/CACHEDEV1_DATA/Container/book_alerter/data/backups/pre-ui-polish-*.db"
```
Expected: a file of roughly 45–50 MB. **If it is missing or under 1 MB, STOP** — do not deploy.

- [ ] **Step 2: Pull and restart**

```bash
ssh nasff235 "cd /share/CACHEDEV1_DATA/Container/book_alerter && \
  $NASDOCKER compose pull && $NASDOCKER compose up -d"
```

- [ ] **Step 3: Wait for healthy, and read the migration lines from the boot log**

```bash
sleep 45
ssh nasff235 "$NASDOCKER ps --filter name=book_alerter --format '{{.Image}} {{.Status}}'"
ssh nasff235 "$NASDOCKER logs --tail 80 book_alerter" | grep -i "alembic\|upgrade\|error" | tail -20
```
Expected: `Up … (healthy)`; the log shows the chain running through `0024_live_offers_deterministic_tiebreak`.
(`docker logs --since` can fail with `invalid character '\x00'` after a NAS reboot — use `--tail`.)

- [ ] **Step 4: The mandatory `VACUUM`**

```bash
ssh nasff235 "$NASDOCKER exec book_alerter python -c \"import sqlite3; \
  c = sqlite3.connect('/app/data/book_alerter.db', isolation_level=None, timeout=60); \
  c.execute('VACUUM'); c.close()\""
ssh nasff235 "ls -la /share/CACHEDEV1_DATA/Container/book_alerter/data/book_alerter.db"
```
Expected: the file drops from ~48 MB to roughly 6 MB. Without this, SQLite keeps the freed pages.

- [ ] **Step 5: Verify the live application, including in a browser**

```bash
curl -sf http://100.115.46.9:8090/api/health
curl -sf http://100.115.46.9:8090/api/books | head -c 300
```
Then screenshot the live instance with the Task 4 script pointed at `http://100.115.46.9:8090`
(`BASE` is a module constant — copy the script and change it, or parameterise it), capturing
`live_dashboard_1280.png` and `live_book3_1280.png`. Confirm against Task 4's acceptance items A1–A5
that the deployed UI is the new one, not a cached old bundle (hard-reload semantics: the SPA is
served fresh from the new image, so the asset hashes differ).

- [ ] **Step 6: Record the post-deploy state and D39's obligation**

```bash
ssh nasff235 "du -sh /share/CACHEDEV1_DATA/Container/book_alerter/data/*"
ssh nasff235 "$NASDOCKER exec book_alerter python -c \"import sqlite3; \
  c=sqlite3.connect('/app/data/book_alerter.db'); \
  print(c.execute('select count(*) from priceobservation').fetchone())\""
```
Append to `RESUME.md`: the deployed digest, the row count, the vacuumed file size, and **D39's standing
obligation** — deploying does not correct stored prices; after the first full scrape cycle, re-measure
the shipping cascade (query in `README.md` → "After the first deploy carrying the shipping fixes"),
because the ~2,780 legacy zero-shipping rows can drag the estimate for newly-unknown rows toward £0.

---

## Self-review (done 2026-09-04 at authoring time)

- **Coverage:** every evidence row maps to a task (rows 1–4 → Task 1, row 5 → Task 2, row 6 → Task 1, row 7 → Task 3); Task 4 verifies each with a named screenshot.
- **Placeholders:** none — every code step carries the code; the only "if X then" is the `tsc` type-widening instruction in Task 2 Step 4, which names the exact remedy.
- **Type consistency:** `changeIndices`/`renderChangeDot`/`DotRenderProps` are defined once (Task 2 Step 1) and used once (Step 3); `alertBody`, `groups`, `page` match the code on disk for Task 1.
- **Write sets:** Task 1 (four `web/src` files) · Task 2 (`HistoryChart.tsx`) · Task 3 (`README.md`) are disjoint, so Tasks 2 and 3 may run concurrently with Task 1's commit; Task 4 depends on 1 and 2.
