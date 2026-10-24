"""Jinja-rendered pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..client import SolaxClient
from ..config import settings
from ..deps import get_client, get_poller, get_storage
from ..poller import Poller
from ..storage import Storage
from ..telemetry import known_devices

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

router = APIRouter(include_in_schema=False)


def _render(request: Request, template: str, active: str, poller: Poller, **extra: Any):
    return templates.TemplateResponse(
        request,
        template,
        {
            "active": active,
            "settings": settings,
            "version": __version__,
            "devices": known_devices(poller),
            **extra,
        },
    )


@router.get("/")
async def overview(request: Request, poller: Poller = Depends(get_poller)):
    return _render(request, "overview.html", "overview", poller)


@router.get("/history")
async def history(request: Request, poller: Poller = Depends(get_poller)):
    return _render(request, "history.html", "history", poller)


@router.get("/alerts")
async def alerts(request: Request, poller: Poller = Depends(get_poller)):
    return _render(request, "alerts.html", "alerts", poller)


@router.get("/settings")
async def settings_page(
    request: Request,
    poller: Poller = Depends(get_poller),
    storage: Storage = Depends(get_storage),
    client: SolaxClient = Depends(get_client),
):
    diagnosis = None
    if settings.configured and not settings.demo_mode:
        diagnosis = await client.diagnose()

    base_url = str(request.base_url).rstrip("/")
    return _render(
        request,
        "settings.html",
        "settings",
        poller,
        status=poller.status(),
        diagnosis=diagnosis,
        callbacks=storage.list_callbacks(20),
        callback_url=f"{base_url}/callback/solax",
        api_base=f"{base_url}/api/v1",
    )
