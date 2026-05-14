"""Config GET / schema / PUT endpoints (Phase 7 Task 7.5, plan line 2538-2542).

Three endpoints under `/api/config`:

- `GET /api/config` — current `Config` serialized via `model_dump(mode="json")`.
- `GET /api/config/schema` — `Config.model_json_schema()` for the future Monaco
  editor (consumer: Phase 11.5).
- `PUT /api/config` — body `{config: <Config-dict>, dry_run: bool = false}`.
  Validates via `Config.model_validate(...)` and always returns
  `{diff, applied, errors}`.

  - `dry_run=true`: validate + compute diff, **no** disk write, **no** mutation
    of `app.state.config`, `applied=false`.
  - `dry_run=false`: validate, rotate backup (single `.bak`, `shutil.copy2`
    preserves mtime; skip if no existing file), atomic-write the YAML via
    `Config.save`, swap `app.state.config`, `applied=true`.
  - Validation failure (either mode): **422** with Pydantic error strings in
    `errors`.

Diff shape (top-level keys only — MVP; the Monaco editor renders the block
diff itself):

    {added: {key: new_value}, removed: {key: old_value},
     changed: {key: {before: ..., after: ...}}}

Comparison is on `model_dump(mode="json")` so nested Pydantic models are
compared as plain JSON-shaped dicts (no identity / Pydantic-equality
surprises).
"""

from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from book_alerter.api.deps import ConfigDep, ConfigPathDep
from book_alerter.config import Config

router = APIRouter(prefix="/api/config", tags=["config"])


# --- DTOs -------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    """Request body for `PUT /api/config`.

    `config` is the raw config dict — `Config.model_validate` does the heavy
    lifting (typed validation, defaults, env substitution is NOT re-run here,
    since the body is already the materialized config).
    """
    config: dict[str, Any]
    dry_run: bool = False


class ConfigDiff(BaseModel):
    added: dict[str, Any] = Field(default_factory=dict)
    removed: dict[str, Any] = Field(default_factory=dict)
    changed: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ConfigUpdateResult(BaseModel):
    diff: ConfigDiff
    applied: bool
    errors: list[str] | None = None


# --- Helpers ----------------------------------------------------------------


def _diff_configs(old: Config, new: Config) -> ConfigDiff:
    """Top-level diff of two `Config` instances.

    Compares JSON-mode `model_dump` so nested Pydantic models become plain
    dicts/lists — deep equality is then trivial.
    """
    old_d = old.model_dump(mode="json")
    new_d = new.model_dump(mode="json")
    old_keys = set(old_d)
    new_keys = set(new_d)
    added = {k: new_d[k] for k in new_keys - old_keys}
    removed = {k: old_d[k] for k in old_keys - new_keys}
    changed = {
        k: {"before": old_d[k], "after": new_d[k]}
        for k in old_keys & new_keys
        if old_d[k] != new_d[k]
    }
    return ConfigDiff(added=added, removed=removed, changed=changed)


# --- Handlers ---------------------------------------------------------------


@router.get("")
def get_config(cfg: ConfigDep) -> dict[str, Any]:
    """Return the current config as JSON."""
    return cfg.model_dump(mode="json")


@router.get("/schema")
def get_config_schema() -> dict[str, Any]:
    """Return the JSON Schema for the `Config` model.

    Consumer: the future Monaco editor (Phase 11.5) uses this for live
    validation. Pydantic generates the schema; we don't massage it.
    """
    return Config.model_json_schema()


@router.put("")
def put_config(
    body: ConfigUpdate,
    request: Request,
    cfg: ConfigDep,
    config_path: ConfigPathDep,
) -> ConfigUpdateResult:
    """Validate + (optionally) persist a new config.

    Validation runs in both dry-run and apply modes; failures always return
    422. On apply, the existing config YAML is rotated to `<path>.bak` via
    `shutil.copy2` (preserves mtime) before the atomic write — single rotating
    backup, overwriting any prior `.bak`. No backup is created when the
    config file doesn't yet exist (first-write case).
    """
    try:
        new_cfg = Config.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": [str(e) for e in exc.errors()]},
        ) from exc

    diff = _diff_configs(cfg, new_cfg)

    if body.dry_run:
        return ConfigUpdateResult(diff=diff, applied=False, errors=None)

    # Rotate backup before write. Skip if the file doesn't yet exist (first
    # write — nothing to back up). Single rotating backup: `shutil.copy2`
    # overwrites any existing `.bak` and preserves mtime so the user can see
    # when the previous config was last touched.
    if config_path.exists():
        backup_path = config_path.with_suffix(config_path.suffix + ".bak")
        shutil.copy2(config_path, backup_path)

    new_cfg.save(config_path)
    request.app.state.config = new_cfg
    return ConfigUpdateResult(diff=diff, applied=True, errors=None)
