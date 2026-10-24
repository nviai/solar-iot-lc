"""The polling schedule, including quiet hours."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..deps import get_poller, get_schedule_config
from ..poller import Poller
from ..runtime import RuntimeConfig

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


def _view(poller: Poller, config: RuntimeConfig) -> dict[str, Any]:
    return {**poller.schedule_status(), "fields": config.describe()}


@router.get("")
async def get_schedule(
    poller: Poller = Depends(get_poller),
    config: RuntimeConfig = Depends(get_schedule_config),
):
    return _view(poller, config)


@router.put("")
async def put_schedule(
    payload: dict[str, Any] = Body(...),
    poller: Poller = Depends(get_poller),
    config: RuntimeConfig = Depends(get_schedule_config),
):
    try:
        config.update(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _view(poller, config)
