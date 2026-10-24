"""Automation for one switch: surplus sun by day, the off-peak tariff by night.

Each device gets its own :class:`DeviceController` and its own loop. The loop
reads the relay over the LAN (free) and reads solar production from the shared
:class:`~app.solar.SolarFeed`, which in turn reads the Solar Monitor's cached
figures (also free), so tightening the evaluation interval never costs SolaX
API quota.

The decision is a hysteresis band rather than a single threshold. Above
``on_threshold_w`` the device should run, below ``off_threshold_w`` it should
not, and in between it holds whatever it is already doing — a load drawing
several kW would otherwise push production down through a single threshold
the instant it started, and oscillate. On top of that a change has to persist
for ``sustain_s`` before the relay actually moves, which absorbs passing
clouds.

Off-peak mode is a plain schedule on top of that: inside the off-peak window
the device is on, and the moment the window ends it goes off. Schedule edges
act at once — there is no cloud to wait out — and need no solar data at all,
so a night charge still runs when the Solar Monitor is down. Where the two
windows overlap, off-peak wins.

A manual press always wins immediately, and then holds automation off for
``manual_override_minutes`` so the next tick does not silently undo it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from .config import Settings
from .registry import DeviceSpec
from .runtime import (
    RuntimeConfig, get_zone, in_window, parse_hhmm, seconds_since, seconds_until,
)
from .solar import SolarFeed
from .storage import Storage
from .tuya import TuyaSwitch

log = logging.getLogger(__name__)


@dataclass
class Decision:
    """What the relay should be doing right now, and why.

    ``target`` of ``None`` means "hold whatever it is doing". ``immediate``
    skips the sustain period, for schedule edges that are not noisy.
    """

    target: bool | None
    reason: str
    #: Which schedule produced it: solar | offpeak | idle.
    mode: str = "idle"
    immediate: bool = False

    @property
    def source(self) -> str:
        """The activity-log source for a move this decision causes."""
        return "offpeak" if self.mode == "offpeak" else "auto"


class DeviceController:
    def __init__(
        self,
        spec: DeviceSpec,
        switch: TuyaSwitch,
        feed: SolarFeed,
        storage: Storage,
        config: RuntimeConfig,
        settings: Settings,
    ) -> None:
        self.spec = spec
        self.switch = switch
        self.feed = feed
        self.storage = storage
        self.config = config
        self.settings = settings

        #: Why the last tick did or did not act, shown verbatim in the UI.
        self.reason: str = "Not evaluated yet."
        #: The schedule behind the last decision: solar | offpeak | idle.
        self.mode: str = "idle"
        self.last_evaluated_at: datetime | None = None
        self.override_until: datetime | None = None

        # A change is "pending" while it waits out the sustain period.
        self._pending_target: bool | None = None
        self._pending_since: datetime | None = None

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

        config.on_change(lambda _cfg: self.trigger())

    @property
    def id(self) -> str:
        return self.spec.id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None or not self.switch.available:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"device-{self.id}")
        log.info("Controller for %s started", self.id)

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
        """Re-evaluate now instead of waiting out the tick."""
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Tick failed for %s", self.id)

            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.config.get("tick_seconds")
                )
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def local_now(self, cfg: dict[str, Any] | None = None) -> datetime:
        zone = (cfg or self.config.all())["timezone"]
        return datetime.now(get_zone(zone))

    @staticmethod
    def window(cfg: dict[str, Any]) -> tuple[time, time]:
        return (
            parse_hhmm(cfg["window_start"], time(8, 30)),
            parse_hhmm(cfg["window_end"], time(16, 30)),
        )

    @staticmethod
    def offpeak_window(cfg: dict[str, Any]) -> tuple[time, time]:
        return (
            parse_hhmm(cfg["offpeak_start"], time(20, 30)),
            parse_hhmm(cfg["offpeak_end"], time(4, 30)),
        )

    def _windows(self, cfg: dict[str, Any]) -> dict[str, tuple[time, time]]:
        """The schedules that are switched on, keyed by mode."""
        out: dict[str, tuple[time, time]] = {}
        if cfg["offpeak_enabled"]:
            out["offpeak"] = self.offpeak_window(cfg)
        if cfg["solar_enabled"]:
            out["solar"] = self.window(cfg)
        return out

    def _last_closed(self, cfg: dict[str, Any], now_local: datetime) -> str | None:
        """Which enabled window ended most recently (``None`` if none can end)."""
        best: tuple[float, str] | None = None
        for mode, (start, end) in self._windows(cfg).items():
            if start == end:  # "always" never closes
                continue
            since = seconds_since(now_local, end)
            if best is None or since < best[0]:
                best = (since, mode)
        return best[1] if best else None

    def next_change(self, cfg: dict[str, Any], now_local: datetime) -> dict[str, Any] | None:
        """The next time a schedule opens or closes, for the UI countdown."""
        best: dict[str, Any] | None = None
        for mode, (start, end) in self._windows(cfg).items():
            if start == end:
                continue
            for edge, at in (("start", start), ("end", end)):
                seconds = seconds_until(now_local, at)
                if best is None or seconds < best["in_s"]:
                    best = {
                        "mode": mode, "edge": edge, "in_s": int(seconds),
                        "at": f"{at.hour:02d}:{at.minute:02d}",
                    }
        return best

    # ------------------------------------------------------------------
    # The decision
    # ------------------------------------------------------------------
    def decide(self, cfg: dict[str, Any]) -> Decision:
        """What the relay should be doing right now, and the reason to show."""
        now = self.local_now(cfg).time()
        solar_label = f"{cfg['window_start']}–{cfg['window_end']}"
        offpeak_label = f"{cfg['offpeak_start']}–{cfg['offpeak_end']}"

        if cfg["offpeak_enabled"] and in_window(now, *self.offpeak_window(cfg)):
            return Decision(
                True, f"Off-peak tariff ({offpeak_label}) — charging at the cheap rate.",
                mode="offpeak", immediate=True,
            )

        if cfg["solar_enabled"] and in_window(now, *self.window(cfg)):
            target, reason = self._solar(cfg)
            return Decision(target, reason, mode="solar")

        if not cfg["solar_enabled"] and not cfg["offpeak_enabled"]:
            return Decision(
                None, "Solar and off-peak modes are both off, so there is nothing to follow."
            )

        if self._last_closed(cfg, self.local_now(cfg)) == "offpeak":
            return Decision(
                False, f"Off-peak ({offpeak_label}) has ended.",
                mode="offpeak", immediate=True,
            )
        if not cfg["solar_enabled"]:
            return Decision(None, f"Waiting for off-peak at {cfg['offpeak_start']}.")
        if cfg["off_outside_window"]:
            return Decision(False, f"Outside the {solar_label} solar window.", mode="solar")
        return Decision(None, (
            f"Outside the {solar_label} solar window; holding, because switching "
            "off at the end of the window is disabled."
        ))

    def _solar(self, cfg: dict[str, Any]) -> tuple[bool | None, str]:
        """Hysteresis on solar production, inside the solar window."""
        pv, measured_at = self.feed.pv_power()
        if pv is None or measured_at is None:
            if self.feed.error:
                return False, f"{self.feed.error} — holding the device off."
            return False, "No inverter reading yet, so the device is held off."

        age = (datetime.now(timezone.utc) - measured_at).total_seconds()
        if age > cfg["stale_after_s"]:
            return False, (
                f"Last inverter reading is {int(age // 60)} min old — too stale to "
                "act on."
            )

        if pv >= cfg["on_threshold_w"]:
            return True, (
                f"Solar {pv / 1000:.2f} kW is at or above the "
                f"{cfg['on_threshold_w'] / 1000:.2f} kW switch-on threshold."
            )
        if pv <= cfg["off_threshold_w"]:
            return False, (
                f"Solar {pv / 1000:.2f} kW is at or below the "
                f"{cfg['off_threshold_w'] / 1000:.2f} kW switch-off threshold."
            )
        return None, f"Solar {pv / 1000:.2f} kW is between the thresholds; holding."

    # ------------------------------------------------------------------
    async def tick(self) -> None:
        cfg = self.config.all()
        now = datetime.now(timezone.utc)
        self.last_evaluated_at = now

        state = await self.switch.refresh()

        if not cfg["auto_enabled"]:
            self._clear_pending()
            self.mode = "idle"
            self.reason = "Automation is off. The device only moves when you move it."
            return

        if self.override_until and now < self.override_until:
            self._clear_pending()
            minutes = max(1, round((self.override_until - now).total_seconds() / 60))
            self.reason = (
                f"Manual override active for another {minutes} min; automation is "
                "standing down."
            )
            return
        if self.override_until:
            self.override_until = None

        if not state.online:
            self._clear_pending()
            self.reason = f"Switch is unreachable: {state.error}"
            return

        decision = self.decide(cfg)
        desired, reason = decision.target, decision.reason
        self.mode = decision.mode
        self.reason = reason

        if desired is None or desired == state.on:
            self._clear_pending()
            return

        if decision.immediate:
            self._clear_pending()
            await self.apply(desired, source=decision.source, reason=reason)
            return

        # A change is warranted; hold it for the sustain period first.
        if self._pending_target != desired:
            self._pending_target = desired
            self._pending_since = now

        waited = (now - (self._pending_since or now)).total_seconds()
        remaining = cfg["sustain_s"] - waited
        if remaining > 0:
            self.reason = (
                f"{reason} Switching {'on' if desired else 'off'} in {int(remaining)}s."
            )
            return

        self._clear_pending()
        await self.apply(desired, source=decision.source, reason=reason)

    def _clear_pending(self) -> None:
        self._pending_target = None
        self._pending_since = None

    # ------------------------------------------------------------------
    async def apply(self, on: bool, *, source: str, reason: str) -> dict[str, Any]:
        """Drive the relay and record it. Used by both automation and the UI."""
        pv, _ = self.feed.pv_power()
        state = await self.switch.set(on)
        ok = state.online and state.error is None

        self.storage.add_event(
            device_id=self.id,
            action="on" if on else "off",
            source=source,
            reason=reason if ok else f"{reason} (failed: {state.error})",
            pv_power=pv,
            ok=ok,
        )
        log.info(
            "%s %s (%s): %s%s",
            self.id, "ON" if on else "OFF", source, reason, "" if ok else " — FAILED",
        )
        return {"ok": ok, "state": state.to_public()}

    async def manual(self, on: bool) -> dict[str, Any]:
        """A press of the On/Off buttons, which also arms the override."""
        minutes = self.config.get("manual_override_minutes")
        self._clear_pending()
        self.override_until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes > 0 else None
        )
        result = await self.apply(on, source="manual", reason="Switched by hand.")
        verb = "on" if on else "off"
        self.reason = (
            f"Switched {verb} by hand; automation stands down for {minutes} min."
            if minutes > 0 and self.config.get("auto_enabled")
            else f"Switched {verb} by hand."
        )
        return result

    def clear_override(self) -> None:
        self.override_until = None
        self._clear_pending()
        self.trigger()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything the UI renders for this device, in one payload."""
        cfg = self.config.all()
        now = datetime.now(timezone.utc)
        now_local = self.local_now(cfg)
        start, end = self.window(cfg)
        off_start, off_end = self.offpeak_window(cfg)
        pv, measured_at = self.feed.pv_power()

        override_seconds = 0
        if self.override_until and self.override_until > now:
            override_seconds = int((self.override_until - now).total_seconds())

        pending = None
        if self._pending_target is not None and self._pending_since is not None:
            waited = (now - self._pending_since).total_seconds()
            pending = {
                "target": self._pending_target,
                "remaining_s": max(0, int(cfg["sustain_s"] - waited)),
            }

        return {
            "id": self.id,
            "name": self.spec.name,
            "device": self.switch.describe(),
            "switch": self.switch.state.to_public(),
            "config": cfg,
            "running": self._task is not None and not self._task.done(),
            "reason": self.reason,
            "last_evaluated_at": (
                self.last_evaluated_at.isoformat() if self.last_evaluated_at else None
            ),
            "mode": self.mode if cfg["auto_enabled"] else "idle",
            "in_window": in_window(now_local.time(), start, end),
            "in_offpeak": in_window(now_local.time(), off_start, off_end),
            "next_change": self.next_change(cfg, now_local) if cfg["auto_enabled"] else None,
            "local_time": now_local.strftime("%H:%M"),
            "local_minutes": now_local.hour * 60 + now_local.minute,
            "timezone": cfg["timezone"],
            "pv_power": pv,
            "pv_measured_at": measured_at.isoformat() if measured_at else None,
            "override_seconds": override_seconds,
            "pending": pending,
        }
