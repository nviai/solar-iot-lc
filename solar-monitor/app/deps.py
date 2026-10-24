"""Shared singletons + FastAPI dependencies.

Built once at import time and wired into the app during lifespan startup, so
routers can pull them without threading arguments through every signature.
"""

from __future__ import annotations

from .alerts import AlertEngine
from .client import SolaxClient
from .config import settings
from .poller import Poller
from .runtime import SCHEDULE_FIELDS, RuntimeConfig
from .storage import Storage

storage = Storage(settings.database_path)
client = SolaxClient(settings)
alert_engine = AlertEngine(storage, settings)

# UI-editable polling schedule. It inherits the .env interval until someone
# saves a value from the Settings page.
schedule_config = RuntimeConfig(
    storage,
    "schedule",
    SCHEDULE_FIELDS,
    seed={"poll_interval_seconds": settings.poll_interval_seconds},
)

poller = Poller(client, storage, alert_engine, settings, schedule_config)


def get_storage() -> Storage:
    return storage


def get_poller() -> Poller:
    return poller


def get_client() -> SolaxClient:
    return client


def get_alert_engine() -> AlertEngine:
    return alert_engine


def get_schedule_config() -> RuntimeConfig:
    return schedule_config
