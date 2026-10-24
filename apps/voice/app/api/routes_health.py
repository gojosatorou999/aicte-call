from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.config import get_settings
from app.utils.drain import is_draining

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Liveness. Always 200 while the process is up; core reachability is
    reported, not gating, because the greeting works without core."""
    settings = get_settings()
    core = getattr(request.app.state, "core", None)
    return {
        "status": "draining" if is_draining() else "ok",
        "service": "acp-voice",
        "version": __version__,
        "agent_version": settings.agent_version,
        "env": settings.app_env.value,
        "core_reachable": await core.ping() if core else None,
    }
