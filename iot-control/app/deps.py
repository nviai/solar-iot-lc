"""Shared singletons + FastAPI dependencies.

Built once at import time and wired into the app during lifespan startup, so
routers can pull them without threading arguments through every signature.
"""

from __future__ import annotations

from fastapi import HTTPException

from .automation import DeviceController
from .config import settings
from .demo import DemoSwitch
from .registry import DeviceSpec, load_devices
from .runtime import AUTOMATION_FIELDS, RuntimeConfig, check_thresholds
from .solar import SolarFeed
from .storage import Storage
from .tuya import TuyaSwitch

storage = Storage(settings.database_path)
feed = SolarFeed(settings)

device_specs, inventory_error = load_devices(settings.devices_file)

#: Values from ``.env`` that stand in until a device's settings are saved.
_seed = {
    "timezone": settings.timezone,
    "offpeak_start": settings.offpeak_start,
    "offpeak_end": settings.offpeak_end,
}


def _driver(spec: DeviceSpec) -> TuyaSwitch | DemoSwitch:
    return DemoSwitch(spec) if spec.kind == "demo_switch" else TuyaSwitch(spec)


#: One controller per configured device, in inventory order.
controllers: dict[str, DeviceController] = {
    spec.id: DeviceController(
        spec,
        _driver(spec),
        feed,
        storage,
        RuntimeConfig(
            storage, f"device.{spec.id}", AUTOMATION_FIELDS,
            seed=_seed, invariants=check_thresholds,
        ),
        settings,
    )
    for spec in device_specs
}


def get_storage() -> Storage:
    return storage


def get_feed() -> SolarFeed:
    return feed


def get_controllers() -> dict[str, DeviceController]:
    return controllers


def get_controller(device_id: str) -> DeviceController:
    """Path dependency: the controller for ``{device_id}``, or 404."""
    controller = controllers.get(device_id)
    if controller is None:
        raise HTTPException(404, f"Unknown device {device_id!r}")
    return controller
