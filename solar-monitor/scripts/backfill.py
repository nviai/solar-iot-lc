"""Load historical samples from SolaX into the local database.

Useful right after setup, so the charts have shape immediately instead of
filling in over the next few days.

    python scripts/backfill.py --days 7
    python scripts/backfill.py --days 3 --interval 5 --device XSUGLNFHAP

The API rejects wide windows, so the range is fetched in chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.client import DEVICE_TYPE_BATTERY, DEVICE_TYPE_INVERTER, SolaxClient  # noqa: E402
from app.config import settings  # noqa: E402
from app.errors import SolaxError  # noqa: E402
from app.models import Reading  # noqa: E402
from app.storage import Storage  # noqa: E402

CHUNK_HOURS = 6


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="how many days to fetch")
    parser.add_argument("--interval", type=int, default=15, help="sample spacing in minutes")
    parser.add_argument("--device", help="registration number (default: every device)")
    args = parser.parse_args()

    if settings.demo_mode:
        print("Backfill needs real credentials; it does nothing in demo mode.")
        return 1

    storage = Storage(settings.database_path)
    total = 0

    async with SolaxClient(settings) as client:
        try:
            devices = await client.discover()
        except SolaxError as exc:
            print(f"Could not list devices: {exc}")
            return 2

        targets = [d for d in devices.values() if not args.device or d.register_no == args.device]
        if not targets:
            print(f"No device '{args.device}'. Available: {', '.join(devices) or 'none'}")
            return 1

        now = datetime.now(timezone.utc)
        for device in targets:
            print(f"\n{device.register_no} ({device.inverter_sn})")
            inserted = 0
            cursor = now - timedelta(days=args.days)

            while cursor < now:
                chunk_end = min(cursor + timedelta(hours=CHUNK_HOURS), now)
                try:
                    inverter_rows = await client.history(
                        device.inverter_sn, DEVICE_TYPE_INVERTER,
                        cursor, chunk_end, args.interval,
                    )
                except SolaxError as exc:
                    print(f"  {cursor:%d %b %H:%M}  error: {exc}")
                    cursor = chunk_end
                    continue

                # Battery history is a separate device; index it by timestamp so
                # each inverter sample can be paired with its state of charge.
                battery_rows: dict[str, dict] = {}
                if device.battery_sn:
                    try:
                        for row in await client.history(
                            device.battery_sn, DEVICE_TYPE_BATTERY,
                            cursor, chunk_end, args.interval,
                        ):
                            if row.get("dataTime"):
                                battery_rows[row["dataTime"]] = row
                    except SolaxError:
                        pass

                for row in inverter_rows:
                    reading = Reading.parse(
                        row,
                        battery=battery_rows.get(row.get("dataTime")),
                        register_no=device.register_no,
                        rated_power=device.rated_power,
                        device_model=device.model,
                    )
                    stamp = row.get("dataTime")
                    if stamp:
                        try:
                            reading.ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    storage.insert_reading(reading)
                    inserted += 1

                print(f"  {cursor:%d %b %H:%M} → {chunk_end:%H:%M}  {len(inverter_rows):>4} samples")
                cursor = chunk_end

            print(f"  → {inserted} rows")
            total += inserted

    print(f"\nStored {total} rows in {settings.database_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
