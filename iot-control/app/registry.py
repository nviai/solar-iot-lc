"""The device inventory: which switches exist and how to reach them.

Devices live in a JSON file (``devices.json`` by default) rather than in
``.env`` because there can be several, and each carries a Tuya local key that
must stay out of version control. A missing or broken file is reported, not
fatal: the app starts with no devices and the UI explains how to add some.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)


class TuyaSpec(BaseModel):
    """LAN credentials, from ``python -m tinytuya wizard``."""

    device_id: str = Field(min_length=1)
    ip: str = Field(min_length=1)
    local_key: str = Field(min_length=1, repr=False)
    #: 3.3 and 3.4 are the common ones; check "ver" in snapshot.json.
    version: float = 3.4
    #: DPS index of the relay; 1 is channel 1 on a single-gang switch.
    dp: int = Field(1, ge=1)


class DeviceSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    name: str = Field(min_length=1, max_length=80)
    #: ``demo_switch`` is a simulated relay for trying the UI without hardware.
    kind: Literal["tuya_switch", "demo_switch"] = "tuya_switch"
    description: str = ""
    tuya: TuyaSpec | None = None

    @model_validator(mode="after")
    def _needs_tuya(self) -> "DeviceSpec":
        if self.kind == "tuya_switch" and self.tuya is None:
            raise ValueError("a tuya_switch needs a 'tuya' block")
        return self

    def public(self) -> dict:
        """Everything about the device except its secret."""
        tuya = self.tuya
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "device_id": tuya.device_id if tuya else "demo",
            "ip": tuya.ip if tuya else "simulated",
            "protocol": tuya.version if tuya else "—",
            "dp": tuya.dp if tuya else 1,
        }


class Inventory(BaseModel):
    devices: list[DeviceSpec] = []

    @field_validator("devices")
    @classmethod
    def _unique_ids(cls, devices: list[DeviceSpec]) -> list[DeviceSpec]:
        seen: set[str] = set()
        for device in devices:
            if device.id in seen:
                raise ValueError(f"Duplicate device id: {device.id}")
            seen.add(device.id)
        return devices


def load_devices(path: Path) -> tuple[list[DeviceSpec], str | None]:
    """Read the inventory. Returns the devices and, on failure, why."""
    if not path.exists():
        return [], f"{path.name} not found. Copy devices.example.json to {path.name} and fill it in."
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"Could not read {path.name}: {exc}"

    # Accept a bare list as well as {"devices": [...]}.
    if isinstance(raw, list):
        raw = {"devices": raw}
    try:
        inventory = Inventory.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return [], f"{path.name} is invalid at {where}: {first['msg']}"

    log.info("Loaded %d device(s) from %s", len(inventory.devices), path)
    return inventory.devices, None
