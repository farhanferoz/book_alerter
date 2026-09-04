"""Sources status, trigger, and config-patch endpoints.

Implements endpoints under `/api/sources` (Phase 7 Task 7.4, plan line
2532-2536; runs-history extension added for Phase 11.2 UI):

- `GET /api/sources` — per-source status: name + `SourceConfig` (config) + last
  `SourceRun` (or `null`). Sorted alphabetically by source name.
- `GET /api/sources/{name}/runs?limit=10` — recent `SourceRun` rows for one
  source, newest first. `limit` defaults to 10 and is capped at 100. 404 if
  unknown source.
- `POST /api/sources/{name}/run` — manual one-shot via `scheduler.trigger_now`.
  Returns `{run_id: int}`. 404 if unknown source. 409 when the scheduler returns
  0 (backoff gate active).
- `PATCH /api/sources/{name}` — partial update of `SourceConfig` fields
  (`enabled`, `schedule`, `concurrency`, `jitter_seconds`,
  `per_book_delay_seconds`). Validates via Pydantic, writes the full config
  back to disk via `Config.save` (atomic tmp-replace), and replaces
  `app.state.config` with the new validated config. 404 unknown source, 422 on
  invalid values (e.g. concurrency out of `ge=1, le=5`).

Design notes:

- `SourceRun.error_traceback` is excluded from the wire — it's debugging-only
  and can be large. Surface it through structured logs instead.
- PATCH uses Pydantic's `model_copy(update=...)`: merge only fields the caller
  explicitly set (`exclude_unset=True`) and skip `None` values so missing patch
  fields don't clobber the existing config with defaults.
- `GET /api/sources`' last-run-per-source lookup is one windowed query
  (`ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC)`), not a
  `LIMIT 1` query repeated per source (T3.3, plan 2026-09-04). `PATCH
  /api/sources/{name}` and any other single-source caller still use the plain
  `LIMIT 1` lookup — a windowed query is pointless overhead for one source.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import select

from book_alerter.api._serializers import UtcDateTime
from book_alerter.api.deps import (
    ConfigDep,
    ConfigPathDep,
    SchedulerDep,
    SessionDep,
)
from book_alerter.config import Config, SourceConfig
from book_alerter.db import models

router = APIRouter(prefix="/api/sources", tags=["sources"])


# --- DTOs -------------------------------------------------------------------


class SourceConfigOut(BaseModel):
    """Wire mirror of `book_alerter.config.SourceConfig`."""
    enabled: bool
    region: str
    schedule: str
    jitter_seconds: int
    per_book_delay_seconds: tuple[int, int]
    concurrency: int
    timeout_seconds: int
    max_consecutive_errors: int

    @classmethod
    def from_config(cls, sc: SourceConfig) -> SourceConfigOut:
        return cls(
            enabled=sc.enabled,
            region=sc.region,
            schedule=sc.schedule,
            jitter_seconds=sc.jitter_seconds,
            per_book_delay_seconds=sc.per_book_delay_seconds,
            concurrency=sc.concurrency,
            timeout_seconds=sc.timeout_seconds,
            max_consecutive_errors=sc.max_consecutive_errors,
        )


class SourceRunOut(BaseModel):
    """Wire mirror of `book_alerter.db.models.SourceRun`.

    Excludes `error_traceback` — debugging-only, can be very large; surface
    via structured logs (`source.run.exception`) rather than the API.
    """
    id: int
    source: str
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
    status: Literal["running", "success", "error", "partial"]
    books_attempted: int
    books_succeeded: int
    error_message: str | None

    @classmethod
    def from_run(cls, run: models.SourceRun) -> SourceRunOut:
        return cls(
            id=run.id or 0,
            source=run.source,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            books_attempted=run.books_attempted,
            books_succeeded=run.books_succeeded,
            error_message=run.error_message,
        )


class SourceHealthOut(BaseModel):
    """Rolled-up scrape health over the last 24 hours.

    `challenged` is an EXACT count as of T1.3: the scheduler now records
    `SourceRun.items_challenged` per run (items whose final outcome was an
    unsolved bot challenge, after the one retry). It was previously a proxy
    -- every item failure, of which challenges were the dominant but not the
    only cause -- because the run row carried no challenge count at all.

    One honest limitation: runs that predate migration 0022 carry 0, since
    the counter did not exist then. So the figure under-reports for the first
    window after deploy rather than inventing history, which is the same
    reason the migration backfills 0 rather than guessing.
    """

    attempted: int = 0
    succeeded: int = 0
    challenged: int = 0


class SourceStatusOut(BaseModel):
    name: str
    config: SourceConfigOut
    last_run: SourceRunOut | None
    last_24h: SourceHealthOut = SourceHealthOut()


class TriggerRunResult(BaseModel):
    run_id: int


class SourcePatch(BaseModel):
    """Partial update to a `SourceConfig`. `None` means "don't change".

    `per_book_delay_seconds` accepts a 2-tuple `(min, max)` matching the YAML
    shape on `SourceConfig`. `jitter_seconds` is a non-negative scalar.
    """
    enabled: bool | None = None
    schedule: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=5)
    jitter_seconds: int | None = Field(default=None, ge=0)
    per_book_delay_seconds: tuple[int, int] | None = None


# --- Helpers ----------------------------------------------------------------


def _last_run_for(session, source_name: str) -> models.SourceRun | None:
    stmt = (
        select(models.SourceRun)
        .where(models.SourceRun.source == source_name)
        .order_by(models.SourceRun.started_at.desc())  # type: ignore[attr-defined]
        .limit(1)
    )
    return session.exec(stmt).first()


def _latest_run_per_source(session) -> dict[str, models.SourceRun]:
    """Most recent `SourceRun` for every source that has ever run, in one
    query (T3.3) — replaces the N-query `_last_run_for` loop `list_sources`
    used to run once per configured source.

    `ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC)` picks
    exactly one (the newest) row per source, the same semantics as
    `_last_run_for`'s `ORDER BY started_at DESC LIMIT 1`. Selecting the
    windowed subquery back through `aliased(models.SourceRun, subq)` yields
    real `SourceRun` instances, so `SourceRunOut.from_run` needs no change.
    """
    rn = func.row_number().over(
        partition_by=models.SourceRun.source,
        order_by=models.SourceRun.started_at.desc(),  # type: ignore[attr-defined]
    ).label("rn")
    subq = select(models.SourceRun, rn).subquery()
    latest = aliased(models.SourceRun, subq)
    stmt = select(latest).where(subq.c.rn == 1)
    return {run.source: run for run in session.exec(stmt).all()}


def _runs_for(
    session, source_name: str, limit: int
) -> list[models.SourceRun]:
    stmt = (
        select(models.SourceRun)
        .where(models.SourceRun.source == source_name)
        .order_by(models.SourceRun.started_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(session.exec(stmt).all())


_HEALTH_WINDOW = timedelta(hours=24)


def _last_24h_health_per_source(session) -> dict[str, SourceHealthOut]:
    """Attempted/succeeded totals per source over the health window, in ONE
    grouped query — deliberately not a per-source loop, which is the shape
    this module just lost in T3.3."""
    since = datetime.now(UTC) - _HEALTH_WINDOW
    stmt = (
        select(
            models.SourceRun.source,
            func.coalesce(func.sum(models.SourceRun.books_attempted), 0),
            func.coalesce(func.sum(models.SourceRun.books_succeeded), 0),
            func.coalesce(func.sum(models.SourceRun.items_challenged), 0),
        )
        .where(models.SourceRun.started_at >= since)
        .group_by(models.SourceRun.source)  # type: ignore[arg-type]
    )
    out: dict[str, SourceHealthOut] = {}
    for source, attempted, succeeded, challenged in session.exec(stmt).all():
        out[source] = SourceHealthOut(
            attempted=int(attempted),
            succeeded=int(succeeded),
            challenged=int(challenged),
        )
    return out


def _last_24h_health_for(session, source_name: str) -> SourceHealthOut:
    """Single-source variant, for the endpoints that return one source."""
    return _last_24h_health_per_source(session).get(source_name, SourceHealthOut())


def _status_for(
    name: str,
    sc: SourceConfig,
    last: models.SourceRun | None,
    health: SourceHealthOut | None = None,
) -> SourceStatusOut:
    return SourceStatusOut(
        name=name,
        config=SourceConfigOut.from_config(sc),
        last_run=SourceRunOut.from_run(last) if last is not None else None,
        last_24h=health or SourceHealthOut(),
    )


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=list[SourceStatusOut])
def list_sources(session: SessionDep, cfg: ConfigDep) -> list[SourceStatusOut]:
    """Per-source status, sorted alphabetically by source name."""
    latest_runs = _latest_run_per_source(session)
    health = _last_24h_health_per_source(session)
    return [
        _status_for(name, cfg.sources[name], latest_runs.get(name), health.get(name))
        for name in sorted(cfg.sources.keys())
    ]


@router.get("/{name}/runs", response_model=list[SourceRunOut])
def list_source_runs(
    name: str,
    session: SessionDep,
    cfg: ConfigDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[SourceRunOut]:
    """Recent `SourceRun` rows for `name`, newest first (default 10, max 100).

    404 if `name` is not configured. Matches the wire shape of `last_run` in
    `GET /api/sources` (excludes `error_traceback`).
    """
    if name not in cfg.sources:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="source not found"
        )
    return [SourceRunOut.from_run(r) for r in _runs_for(session, name, limit)]


@router.post("/{name}/run", response_model=TriggerRunResult)
async def trigger_source_run(
    name: str,
    cfg: ConfigDep,
    scheduler: SchedulerDep,
) -> TriggerRunResult:
    """Trigger an immediate scrape via `scheduler.trigger_now(name)`.

    Returns the new `SourceRun.id`. 404 if `name` is not in the configured
    sources. 409 when the scheduler returns 0 (backoff gate is active).
    """
    if name not in cfg.sources:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="source not found"
        )
    run_id = await scheduler.trigger_now(name)
    if run_id == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source is in backoff; run skipped",
        )
    return TriggerRunResult(run_id=run_id)


@router.patch("/{name}", response_model=SourceStatusOut)
async def patch_source(
    name: str,
    payload: SourcePatch,
    request: Request,
    session: SessionDep,
    cfg: ConfigDep,
    cfg_path: ConfigPathDep,
) -> SourceStatusOut:
    """Partial update of a source's config.

    Only `enabled`, `schedule`, and `concurrency` may be patched (mirrors
    `SourcePatch`). Empty body is a 200 no-op. The whole config is re-validated
    via `Config.model_validate`, written back to disk via `Config.save`, and
    swapped into `app.state.config`.
    """
    if name not in cfg.sources:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="source not found"
        )

    # `exclude_unset=True` so untouched fields don't reset to their defaults.
    # `is not None` so explicit-`null` from the wire is also a no-op (matches
    # the "None means don't change" contract).
    patch_data = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None
    }

    current = cfg.sources[name]
    if patch_data:
        # `model_copy(update=...)` does NOT re-validate, so a bad value (e.g.
        # `concurrency=99` outside `ge=1, le=5`) slips through here and is
        # caught by the single `Config.model_validate` pass below. One
        # validation, one 422 path. Keeping it at the `Config` level means
        # any future cross-field invariants are also enforced here.
        updated = current.model_copy(update=patch_data)
        new_sources = {**cfg.sources, name: updated}
        try:
            new_cfg = Config.model_validate(
                cfg.model_copy(update={"sources": new_sources}).model_dump()
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        new_cfg.save(cfg_path)
        request.app.state.config = new_cfg
        from book_alerter.app import rebuild_runtime
        rebuild_runtime(request.app)
        sc = new_cfg.sources[name]
    else:
        sc = current

    return _status_for(name, sc, _last_run_for(session, name), _last_24h_health_for(session, name))
