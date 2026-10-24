"""Background poller: reads every device on an interval, stores the sample,
and runs the alert engine over it.

Each cycle costs two or three API calls per device (inverter, battery, and
optionally the plant totals), which counts against the hits/min and hits/day
quota of the application's API service package. That quota is the reason for
quiet hours: overnight there is no production to watch, so the interval either
stretches or the loop stops calling out altogether until morning.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from typing import Any

from .alerts import AlertEngine
from .client import SolaxClient
from .config import Settings
from .errors import SolaxError
from .models import Reading
from .runtime import RuntimeConfig, get_zone, in_window, parse_hhmm, seconds_until
from .storage import Storage

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 15

#: Longest the loop will sleep through a pause without re-reading its config,
#: so a settings change is never more than this far from taking effect.
MAX_SLEEP_S = 900


class Poller:
    """Owns the polling loop and the in-memory view of the latest readings."""

    def __init__(
        self,
        client: SolaxClient,
        storage: Storage,
        alerts: AlertEngine,
        settings: Settings,
        schedule: RuntimeConfig,
    ) -> None:
        self.client = client
        self.storage = storage
        self.alerts = alerts
        self.settings = settings
        self.schedule = schedule

        self.latest: dict[str, Reading] = {}
        self.errors: dict[str, str] = {}
        self.last_poll_at: datetime | None = None
        self.poll_count = 0
        self.known_devices: list[str] = []

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._forced = False
        #: When the loop last attempted a cycle. Distinct from ``last_poll_at``,
        #: which only moves on a cycle that actually reached the API, and which
        #: would therefore never pace a loop that has no devices to poll.
        self._last_cycle_at: datetime | None = None

        # Re-plan the next wake-up whenever the schedule is edited, without
        # spending a call: a shortened interval should not wait out the old one.
        schedule.on_change(lambda _cfg: self.reschedule())

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------
    def local_now(self) -> datetime:
        return datetime.now(get_zone(self.settings.timezone))

    def quiet_now(self) -> bool:
        cfg = self.schedule.all()
        if not cfg["quiet_enabled"]:
            return False
        start = parse_hhmm(cfg["quiet_start"], time(23, 0))
        end = parse_hhmm(cfg["quiet_end"], time(5, 0))
        return in_window(self.local_now().time(), start, end)

    @property
    def interval(self) -> int:
        """Seconds between polls right now, quiet hours included."""
        cfg = self.schedule.all()
        if self.quiet_now() and cfg["quiet_mode"] == "interval":
            return max(MIN_INTERVAL_S, cfg["quiet_interval_seconds"])
        return max(MIN_INTERVAL_S, cfg["poll_interval_seconds"])

    @property
    def paused(self) -> bool:
        """True when quiet hours say not to call the API at all."""
        return self.quiet_now() and self.schedule.get("quiet_mode") == "pause"

    def _elapsed(self) -> float:
        """Seconds since the last cycle, or infinity before the first one."""
        if self._last_cycle_at is None:
            return float("inf")
        return (datetime.now(timezone.utc) - self._last_cycle_at).total_seconds()

    def _due(self) -> bool:
        """Has a full interval passed since the last cycle?

        Checked against the clock rather than assumed from the timeout, so a
        wake-up caused by an edited schedule re-plans instead of spending a
        cycle — otherwise every save on the Settings page would cost API calls.
        """
        return self._elapsed() >= self.interval - 0.5

    def _sleep_seconds(self) -> float:
        """How long to wait before looking again."""
        if self.paused:
            # Wake up when the quiet window ends, so polling resumes on time.
            end = parse_hhmm(self.schedule.get("quiet_end"), time(5, 0))
            return min(MAX_SLEEP_S, max(1.0, seconds_until(self.local_now(), end)))
        return min(MAX_SLEEP_S, max(1.0, self.interval - self._elapsed()))

    def schedule_status(self) -> dict[str, Any]:
        cfg = self.schedule.all()
        quiet = self.quiet_now()
        return {
            **cfg,
            "quiet_now": quiet,
            "paused": self.paused,
            "effective_interval_seconds": self.interval,
            "local_time": self.local_now().strftime("%H:%M"),
            "timezone": self.settings.timezone,
        }

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="solax-poller")
        log.info("Poller started, every %ds", self.interval)

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

    def trigger(self) -> None:
        """Poll immediately, even during a quiet-hours pause."""
        self._forced = True
        self._wake.set()

    def reschedule(self) -> None:
        """Recompute the next wake-up without spending a poll on it."""
        self._wake.set()

    async def _run(self) -> None:
        # Poll once immediately so the dashboard is populated on boot, unless
        # we are starting inside a paused quiet window.
        while not self._stop.is_set():
            forced, self._forced = self._forced, False
            self._wake.clear()

            if forced or (not self.paused and self._due()):
                self._last_cycle_at = datetime.now(timezone.utc)
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # keep the loop alive whatever happens
                    log.exception("Poll cycle failed")

            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._sleep_seconds())
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    async def devices(self) -> list[str]:
        """Devices to poll: the configured filter, else everything discovered."""
        if self.known_devices:
            return self.known_devices
        try:
            found = list(await self.client.discover())
        except SolaxError as exc:
            log.warning("Could not list devices: %s", exc)
            return []
        wanted = self.settings.device_filter
        self.known_devices = [d for d in found if d in wanted] if wanted else found
        if wanted:
            for missing in set(wanted) - set(found):
                log.warning("Configured device %s is not in this account", missing)
        self._log_budget()
        return self.known_devices

    def _log_budget(self) -> None:
        calls_per_cycle = 3 if self.settings.fetch_plant_totals else 2
        per_day = len(self.known_devices) * calls_per_cycle * 86400 / self.interval
        log.info(
            "Polling %d device(s) every %ds — about %.0f API calls/day",
            len(self.known_devices),
            self.interval,
            per_day,
        )

    async def poll_once(self) -> dict[str, Reading | SolaxError]:
        """One full cycle across all devices. Safe to call ad hoc."""
        register_nos = await self.devices()
        if not register_nos:
            return {}

        results = await self.client.realtime_many(register_nos)
        self.last_poll_at = datetime.now(timezone.utc)
        self.poll_count += 1

        for register_no, result in results.items():
            if isinstance(result, SolaxError):
                self.errors[register_no] = str(result)
                log.warning("Poll of %s failed: %s", register_no, result)
                continue

            self.errors.pop(register_no, None)
            self.latest[register_no] = result
            try:
                self.storage.insert_reading(result)
            except Exception:
                log.exception("Could not persist the reading for %s", register_no)
            try:
                await self.alerts.evaluate(result)
            except Exception:
                log.exception("Alert evaluation failed for %s", register_no)

        # Housekeeping roughly once every 1000 cycles.
        if self.poll_count % 1000 == 0:
            try:
                removed = self.storage.prune(self.settings.retention_days)
                log.info("Pruned %d old readings", removed)
            except Exception:
                log.exception("Prune failed")

        return results

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval,
            "devices": self.known_devices,
            "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
            "poll_count": self.poll_count,
            "errors": dict(self.errors),
            "demo_mode": self.settings.demo_mode,
            "api_base": self.settings.openapi_base,
            "quiet_now": self.quiet_now(),
            "paused": self.paused,
        }
