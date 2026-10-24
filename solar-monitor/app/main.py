"""FastAPI entrypoint for the solar monitor."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .alerts import seed_default_rules
from .config import settings
from .deps import client, poller, storage
from .routers import alerts, callbacks, pages, schedule, telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("solar_monitor")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_rules(storage)
    await client.start()

    if settings.configured:
        poller.start()
    else:
        log.warning(
            "SOLAX_CLIENT_ID / SOLAX_CLIENT_SECRET are not set, so the poller is "
            "idle. Set DEMO_MODE=true to explore the UI with synthetic data."
        )

    yield

    await poller.stop()
    await client.close()


app = FastAPI(
    title="Solar Monitor",
    summary="Live and historical power figures for a SolaX solar installation.",
    description=(
        "Polls the SolaX Developer Platform and republishes the readings as a "
        "small, stable JSON API. Integrations should start with "
        "`GET /api/v1/power`, which serves the latest aggregated figures from "
        "memory and never spends SolaX API quota."
    ),
    version=__version__,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "telemetry", "description": "Live power, device readings and history."},
        {"name": "alerts", "description": "Threshold rules and the events they produce."},
        {"name": "schedule", "description": "How often the SolaX API is polled."},
        {"name": "callback", "description": "Inbound SolaX data-subscription pushes."},
    ],
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(pages.router)
app.include_router(telemetry.router)
app.include_router(alerts.router)
app.include_router(schedule.router)
app.include_router(callbacks.router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok", "version": __version__, "configured": settings.configured}
