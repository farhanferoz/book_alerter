from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(request: Request) -> dict[str, object]:
    cfg = getattr(request.app.state, "config", None)
    return {
        "status": "ok",
        "config_version": cfg.config_version if cfg else None,
    }
