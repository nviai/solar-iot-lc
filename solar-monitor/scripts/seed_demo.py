"""Fill the database with synthetic samples so the charts have shape.

For development and for exploring the UI without credentials:

    python scripts/seed_demo.py --days 14 --step 300
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.demo import demo_reading  # noqa: E402
from app.storage import Storage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="how many days to generate")
    parser.add_argument("--step", type=int, default=300, help="sample spacing in seconds")
    parser.add_argument("--device", default="DEMO000001", help="registration number to use")
    args = parser.parse_args()

    storage = Storage(settings.database_path)
    now = datetime.now(timezone.utc)
    at = now - timedelta(days=args.days)
    count = 0

    while at < now:
        reading = demo_reading(args.device, at)
        reading.ts = at
        storage.insert_reading(reading)
        at += timedelta(seconds=args.step)
        count += 1

    print(
        f"Stored {count} synthetic rows for {args.device} "
        f"({args.days} days, every {args.step}s) in {settings.database_path}"
    )


if __name__ == "__main__":
    main()
