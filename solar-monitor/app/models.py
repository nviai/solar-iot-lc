"""Normalised representation of one SolaX telemetry sample.

A site is reported as several devices — the inverter carries PV and grid
figures, the battery carries state of charge — so a :class:`Reading` is
assembled from more than one API response and exposes the derived power flows
the dashboard actually plots.

Sign conventions, verified against real history:

* ``gridPower``            > 0 exports to the grid, < 0 imports from it
* ``chargeDischargePower`` > 0 charges the battery, < 0 discharges it
* ``acPower1..3``          inverter AC output, already net of battery charging

Site consumption is not reported directly, so it is derived as
``ac_power - grid_power``: whatever the inverter puts out that does not leave
through the meter is being used on site.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

# Device status codes -> (label, severity) where severity is one of
# "ok" | "warn" | "fault" | "idle".
DEVICE_STATUS: dict[str, tuple[str, str]] = {
    "100": ("Waiting", "idle"),
    "101": ("Self-test", "idle"),
    "102": ("Normal", "ok"),
    "103": ("Recoverable fault", "warn"),
    "104": ("Permanent fault", "fault"),
    "105": ("Firmware upgrade", "idle"),
    "106": ("EPS check", "idle"),
    "107": ("Off-grid (EPS)", "warn"),
    "108": ("Self-test mode", "idle"),
    "109": ("Sleep", "idle"),
    "110": ("Standby", "idle"),
    "111": ("PV waking battery", "idle"),
    "112": ("Generator check", "idle"),
    "113": ("Generator mode", "warn"),
    "114": ("Fast shutdown standby", "idle"),
    "130": ("VPP mode", "ok"),
    "131": ("TOU - self use", "ok"),
    "132": ("TOU - charging", "ok"),
    "133": ("TOU - discharging", "ok"),
}


def _f(value: Any) -> float | None:
    """Coerce an API field to float, mapping absent/garbage values to None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(*values: float | None) -> float:
    return sum(v for v in values if v is not None)


