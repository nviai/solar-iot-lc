"""Response and request models for the public JSON API.

These are the contract other applications integrate against (iot-control
reads ``PowerSnapshot``), so field names are stable and every power figure is
in watts, every energy figure in kWh.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from .models import METRIC_META


def _known_metric(value: str) -> str:
    if value not in METRIC_META:
        raise ValueError(f"Unknown metric: {value}")
    return value


Operator = Literal["gt", "gte", "lt", "lte", "eq", "neq"]
Severity = Literal["info", "warning", "critical"]
MetricKey = Annotated[str, AfterValidator(_known_metric)]


# ----------------------------------------------------------------------
# Power snapshot
# ----------------------------------------------------------------------
class PowerFigures(BaseModel):
    """Instantaneous power flows. Watts unless stated otherwise."""

    pv_power: float = Field(description="Solar production across all MPPT strings (W).")
    load_power: float = Field(description="Site consumption (W).")
    grid_power: float = Field(description="Grid exchange: > 0 exporting, < 0 importing (W).")
    grid_import: float = Field(description="Power drawn from the grid, never negative (W).")
    grid_export: float = Field(description="Power fed into the grid, never negative (W).")
    battery_power: float = Field(description="Battery: > 0 charging, < 0 discharging (W).")
    battery_charge: float = Field(description="Charging power, never negative (W).")
    battery_discharge: float = Field(description="Discharging power, never negative (W).")
    soc: float | None = Field(None, description="Battery state of charge (%).")
    yield_today: float | None = Field(None, description="Energy produced today (kWh).")


class DevicePower(PowerFigures):
    """One inverter site's latest figures."""

    register_no: str = Field(description="Dongle registration number.")
    measured_at: datetime = Field(description="When this reading was taken (UTC).")
    age_seconds: float = Field(description="Seconds since `measured_at`.")
    status: str | None = Field(None, description="Raw SolaX device status code.")
    status_label: str | None = None
    error: str | None = Field(None, description="Last poll error for this device, if any.")


class PowerSnapshot(BaseModel):
    """Aggregated live power across every device in the account."""

    measured_at: datetime | None = Field(
        description="Timestamp of the oldest contributing reading: the totals "
                    "are only as fresh as this."
    )
    age_seconds: float | None = Field(description="Seconds since `measured_at`.")
    poll_interval_seconds: int = Field(description="Current polling interval.")
    paused: bool = Field(description="True while quiet hours suspend polling.")
    totals: PowerFigures | None = Field(
        description="Sum across devices (state of charge is averaged). Null "
                    "until the first reading arrives."
    )
    devices: list[DevicePower]


# ----------------------------------------------------------------------
# Alert rules
# ----------------------------------------------------------------------
class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric: MetricKey
    op: Operator
    threshold: float
    duration_s: int = Field(0, ge=0, le=86400)
    cooldown_s: int = Field(900, ge=0, le=7 * 86400)
    severity: Severity = "warning"
    register_no: str = "*"
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    metric: MetricKey | None = None
    op: Operator | None = None
    threshold: float | None = None
    duration_s: int | None = Field(None, ge=0, le=86400)
    cooldown_s: int | None = Field(None, ge=0, le=7 * 86400)
    severity: Severity | None = None
    register_no: str | None = None
    enabled: bool | None = None


class AckRequest(BaseModel):
    id: int | None = Field(None, description="Event to acknowledge; omit for all.")
