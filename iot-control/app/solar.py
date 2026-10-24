"""Live power figures, read from the Solar Monitor's public API.

This app never talks to SolaX. It reads ``GET /api/v1/power`` from the Solar
Monitor, which answers from memory with whatever its own poller last fetched,
so reading it every few seconds costs no SolaX API quota at all.

The feed runs as one background loop shared by every device controller, so
however many devices there are, the monitor sees one reader.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)

POWER_PATH = "/api/v1/power"


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class SolarFeed:
    """Keeps the latest power snapshot from the Solar Monitor."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.snapshot: dict[str, Any] | None = None
        #: When the monitor last answered successfully.
        self.fetched_at: datetime | None = None
        #: When the last attempt was made, successful or not.
        self.attempted_at: datetime | None = None
        self.error: str | None = None

        self._http: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None:
            return
        self._http = httpx.AsyncClient(
            base_url=self.settings.solar_api_base,
            timeout=self.settings.solar_timeout_seconds,
            headers={"User-Agent": "iot-control/2.0", "Accept": "application/json"},
        )
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="solar-feed")
        log.info("Reading power from %s%s every %gs",
                 self.settings.solar_api_base, POWER_PATH, self.settings.solar_poll_seconds)

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def trigger(self) -> None:
        """Fetch now instead of waiting out the interval."""
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.fetch()
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=max(1.0, self.settings.solar_poll_seconds)
                )
            except asyncio.TimeoutError:
                pass

    async def fetch(self) -> bool:
        """One read of the monitor. Never raises; failures land in ``error``."""
        if self._http is None:
            return False
        self.attempted_at = datetime.now(timezone.utc)
        try:
            resp = await self._http.get(POWER_PATH)
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                raise ValueError("unexpected response shape")
        except httpx.HTTPStatusError as exc:
            self._fail(f"Solar Monitor answered HTTP {exc.response.status_code}")
            return False
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(f"Solar Monitor unreachable: {exc or type(exc).__name__}")
            return False

        if self.error:
            log.info("Solar Monitor reachable again")
        self.snapshot = payload
        self.fetched_at = self.attempted_at
        self.error = None
        return True

    def _fail(self, message: str) -> None:
        if message != self.error:
            log.warning("%s", message)
        self.error = message

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @property
    def ok(self) -> bool:
        return self.error is None and self.snapshot is not None

    @property
    def totals(self) -> dict[str, Any] | None:
        return (self.snapshot or {}).get("totals") or None

    @property
    def measured_at(self) -> datetime | None:
        """When the inverter reading behind the snapshot was taken."""
        return _parse_ts((self.snapshot or {}).get("measured_at"))

    def pv_power(self) -> tuple[float | None, datetime | None]:
        """Total solar production (W), and when it was measured.

        The timestamp is the inverter's, not this app's fetch time, so a
        monitor that is up but no longer polling still reads as stale.
        """
        totals = self.totals
        if not totals or totals.get("pv_power") is None:
            return None, None
        return float(totals["pv_power"]), self.measured_at

    def status(self) -> dict[str, Any]:
        measured_at = self.measured_at
        age = (
            (datetime.now(timezone.utc) - measured_at).total_seconds()
            if measured_at else None
        )
        return {
            "ok": self.ok,
            "source": self.settings.solar_api_base + POWER_PATH,
            "public_url": self.settings.solar_link,
            "poll_seconds": self.settings.solar_poll_seconds,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
            "error": self.error,
            "measured_at": measured_at.isoformat() if measured_at else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "paused": bool((self.snapshot or {}).get("paused")),
        }