class Reading(BaseModel):
    """One telemetry sample for one site, with derived power flows."""

    # Identity + timing
    register_no: str
    inverter_sn: str | None = None
    plant_id: str | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    upload_time: str | None = None

    # Power (W)
    ac_power: float | None = None
    grid_power: float | None = None
    grid_power_m2: float | None = None
    battery_power: float | None = None
    pv1: float | None = None
    pv2: float | None = None
    pv3: float | None = None
    pv4: float | None = None
    eps_power: float | None = None

    # Energy counters (kWh)
    yield_today: float | None = None
    yield_total: float | None = None
    export_total: float | None = None
    import_total: float | None = None
    export_today: float | None = None
    import_today: float | None = None
    charged_total: float | None = None
    discharged_total: float | None = None
    charged_today: float | None = None
    discharged_today: float | None = None

    # Battery
    soc: float | None = None
    battery_voltage: float | None = None
    battery_current: float | None = None
    battery_temp: float | None = None
    battery_soh: float | None = None
    battery_cycles: float | None = None
    battery_remaining_kwh: float | None = None

    # AC side
    vac1: float | None = None
    vac2: float | None = None
    vac3: float | None = None
    iac1: float | None = None
    iac2: float | None = None
    iac3: float | None = None
    frequency: float | None = None

    # Hardware
    temperature: float | None = None
    rated_power: float | None = None
    model: str | None = None
    status: str | None = None

    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    # ------------------------------------------------------------------
    # Derived flows
    # ------------------------------------------------------------------
    @property
    def pv_power(self) -> float:
        """Total DC input across all MPPT strings (W)."""
        return _sum(self.pv1, self.pv2, self.pv3, self.pv4)

    @property
    def grid_export(self) -> float:
        return max(self.grid_power or 0.0, 0.0)

    @property
    def grid_import(self) -> float:
        return max(-(self.grid_power or 0.0), 0.0)

    @property
    def load_power(self) -> float:
        """Site consumption (W).

        Grid-connected, this is the inverter's AC output minus whatever leaves
        through the meter.

        During an outage that measurement disappears. The inverter moves the
        house onto the backup port, ``acPower1..3`` drops to zero, and on this
        hardware ``EPSL*ActivePower`` reads zero as well — so the whole AC side
        says nothing is being consumed while the battery is plainly discharging
        several kW into the house. What is still visible is the DC side, so
        fall back to the energy balance there: everything PV and the battery
        deliver, less anything exported or stored, is being used on site.

        That fallback counts inverter conversion losses as consumption, so it
        reads a few percent high (about 5% against the AC-side figure on the
        readings either side of an outage). It is a far better answer than
        zero.
        """
        grid = self.grid_power or 0.0
        ac_side = max((self.ac_power or 0.0) - grid, 0.0)

        # Inverters that do report the backup port put its output alongside
        # acPower1..3 rather than inside it, so the two add up.
        eps = self.eps_power or 0.0
        if eps > 0:
            return ac_side + eps

        if ac_side <= 0 and (self.pv_power > 0 or self.battery_discharge > 0):
            return max(self.pv_power - (self.battery_power or 0.0) - grid, 0.0)

        return ac_side

    @property
    def battery_charge(self) -> float:
        return max(self.battery_power or 0.0, 0.0)

    @property
    def battery_discharge(self) -> float:
        return max(-(self.battery_power or 0.0), 0.0)

    @property
    def self_sufficiency(self) -> float | None:
        """Share of consumption covered without importing (0-100%)."""
        load = self.load_power
        if load <= 0:
            return None
        return min(100.0, (load - self.grid_import) / load * 100.0)

    @property
    def status_label(self) -> str:
        return DEVICE_STATUS.get(self.status or "", ("Unknown", "idle"))[0]

    @property
    def status_severity(self) -> str:
        return DEVICE_STATUS.get(self.status or "", ("Unknown", "idle"))[1]

    @property
    def model_label(self) -> str:
        return f"Model {self.model}" if self.model else "-"

    @property
    def has_battery(self) -> bool:
        return self.soc is not None or self.battery_power is not None

    # ------------------------------------------------------------------
    def metric(self, name: str) -> float | None:
        """Look up a metric by the identifier used in alert rules."""
        return METRIC_ACCESSORS.get(name, lambda _: None)(self)

    def to_public(self) -> dict[str, Any]:
        """Flat dict for JSON responses and chart payloads."""
        return {
            "register_no": self.register_no,
            "inverter_sn": self.inverter_sn,
            "plant_id": self.plant_id,
            "ts": self.ts.isoformat(),
            "upload_time": self.upload_time,
            "pv_power": round(self.pv_power, 1),
            "load_power": round(self.load_power, 1),
            "grid_power": round(self.grid_power or 0.0, 1),
            "grid_import": round(self.grid_import, 1),
            "grid_export": round(self.grid_export, 1),
            "battery_power": round(self.battery_power or 0.0, 1),
            "battery_charge": round(self.battery_charge, 1),
            "battery_discharge": round(self.battery_discharge, 1),
            "ac_power": self.ac_power,
            "eps_power": self.eps_power,
            "soc": self.soc,
            "battery_voltage": self.battery_voltage,
            "battery_current": self.battery_current,
            "battery_temp": self.battery_temp,
            "battery_soh": self.battery_soh,
            "battery_cycles": self.battery_cycles,
            "battery_remaining_kwh": self.battery_remaining_kwh,
            "yield_today": self.yield_today,
            "yield_total": self.yield_total,
            "import_today": self.import_today,
            "export_today": self.export_today,
            "import_total": self.import_total,
            "export_total": self.export_total,
            "charged_today": self.charged_today,
            "discharged_today": self.discharged_today,
            "charged_total": self.charged_total,
            "discharged_total": self.discharged_total,
            "pv_strings": [self.pv1, self.pv2, self.pv3, self.pv4],
            "vac": [self.vac1, self.vac2, self.vac3],
            "iac": [self.iac1, self.iac2, self.iac3],
            "frequency": self.frequency,
            "temperature": self.temperature,
            "rated_power": self.rated_power,
            "model": self.model_label,
            "status": self.status,
            "status_label": self.status_label,
            "status_severity": self.status_severity,
            "self_sufficiency": self.self_sufficiency,
            "has_battery": self.has_battery,
        }

    # ------------------------------------------------------------------
    @classmethod
    def parse(
        cls,
        inverter: dict[str, Any],
        *,
        battery: dict[str, Any] | None = None,
        plant: dict[str, Any] | None = None,
        register_no: str = "",
        rated_power: float | None = None,
        device_model: Any = None,
    ) -> "Reading":
        """Build a Reading from the inverter, battery and plant payloads."""
        inv = inverter or {}
        bat = battery or {}
        pl = plant or {}

        mppt = inv.get("mpptMap") or {}
        strings = [_f(mppt.get(f"MPPT{i}Power")) for i in range(1, 5)]

        # MPPTTotalInputPower is authoritative. When only the aggregate is
        # reported, park it on string 1 so `pv_power` (a sum) still sees it.
        pv_total = _f(inv.get("MPPTTotalInputPower"))
        if all(v is None for v in strings) and pv_total is not None:
            strings[0] = pv_total

        ac_parts = [_f(inv.get(f"acPower{i}")) for i in range(1, 4)]
        ac_power = _sum(*ac_parts) if any(v is not None for v in ac_parts) else None
        if ac_power is None:
            ac_power = _f(inv.get("totalActivePower"))

        eps_parts = [_f(inv.get(f"EPSL{i}ActivePower")) for i in range(1, 4)]
        eps_power = _sum(*eps_parts) if any(v is not None for v in eps_parts) else None

        status = inv.get("deviceStatus")

        return cls(
            register_no=inv.get("registerNo") or register_no,
            inverter_sn=inv.get("deviceSn"),
            plant_id=str(pl.get("plantId")) if pl.get("plantId") else None,
            upload_time=inv.get("plantLocalTime"),
            # Power
            ac_power=ac_power,
            grid_power=_f(inv.get("gridPower")),
            grid_power_m2=_f(inv.get("gridPowerM2")),
            battery_power=_f(bat.get("chargeDischargePower")),
            pv1=strings[0],
            pv2=strings[1],
            pv3=strings[2],
            pv4=strings[3],
            eps_power=eps_power,
            # Energy
            yield_today=_f(inv.get("dailyYield")),
            yield_total=_f(inv.get("totalYield")),
            export_total=_f(inv.get("totalExportEnergy")),
            import_total=_f(inv.get("totalImportEnergy")),
            export_today=_f(inv.get("todayExportEnergy")),
            import_today=_f(inv.get("todayImportEnergy")),
            charged_total=_f(pl.get("totalCharged")) or _f(bat.get("totalDeviceCharge")),
            discharged_total=_f(pl.get("totalDischarged")) or _f(bat.get("totalDeviceDischarge")),
            charged_today=_f(pl.get("dailyCharged")),
            discharged_today=_f(pl.get("dailyDischarged")),
            # Battery
            soc=_f(bat.get("batterySOC")),
            battery_voltage=_f(bat.get("batteryVoltage")),
            battery_current=_f(bat.get("batteryCurrent")),
            battery_temp=_f(bat.get("batteryTemperature")),
            battery_soh=_f(bat.get("batterySOH")),
            battery_cycles=_f(bat.get("batteryCycleTimes")),
            battery_remaining_kwh=_f(bat.get("batteryRemainings")),
            # AC side
            vac1=_f(inv.get("acVoltage1")),
            vac2=_f(inv.get("acVoltage2")),
            vac3=_f(inv.get("acVoltage3")),
            iac1=_f(inv.get("acCurrent1")),
            iac2=_f(inv.get("acCurrent2")),
            iac3=_f(inv.get("acCurrent3")),
            frequency=_f(inv.get("acFrequency1")) or _f(inv.get("gridFrequency")),
            # Hardware
            temperature=_f(inv.get("inverterTemperature")),
            rated_power=rated_power if rated_power is not None else _f(inv.get("ratedPower")),
            model=str(device_model) if device_model is not None else None,
            status=str(status) if status is not None else None,
            raw={"inverter": inv, "battery": bat, "plant": pl},
        )


