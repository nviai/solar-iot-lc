"""Jinja-rendered pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..automation import DeviceController
from ..config import settings
from ..deps import get_controller, get_controllers, get_feed, inventory_error
from ..solar import SolarFeed

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

router = APIRouter(include_in_schema=False)


def _render(request: Request, template: str, active: str,
            controllers: dict[str, DeviceController], **extra: Any):
    return templates.TemplateResponse(
        request,
        template,
        {
            "active": active,
            "settings": settings,
            "version": __version__,
            "devices": [c.switch.describe() for c in controllers.values()],
            "inventory_error": inventory_error,
            **extra,
        },
    )


@router.get("/")
async def overview(
    request: Request,
    controllers: dict[str, DeviceController] = Depends(get_controllers),
):
    return _render(request, "devices.html", "devices", controllers)


@router.get("/devices/{device_id}")
async def device(
    request: Request,
    controller: DeviceController = Depends(get_controller),
    controllers: dict[str, DeviceController] = Depends(get_controllers),
):
    return _render(
        request, "device.html", controller.id, controllers,
        device=controller.switch.describe(),
    )


@router.get("/activity")
async def activity(
    request: Request,
    controllers: dict[str, DeviceController] = Depends(get_controllers),
):
    return _render(request, "activity.html", "activity", controllers)


@router.get("/system")
async def system(
    request: Request,
    controllers: dict[str, DeviceController] = Depends(get_controllers),
    feed: SolarFeed = Depends(get_feed),
):
    return _render(
        request, "system.html", "system", controllers,
        solar=feed.status(),
        api_base=str(request.base_url).rstrip("/") + "/api/v1",
    )
