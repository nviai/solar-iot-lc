"""Inbound endpoint for the "Callback Address" of a developer-portal application.

The portal describes the field as *"For asynchronous callback message
reception"*. It is where SolaX pushes data for the **Data Subscription**
feature — realtime samples or alarm records — instead of you polling for them,
and where asynchronous device-control results are delivered.

SolaX must be able to reach the URL from the internet, and it must answer 2xx
quickly or the delivery counts as failed and is retried.

The push envelope is not published and varies per subscription template, so
this handler is deliberately permissive: it accepts any JSON shape, logs it
verbatim for inspection on /settings, and opportunistically ingests anything
that looks like a telemetry record.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..alerts import AlertEngine
from ..config import settings
from ..deps import get_alert_engine, get_poller, get_storage
from ..models import Reading
from ..poller import Poller
from ..storage import Storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/callback", tags=["callback"])


def _authorised(request: Request) -> bool:
    """Check the shared secret, if one is configured.

    The signature scheme is subscription-specific, so the secret is accepted as
    either a header or a query parameter and compared in constant time.
    """
    expected = settings.callback_secret
    if not expected:
        return True
    provided = (
        request.headers.get("x-solax-signature")
        or request.headers.get("x-callback-secret")
        or request.query_params.get("secret")
        or ""
    )
    return hmac.compare_digest(provided, expected)


def _extract_records(body: Any) -> list[dict[str, Any]]:
    """Pull telemetry-looking dicts out of whatever envelope arrived."""
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []

    for key in ("result", "data", "records", "list", "items"):
        inner = body.get(key)
        if isinstance(inner, dict):
            return [inner]
        if isinstance(inner, list):
            return [row for row in inner if isinstance(row, dict)]

    # A bare record, identified by carrying a device serial.
    if any(key in body for key in ("registerNo", "deviceSn", "sn")):
        return [body]
    return []


@router.post("/solax")
async def receive(
    request: Request,
    storage: Storage = Depends(get_storage),
    poller: Poller = Depends(get_poller),
    alerts: AlertEngine = Depends(get_alert_engine),
):
    """Receive a data-subscription push."""
    if not _authorised(request):
        raise HTTPException(401, "Invalid callback secret")

    try:
        body = await request.json()
    except Exception:
        body = (await request.body()).decode("utf-8", "replace")

    storage.log_callback(
        {k: v for k, v in request.headers.items() if k.lower() != "authorization"},
        body,
    )

    ingested = 0
    for record in _extract_records(body):
        register_no = record.get("registerNo") or record.get("sn") or ""
        if not register_no:
            continue
        try:
            reading = Reading.parse(record, register_no=register_no)
        except Exception:
            log.exception("Could not parse a pushed record")
            continue

        poller.latest[register_no] = reading
        storage.insert_reading(reading)
        await alerts.evaluate(reading)
        ingested += 1

    # SolaX retries on non-2xx, so acknowledge even when nothing was usable.
    return {"success": True, "ingested": ingested}


@router.get("/solax")
async def probe():
    """Some providers GET the endpoint once to verify reachability."""
    return {"success": True, "message": "Solax callback endpoint is alive"}
