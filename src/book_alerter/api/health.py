"""Deep health endpoint.

Docker's HEALTHCHECK polls this every 30s. 503 (not 200-with-an-errors-
field) is critical — only the status code makes the orchestrator
restart the container after the configured retries. Test stubs that
omit `.running` are treated as "no probe available, OK".
"""

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlmodel import Session

from book_alerter.logging_setup import get_logger

router = APIRouter(prefix="/api/health", tags=["health"])
log = get_logger(__name__)


def _probe_db(request: Request) -> str | None:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return "engine not initialized"
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        return None
    except Exception as e:
        # Log the full error for ops, but return a generic message so the
        # unauthenticated /api/health response doesn't echo SQLAlchemy
        # internals (table names, full statement text).
        log.error("health.db_probe.failed", error=str(e))
        return "db probe failed"


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
        # Null until the daily janitor has run once in this process. Reported
        # so a silently-dead cleanup job is visible before the disk fills,
        # rather than after. Absence is informational, never a health error.
        "janitor_last_run_at": getattr(request.app.state, "janitor_last_run_at", None),
    }
