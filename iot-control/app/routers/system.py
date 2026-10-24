"""Service health and the solar feed."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..automation import DeviceController
from ..config import settings
from ..deps import get_controllers, get_feed, inventory_error
from ..solar import SolarFeed

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/status", summary="Solar feed health and device count")
async def status(
    feed: SolarFeed = Depends(get_feed),
    controllers: dict[str, DeviceController] = Depends(get_controllers),
):
    return {
        "solar": feed.status(),
        "devices": len(controllers),
        "devices_online": sum(1 for c in controllers.values() if c.switch.state.online),
        "inventory_error": inventory_error,
        "devices_file": str(settings.devices_file),
        "timezone": settings.timezone,
    }


@router.get("/solar", summary="Latest power snapshot from the Solar Monitor")
async def solar(refresh: bool = False, feed: SolarFeed = Depends(get_feed)):
    if refresh:
        await feed.fetch()
    return {"status": feed.status(), "snapshot": feed.snapshot}
