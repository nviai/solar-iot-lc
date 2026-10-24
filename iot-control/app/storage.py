"""SQLite persistence: UI-edited settings and the device activity log.

Deliberately small — one file, stdlib ``sqlite3``, no ORM. WAL mode plus a
short busy timeout is plenty for a handful of devices writing an event now and
then.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
-- Settings editable from the UI, namespaced per device. Values are JSON so
-- types survive the round trip.
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Audit trail: every time a device moved, who moved it, and why.
CREATE TABLE IF NOT EXISTS device_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    device_id TEXT    NOT NULL,
    action    TEXT    NOT NULL,
    source    TEXT    NOT NULL DEFAULT 'auto',
    reason    TEXT    NOT NULL DEFAULT '',
    pv_power  REAL,
    ok        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_device_events_device ON device_events (device_id, id DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA busy_timeout=5000")
            self._local.con = con
        return con

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = self._connection()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise

    # ------------------------------------------------------------------
    # UI-editable settings
    # ------------------------------------------------------------------
    def get_settings_blob(self) -> dict[str, Any]:
        """Every stored override, decoded. Unknown keys are the caller's problem."""
        with self.connect() as con:
            rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        return out

    def put_settings_blob(self, values: dict[str, Any]) -> None:
        with self.connect() as con:
            con.executemany(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [(key, json.dumps(value)) for key, value in values.items()],
            )

    # ------------------------------------------------------------------
    # Device activity
    # ------------------------------------------------------------------
    def add_event(
        self,
        *,
        device_id: str,
        action: str,
        source: str,
        reason: str = "",
        pv_power: float | None = None,
        ok: bool = True,
    ) -> int:
        with self.connect() as con:
            cursor = con.execute(
                "INSERT INTO device_events "
                "(ts, device_id, action, source, reason, pv_power, ok) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), device_id, action, source, reason, pv_power, 1 if ok else 0),
            )
        return int(cursor.lastrowid)

    def list_events(
        self, device_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM device_events"
        params: tuple[Any, ...] = ()
        if device_id is not None:
            sql += " WHERE device_id = ?"
            params = (device_id,)
        sql += " ORDER BY id DESC LIMIT ?"
        with self.connect() as con:
            rows = con.execute(sql, (*params, limit)).fetchall()
        return [{**dict(r), "ok": bool(r["ok"])} for r in rows]

    def prune(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connect() as con:
            cursor = con.execute("DELETE FROM device_events WHERE ts < ?", (cutoff,))
        return cursor.rowcount
