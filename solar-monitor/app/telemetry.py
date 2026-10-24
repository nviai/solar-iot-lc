"""Aggregation of the poller's latest readings into the public power snapshot.

The snapshot is built from memory — whatever the poller last fetched — so
serving it costs no SolaX quota however often it is read. Before the first
poll after a restart, the newest stored sample stands in, with its real
timestamp, so consumers can judge its age themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .poller import Poller
from .storage import Storage

POWER_KEYS = (
    "pv_power", "load_power", "grid_power", "grid_import", "grid_export",
    "battery_power", "battery_charge", "battery_discharge",
)


def known_devices(poller: Poller) -> list[str]:
    return poller.known_devices or list(poller.latest)


def latest_public(register_no: str, poller: Poller, storage: Storage) -> dict[str, Any] | None:
    """The newest reading for a device as a flat dict, from memory or disk."""
    reading = poller.latest.get(register_no)
    return reading.to_public() if reading else storage.latest(register_no)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _device_power(register_no: str, data: dict[str, Any], error: str | None,
                  now: datetime) -> dict[str, Any]:
    measured_at = _parse_ts(data["ts"])
    return {
        "register_no": register_no,
        "measured_at": measured_at,
        "age_seconds": round((now - measured_at).total_seconds(), 1),
        **{key: float(data.get(key) or 0.0) for key in POWER_KEYS},
        "soc": data.get("soc"),
        "yield_today": data.get("yield_today"),
        "status": data.get("status"),
        "status_label": data.get("status_label"),
        "error": error,
    }


def _totals(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not devices:
        return None
    totals: dict[str, Any] = {
        key: round(sum(d[key] for d in devices), 1) for key in POWER_KEYS
    }
    socs = [d["soc"] for d in devices if d["soc"] is not None]
    totals["soc"] = round(sum(socs) / len(socs), 1) if socs else None
    yields = [d["yield_today"] for d in devices if d["yield_today"] is not None]
    totals["yield_today"] = round(sum(yields), 2) if yields else None
    return totals


def power_snapshot(poller: Poller, storage: Storage) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    devices = []
    for register_no in known_devices(poller):
        data = latest_public(register_no, poller, storage)
        if data and data.get("ts"):
            devices.append(
                _device_power(register_no, data, poller.errors.get(register_no), now)
            )

    oldest = min((d["measured_at"] for d in devices), default=None)
    return {
        "measured_at": oldest,
        "age_seconds": round((now - oldest).total_seconds(), 1) if oldest else None,
        "poll_interval_seconds": poller.interval,
        "paused": poller.paused,
        "totals": _totals(devices),
        "devices": devices,
    }
