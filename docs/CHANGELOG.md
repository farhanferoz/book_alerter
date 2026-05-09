# Book Alerter — Implementation Changelog

Append-only log of completed implementation tasks. Newest entries on top within a date.

Each entry: plan task ID(s), one-line summary, commit SHA(s), notable deviations from plan (if any).

---

## 2026-05-09

### Pre-implementation (specification + planning)

- `2eafd01` — Spec drafted (846 lines)
- `22521e4` — Spec polished per reviewer's advisory recommendations
- `7ecba3d` — User-supplied test ISBN fixtures added to spec
- `84ca651` — Spec narrowed: ntfy-only push; Tailscale auth posture
- `6cc7189` — Implementation plan drafted (13 phases, ~80 tasks, ~2840 lines)
- `a5d030c` — Plan fixed per reviewer: BookSignalState, working backoff, dry_run config, tsconfig alias, view tie-break, BookStats.percentile_at
- `d953741` — RESUME + CHANGELOG scaffolds added

### Phase 0 — Foundation

- **Task 0.1** — `defc8c1` Initialize uv project + base dependencies. `uv sync` succeeds; `import book_alerter` works.
  - **Incident & fix**: `0b93bbc` Subagent encountered a Python-version conflict because `book_alerter` is a member of an existing uv workspace at `/home/ff235/dev/` whose other members are pinned to `>=3.12,<3.13` (likely for PyTorch XPU wheel compatibility). Subagent attempted to resolve by relaxing the siblings' constraints — out of scope. Reverted those changes; instead lowered `book_alerter` to `>=3.12,<3.13` (none of our deps need 3.13). Spec + plan updated. Future implementer prompts now hard-constrain edits to `/home/ff235/dev/book_alerter/`.
