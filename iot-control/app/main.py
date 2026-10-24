"""FastAPI entrypoint for IoT Control."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .deps import controllers, feed, inventory_error, storage
from .routers import devices, pages, system

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("iot_control")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if inventory_error:
        log.warning("No devices loaded: %s", inventory_error)

    removed = storage.prune(settings.retention_days)
    if removed:
        log.info("Pruned %d old device events", removed)

    feed.start()
    for controller in controllers.values():
        if controller.switch.available:
            controller.start()
        else:
            log.info("%s is not controllable: %s",
                     controller.id, controller.switch.unavailable_reason)

    yield

    for controller in controllers.values():
        await controller.stop()
    await feed.stop()


app = FastAPI(
    title="IoT Control",
    summary="LAN control and solar-following automation for smart switches.",
    description=(
        "Drives Tuya relays over the local network and switches them on surplus "
        "solar. Power figures come from the Solar Monitor's public API "
        "(GET /api/v1/power); this service never calls SolaX itself."
    ),
    version=__version__,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "devices", "description": "Switch state, manual control and automation."},
        {"name": "system", "description": "Service health and the solar feed."},
    ],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(pages.router)
app.include_router(devices.router)
app.include_router(system.router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {
        "status": "ok",
        "version": __version__,
        "devices": len(controllers),
        "solar_feed": feed.ok,
    }
