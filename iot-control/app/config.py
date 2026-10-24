"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Solar Monitor (the only source of power figures) -------------------
    #: Base URL this app calls server-side, e.g. http://solar-monitor:8000.
    solar_api_url: str = "http://localhost:8000"
    #: Address of the monitor's UI as a browser should reach it. Defaults to
    #: ``solar_api_url``, which is wrong when that is a container hostname.
    solar_public_url: str = ""
    #: Seconds between reads of /api/v1/power. The monitor answers from memory,
    #: so this costs no SolaX quota however short it is.
    solar_poll_seconds: float = 10.0
    solar_timeout_seconds: float = 5.0

    # --- Devices and storage ------------------------------------------------
    #: JSON inventory of the switches (ids, IPs, Tuya local keys).
    devices_file: Path = BASE_DIR / "devices.json"
    database_path: Path = BASE_DIR / "data" / "iot.db"
    retention_days: int = 90

    #: IANA zone the automation windows are read in. Containers run on UTC, so
    #: leaving this at the host default would shift every schedule. This and
    #: the off-peak hours below are only starting values: each device's own
    #: setting, edited in the UI, takes over once saved.
    timezone: str = "Asia/Ho_Chi_Minh"
    offpeak_start: str = "20:30"
    offpeak_end: str = "04:30"

    @field_validator("devices_file", "database_path")
    @classmethod
    def _relative_to_app(cls, value: Path) -> Path:
        """Relative paths are read against the app folder, not the CWD."""
        return value if value.is_absolute() else BASE_DIR / value

    @property
    def solar_api_base(self) -> str:
        return self.solar_api_url.rstrip("/")

    @property
    def solar_link(self) -> str:
        return (self.solar_public_url or self.solar_api_url).rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
