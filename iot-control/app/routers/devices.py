"""Device API: switch state, manual control, automation settings, activity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..automation import DeviceController
from ..deps import get_controller, get_controllers, get_storage
from ..schemas import SwitchRequest
from ..storage import Storage

router = APIRouter(prefix="/api/v1", tags=["devices"])


def _detail(controller: DeviceController, storage: Storage) -> dict[str, Any]:
    return {
        **controller.snapshot(),
        "fields": controller.config.describe(),
        "events": storage.list_events(controller.id, 30),
    }


def _require_available(controller: DeviceController) -> None:
    if not controller.switch.available:
        raise HTTPException(409, controller.switch.unavailable_reason or "Not available")


# ----------------------------------------------------------------------
@router.get("/devices", summary="Every device with its current state")
async def list_devices(controllers: dict[str, DeviceController] = Depends(get_controllers)):
    return {"devices": [c.snapshot() for c in controllers.values()]}


@router.get("/devices/{device_id}", summary="One device: state, automation fields, recent activity")
async def device(
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    return _detail(controller, storage)


@router.post("/devices/{device_id}/switch", summary="Switch by hand (arms the manual override)")
async def switch(
    body: SwitchRequest,
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    _require_available(controller)
    result = await controller.manual(body.on)
    if not result["ok"]:
        raise HTTPException(502, result["state"].get("error") or "The switch did not respond")
    return _detail(controller, storage)


@router.post("/devices/{device_id}/refresh", summary="Re-read the relay and re-run the rules now")
async def refresh(
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    _require_available(controller)
    await controller.tick()
    return _detail(controller, storage)


@router.post("/devices/{device_id}/override/clear", summary="Hand control back to automation")
async def clear_override(
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    controller.clear_override()
    if controller.switch.available:
        await controller.tick()
    return _detail(controller, storage)


@router.put("/devices/{device_id}/automation", summary="Update automation settings (partial)")
async def update_automation(
    payload: dict[str, Any] = Body(...),
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    try:
        controller.config.update(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if controller.switch.available:
        await controller.tick()
    return _detail(controller, storage)


@router.get("/devices/{device_id}/events", summary="Activity log for one device")
async def device_events(
    limit: int = Query(30, ge=1, le=500),
    controller: DeviceController = Depends(get_controller),
    storage: Storage = Depends(get_storage),
):
    return {"events": storage.list_events(controller.id, limit)}


@router.get("/events", summary="Activity log across every device")
async def all_events(
    limit: int = Query(100, ge=1, le=1000),
    device: str | None = Query(None, description="Only this device id."),
    storage: Storage = Depends(get_storage),
    controllers: dict[str, DeviceController] = Depends(get_controllers),
):
    events = storage.list_events(device, limit)
    for event in events:
        controller = controllers.get(event["device_id"])
        event["device_name"] = controller.spec.name if controller else event["device_id"]
    return {"events": events}
