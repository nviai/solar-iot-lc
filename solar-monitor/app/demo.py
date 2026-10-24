"""Synthetic telemetry, so the whole UI can be exercised without credentials.

Shapes a plausible day: a solar bell curve peaking around midday, a load with
morning and evening humps, and a battery that charges on surplus and covers the
evening.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from .models import Reading


def demo_reading(register_no: str, at: datetime | None = None) -> Reading:
    now = at or datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute

    sun = math.exp(-((minutes - 750) ** 2) / (2 * 190**2))
    pv = max(0.0, 12000 * sun + random.uniform(-200, 200))

    load = (
        700
        + 1600 * math.exp(-((minutes - 420) ** 2) / (2 * 70**2))
        + 2400 * math.exp(-((minutes - 1140) ** 2) / (2 * 100**2))
        + random.uniform(-90, 90)
    )

    soc = 30 + 55 * sun
    surplus = pv - load
    battery = (
        max(-6000.0, min(6000.0, surplus * 0.55))
        if surplus > 0
        else max(-6000.0, surplus * 0.6)
    )
    grid = surplus - battery
    ac_power = pv - max(battery, 0.0) + max(-battery, 0.0)

    return Reading.parse(
        {
            "registerNo": register_no,
            "deviceSn": "DEMO0000000001",
            "plantLocalTime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "acPower1": round(ac_power, 1),
            "gridPower": round(grid, 1),
            "mpptMap": {
                "MPPT1Power": round(pv * 0.36, 1),
                "MPPT2Power": round(pv * 0.34, 1),
                "MPPT3Power": round(pv * 0.30, 1),
            },
            "MPPTTotalInputPower": round(pv, 1),
            "dailyYield": round(48.0 * sun + 3.2, 1),
            "totalYield": 5086.9,
            "totalExportEnergy": 0.5,
            "totalImportEnergy": 868.8,
            "todayExportEnergy": 0.0,
            "todayImportEnergy": round(8.0 * (1 - sun), 1),
            "acVoltage1": round(228.0 + random.uniform(-3, 3), 1),
            "acCurrent1": round(ac_power / 228, 1),
            "acFrequency1": round(50.0 + random.uniform(-0.1, 0.1), 2),
            "inverterTemperature": round(32 + 22 * sun, 1),
            "deviceStatus": 102,
        },
        battery={
            "batterySOC": round(soc, 1),
            "chargeDischargePower": round(battery, 1),
            "batteryVoltage": 53.1,
            "batteryCurrent": round(battery / 53.1, 1),
            "batteryTemperature": 37.0,
            "batterySOH": 100,
            "batteryCycleTimes": 92,
            "batteryRemainings": round(32.0 * soc / 100, 1),
            "totalDeviceCharge": 2657.1,
            "totalDeviceDischarge": 2534.1,
        },
        plant={
            "plantId": "demo-plant",
            "dailyCharged": round(20.0 * sun, 1),
            "dailyDischarged": round(8.0 * (1 - sun), 1),
            "totalCharged": 2675.3,
            "totalDischarged": 2545.0,
        },
        register_no=register_no,
        rated_power=12.0,
        device_model=103,
    )
