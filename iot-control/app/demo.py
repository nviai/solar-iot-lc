"""A simulated relay, for trying the UI and automation without hardware.

Declared in ``devices.json`` with ``"kind": "demo_switch"`` and no ``tuya``
block. It answers instantly, remembers its position in memory and is always
online, so every page renders the way it would against a real switch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .registry import DeviceSpec
from .tuya import SwitchState


class DemoSwitch:
    """Same surface as :class:`~app.tuya.TuyaSwitch`, with no network."""

    def __init__(self, spec: DeviceSpec) -> None:
        self.spec = spec
        self.dp = "1"
        self.state = SwitchState()
        self._on = False

    available = True
    unavailable_reason: str | None = None

    def describe(self) -> dict[str, Any]:
        return {**self.spec.public(), "available": True, "reason": None}

    def _report(self) -> SwitchState:
        self.state = SwitchState(
            on=self._on,
            online=True,
            checked_at=datetime.now(timezone.utc),
            dps={self.dp: self._on},
        )
        return self.state

    async def refresh(self) -> SwitchState:
        return self._report()

    async def set(self, on: bool) -> SwitchState:
        self._on = on
        return self._report()