# Metrics an alert rule can watch. Keys are persisted in the rules table, so
# keep them stable.
METRIC_ACCESSORS: dict[str, Any] = {
    "pv_power": lambda r: r.pv_power,
    "load_power": lambda r: r.load_power,
    "grid_power": lambda r: r.grid_power,
    "grid_import": lambda r: r.grid_import,
    "grid_export": lambda r: r.grid_export,
    "battery_power": lambda r: r.battery_power,
    "battery_charge": lambda r: r.battery_charge,
    "battery_discharge": lambda r: r.battery_discharge,
    "soc": lambda r: r.soc,
    "ac_power": lambda r: r.ac_power,
    "yield_today": lambda r: r.yield_today,
    "temperature": lambda r: r.temperature,
    "battery_temp": lambda r: r.battery_temp,
    "vac1": lambda r: r.vac1,
    "frequency": lambda r: r.frequency,
    "self_sufficiency": lambda r: r.self_sufficiency,
}

METRIC_META: dict[str, dict[str, str]] = {
    "pv_power": {"label": "Solar production", "unit": "W"},
    "load_power": {"label": "Consumption", "unit": "W"},
    "grid_power": {"label": "Grid power (+export / -import)", "unit": "W"},
    "grid_import": {"label": "Grid import", "unit": "W"},
    "grid_export": {"label": "Grid export", "unit": "W"},
    "battery_power": {"label": "Battery power (+charge / -discharge)", "unit": "W"},
    "battery_charge": {"label": "Battery charging", "unit": "W"},
    "battery_discharge": {"label": "Battery discharging", "unit": "W"},
    "soc": {"label": "Battery state of charge", "unit": "%"},
    "ac_power": {"label": "Inverter AC output", "unit": "W"},
    "yield_today": {"label": "Yield today", "unit": "kWh"},
    "temperature": {"label": "Inverter temperature", "unit": "°C"},
    "battery_temp": {"label": "Battery temperature", "unit": "°C"},
    "vac1": {"label": "AC voltage L1", "unit": "V"},
    "frequency": {"label": "Grid frequency", "unit": "Hz"},
    "self_sufficiency": {"label": "Self-sufficiency", "unit": "%"},
}
