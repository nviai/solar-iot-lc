"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Required: the developer-portal application credentials -------------
    solax_client_id: str = ""
    solax_client_secret: str = ""

    #: Regional gateway. The wrong region answers "10400 Bad client credentials".
    openapi_base: str = "https://openapi-eu.solaxcloud.com"

    # --- Optional -----------------------------------------------------------
    #: Run against synthetic data; needs no credentials.
    demo_mode: bool = False

    #: Devices are discovered automatically; set this only to restrict polling
    #: to specific dongle registration numbers (comma separated).
    devices: str = ""

    poll_interval_seconds: int = 60
    request_timeout_seconds: float = 15.0

    #: Adds daily charge/discharge totals at one extra request per cycle.
    fetch_plant_totals: bool = True

    retention_days: int = 90
    energy_price: float = 0.0
    currency: str = "USD"

    #: Outbound webhook for alerts (Slack, Discord, or any JSON endpoint).
    alert_webhook_url: str = ""

    #: Shared secret checked on inbound data-subscription callbacks.
    callback_secret: str = ""

    #: Origins allowed to call the public API from a browser (comma separated,
    #: or "*"). Server-to-server consumers such as iot-control need nothing.
    cors_origins: str = ""

    database_path: Path = BASE_DIR / "data" / "solax.db"

    #: IANA zone the quiet-hours window is read in. Containers run on UTC, so
    #: leaving this at the host default would shift the schedule.
    timezone: str = "Asia/Ho_Chi_Minh"

    @property
    def device_filter(self) -> list[str]:
        """Registration numbers to poll, empty meaning "everything found"."""
        return [d.strip() for d in self.devices.split(",") if d.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def configured(self) -> bool:
        return self.demo_mode or bool(self.solax_client_id and self.solax_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
