"""Public telemetry API: live power, per-device readings and history.

Everything here except ``POST /refresh`` and ``GET /alarms`` is served from
memory or the local database and costs no SolaX quota.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..client import SolaxClient
from ..config import settings
from ..deps import get_client, get_poller, get_storage
from ..errors import SolaxError
from ..poller import Poller
from ..schemas import PowerSnapshot
from ..storage import Storage
from ..telemetry import known_devices, latest_public, power_snapshot

router = APIRouter(prefix="/api/v1", tags=["telemetry"])


def _require_device(register_no: str, poller: Poller) -> str:
    known = known_devices(poller)
    if known and register_no not in known:
        raise HTTPException(
            404, f"Unknown device {register_no!r}. Known: {', '.join(known)}"
        )
    return register_no


def _device_entry(register_no: str, poller: Poller, storage: Storage) -> dict[str, Any]:
    return {
        "register_no": register_no,
        "data": latest_public(register_no, poller, storage),
        "error": poller.errors.get(register_no),
        "live": register_no in poller.latest,
    }


# ----------------------------------------------------------------------
@router.get("/status", summary="Poller and service status")
async def status(
    poller: Poller = Depends(get_poller), storage: Storage = Depends(get_storage)
):
    return {
        **poller.status(),
        "configured": settings.configured,
        "unacked_alerts": storage.count_unacked(),
        "currency": settings.currency,
        "energy_price": settings.energy_price,
    }


@router.get(
    "/power",
    response_model=PowerSnapshot,
    summary="Live power, aggregated across devices",
    description=(
        "The integration endpoint. Served from memory, so poll it as often as "
        "you like; check age_seconds before acting on the figures."
    ),
)
async def power(
    poller: Poller = Depends(get_poller), storage: Storage = Depends(get_storage)
):
    return power_snapshot(poller, storage)


@router.get("/devices", summary="Latest full reading for every device")
async def devices(
    poller: Poller = Depends(get_poller), storage: Storage = Depends(get_storage)
):
    return {
        "devices": [_device_entry(rn, poller, storage) for rn in known_devices(poller)],
        "last_poll_at": poller.last_poll_at.isoformat() if poller.last_poll_at else None,
    }


@router.get("/devices/{register_no}", summary="Latest full reading for one device")
async def device(
    register_no: str,
    poller: Poller = Depends(get_poller),
    storage: Storage = Depends(get_storage),
):
    return _device_entry(_require_device(register_no, poller), poller, storage)


@router.get("/devices/{register_no}/history", summary="Stored power samples")
async def history(
    register_no: str,
    hours: int = Query(24, ge=1, le=24 * 90),
    storage: Storage = Depends(get_storage),
    poller: Poller = Depends(get_poller),
):
    _require_device(register_no, poller)
    return {
        "register_no": register_no,
        "hours": hours,
        "points": storage.history(register_no, hours),
    }


@router.get("/devices/{register_no}/daily", summary="Energy totals per day (kWh)")
async def daily(
    register_no: str,
    days: int = Query(14, ge=1, le=90),
    storage: Storage = Depends(get_storage),
    poller: Poller = Depends(get_poller),
):
    _require_device(register_no, poller)
    return {"register_no": register_no, "days": storage.daily_totals(register_no, days)}


# ----------------------------------------------------------------------
@router.post("/refresh", summary="Poll SolaX now (costs API quota)")
async def refresh(poller: Poller = Depends(get_poller)):
    results = await poller.poll_once()
    errors = {sn: str(r) for sn, r in results.items() if isinstance(r, SolaxError)}
    return {"polled": len(results), "errors": errors}


@router.get("/alarms", summary="Alarms raised by SolaX itself (costs API quota)")
async def alarms(
    device: str | None = Query(None),
    poller: Poller = Depends(get_poller),
    client: SolaxClient = Depends(get_client),
):
    if settings.demo_mode:
        return {"alarms": []}
    target = device or next(iter(known_devices(poller)), None)
    try:
        return {"alarms": await client.alarms(target)}
    except SolaxError as exc:
        raise HTTPException(502, str(exc)) from exc
