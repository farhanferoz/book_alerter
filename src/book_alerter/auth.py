"""Optional HTTP Basic auth for the API.

Off by default. Enabled when both `APP_BASIC_AUTH_USER` and `APP_BASIC_AUTH_PASS`
are set to non-empty values. Intended for non-Tailscale deployments where the
API is reachable beyond a trusted network boundary.

The enabled flag is evaluated at `create_app()` time. Env-var changes after
startup will not flip auth on/off until the app restarts — fine for the
NAS deployment model this project targets.
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


def is_basic_auth_enabled() -> bool:
    """Return True iff both env vars are set to non-empty strings."""
    return bool(os.environ.get("APP_BASIC_AUTH_USER")) and bool(
        os.environ.get("APP_BASIC_AUTH_PASS")
    )


_security = HTTPBasic()

BasicCredsDep = Annotated[HTTPBasicCredentials, Depends(_security)]


def basic_auth_dep(credentials: BasicCredsDep) -> None:
    """Validate Basic credentials against the configured env vars.

    No-op when auth is disabled (kept defensively so the dep is safe to apply
    unconditionally if a caller forgets the `create_app()`-time gate).
    """
    if not is_basic_auth_enabled():
        return
    user = os.environ["APP_BASIC_AUTH_USER"]
    pw = os.environ["APP_BASIC_AUTH_PASS"]
    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pw = secrets.compare_digest(credentials.password, pw)
    if not (ok_user and ok_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
