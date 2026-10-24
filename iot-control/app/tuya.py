"""Local-network control of a Tuya relay or smart plug.

``tinytuya`` speaks the LAN protocol directly — no cloud round trip, no Tuya
API quota — but it is a blocking, socket-based library, so every call here is
pushed onto a worker thread and serialised behind a lock: the protocol runs one
request at a time over a single socket, and two overlapping calls corrupt the
sequence numbers rather than merely queueing.

Failures are expected and routine (the switch drops off Wi-Fi, the router
reboots), so nothing in here raises on a connection problem; callers get a
:class:`SwitchState` with ``online=False`` and an error message to show.

One operational caveat: these switches accept a single local connection at a
time. Two instances of this app pointed at the same device — a dev server and
the container, say — or the vendor app polling it locally will fight over
the socket and both report it unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .registry import DeviceSpec

log = logging.getLogger(__name__)

try:  # keep the app importable when the optional dependency is absent
    import tinytuya
except ImportError:  # pragma: no cover - exercised only on broken installs
    tinytuya = None  # type: ignore[assignment]


@dataclass
class SwitchState:
    """What the relay last told us, and when."""

    on: bool | None = None
    online: bool = False
    error: str | None = None
    checked_at: datetime | None = None
    dps: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "on": self.on,
            "online": self.online,
            "error": self.error,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "dps": self.dps,
        }


class TuyaSwitch:
    """One Tuya outlet/relay, reachable on the LAN."""

    def __init__(self, spec: DeviceSpec) -> None:
        self.spec = spec
        self.dp = str(spec.tuya.dp)
        self.state = SwitchState()
        self._device: Any = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Can the switch be driven at all? Specs are validated on load, so
        only the optional dependency can be missing."""
        return tinytuya is not None

    @property
    def unavailable_reason(self) -> str | None:
        if tinytuya is None:
            return "The tinytuya package is not installed."
        return None

    def describe(self) -> dict[str, Any]:
        return {
            **self.spec.public(),
            "available": self.available,
            "reason": self.unavailable_reason,
        }

    # ------------------------------------------------------------------
    def _connect(self) -> Any:
        """Build the device object. Runs on a worker thread."""
        tuya = self.spec.tuya
        device = tinytuya.OutletDevice(
            dev_id=tuya.device_id,
            address=tuya.ip,
            local_key=tuya.local_key,
            version=tuya.version,
        )
        # Bound how long a dead switch can stall the loop. The persistent
        # socket matters for 3.4, which otherwise renegotiates a session key on
        # every single call.
        device.set_socketTimeout(5)
        device.set_socketRetryLimit(1)
        device.set_socketPersistent(True)
        return device

    def _drop(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:  # the socket is already broken; nothing to salvage
                pass
        self._device = None

    def _call(self, action: str, value: bool | None = None) -> dict[str, Any]:
        """Blocking body of every operation, retried once on a stale socket."""
        last_error: Exception | None = None

        for attempt in (1, 2):
            try:
                if self._device is None:
                    self._device = self._connect()

                if action == "status":
                    result = self._device.status()
                else:
                    result = self._device.set_value(self.dp, bool(value))
                    # set_value echoes the accepted dps; when it does not, ask.
                    if not isinstance(result, dict) or "dps" not in result:
                        result = self._device.status()

                if not isinstance(result, dict):
                    raise RuntimeError(f"Unexpected reply: {result!r}")
                if result.get("Error") or result.get("Err"):
                    raise RuntimeError(
                        str(result.get("Error") or f"error {result.get('Err')}")
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - reported, never raised on
                last_error = exc
                self._drop()
                if attempt == 1:
                    log.debug("Tuya %s on %s failed (%s), reconnecting", action, self.spec.id, exc)

        raise RuntimeError(str(last_error) if last_error else "unknown error")

    async def _run(self, action: str, value: bool | None = None) -> SwitchState:
        if not self.available:
            self.state = SwitchState(
                on=None,
                online=False,
                error=self.unavailable_reason,
                checked_at=datetime.now(timezone.utc),
            )
            return self.state

        async with self._lock:
            try:
                result = await asyncio.to_thread(self._call, action, value)
            except Exception as exc:  # noqa: BLE001 - surfaced through the state
                self.state = SwitchState(
                    on=self.state.on,
                    online=False,
                    error=str(exc),
                    checked_at=datetime.now(timezone.utc),
                    dps=self.state.dps,
                )
                return self.state

            dps = {str(k): v for k, v in (result.get("dps") or {}).items()}
            raw = dps.get(self.dp)
            self.state = SwitchState(
                on=bool(raw) if raw is not None else self.state.on,
                online=True,
                error=None,
                checked_at=datetime.now(timezone.utc),
                dps=dps,
            )
            return self.state

    # ------------------------------------------------------------------
    async def refresh(self) -> SwitchState:
        """Read the relay's current position."""
        return await self._run("status")

    async def set(self, on: bool) -> SwitchState:
        """Drive the relay, then report where it ended up."""
        return await self._run("set", on)
