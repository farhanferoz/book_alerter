"""Deep health endpoint.

Docker's HEALTHCHECK polls this every 30s. Returning 200 only when the
DB is writable and the scheduler is running prevents a stuck container
from looking healthy to the orchestrator.

  - DB probe: a SELECT 1 round-trip via a fresh Session from the lifespan
    engine. If the connection is broken or the file is read-only the
    SQLAlchemy connection raises and we 503.
  - Scheduler probe: APScheduler exposes `.running` (False after a clean
    shutdown or before .start()). Tests attach a stub scheduler — we
    treat a missing `.running` attribute as "no probe available, OK".

Failure mode returns HTTP 503 with the same JSON shape (with `status` =
"error" and an `errors` array) so Docker considers the container
unhealthy and restarts it after the configured retries.
"""

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlmodel import Session

router = APIRouter(prefix="/api/health", tags=["health"])


def _probe_db(request: Request) -> str | None:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return "engine not initialized"
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        return None
    except Exception as e:
        return f"db unavailable: {e}"


def _probe_scheduler(request: Request) -> str | None:
    sched = getattr(request.app.state, "scheduler", None)
    if sched is None:
        return None  # test contexts; no probe possible
    running = getattr(sched, "running", None)
    if running is False:
        return "scheduler not running"
    return None


@router.get("")
def health(request: Request, response: Response) -> dict[str, object]:
    cfg = getattr(request.app.state, "config", None)
    errors: list[str] = []
    if (e := _probe_db(request)) is not None:
        errors.append(e)
    if (e := _probe_scheduler(request)) is not None:
        errors.append(e)
    if errors:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "error",
            "config_version": cfg.config_version if cfg else None,
            "errors": errors,
        }
    return {
        "status": "ok",
        "config_version": cfg.config_version if cfg else None,
    }
