"""SQLite persistence: reading history, alert rules, alert events, callbacks.

Deliberately small — one file, stdlib ``sqlite3``, no ORM. The poller is the
only writer, so WAL mode plus a short busy timeout is enough concurrency
control for the read-heavy dashboard.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    register_no   TEXT    NOT NULL,
    ts            TEXT    NOT NULL,
    pv_power      REAL,
    load_power    REAL,
    grid_power    REAL,
    battery_power REAL,
    soc           REAL,
    yield_today   REAL,
    temperature   REAL,
    status        TEXT,
    payload       TEXT    NOT NULL,
    UNIQUE (register_no, ts)
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings (register_no, ts);

CREATE TABLE IF NOT EXISTS alert_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    register_no   TEXT    NOT NULL DEFAULT '*',
    metric        TEXT    NOT NULL,
    op            TEXT    NOT NULL,
    threshold     REAL    NOT NULL,
    duration_s    INTEGER NOT NULL DEFAULT 0,
    cooldown_s    INTEGER NOT NULL DEFAULT 900,
    severity      TEXT    NOT NULL DEFAULT 'warning',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    breach_since  TEXT,
    last_fired_at TEXT,
    state         TEXT    NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS alert_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id      INTEGER,
    rule_name    TEXT    NOT NULL,
    register_no  TEXT    NOT NULL,
    metric       TEXT    NOT NULL,
    value        REAL,
    threshold    REAL,
    op           TEXT,
    severity     TEXT    NOT NULL DEFAULT 'warning',
    kind         TEXT    NOT NULL DEFAULT 'triggered',
    message      TEXT    NOT NULL,
    ts           TEXT    NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON alert_events (ts DESC);

CREATE TABLE IF NOT EXISTS callbacks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    headers TEXT NOT NULL,
    body    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_callbacks_ts ON callbacks (ts DESC);

-- Settings editable from the UI, as opposed to the .env ones which are
-- read once at boot. Values are JSON so types survive the round trip.
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
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
    # Readings
    # ------------------------------------------------------------------
    def insert_reading(self, reading: Reading) -> None:
        """Store one sample. Re-inserting the same timestamp updates it, which
        keeps backfills idempotent."""
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO readings
                    (register_no, ts, pv_power, load_power, grid_power,
                     battery_power, soc, yield_today, temperature, status, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (register_no, ts) DO UPDATE SET
                    pv_power = excluded.pv_power,
                    load_power = excluded.load_power,
                    grid_power = excluded.grid_power,
                    battery_power = excluded.battery_power,
                    soc = excluded.soc,
                    yield_today = excluded.yield_today,
                    temperature = excluded.temperature,
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (
                    reading.register_no,
                    reading.ts.isoformat(),
                    reading.pv_power,
                    reading.load_power,
                    reading.grid_power,
                    reading.battery_power,
                    reading.soc,
                    reading.yield_today,
                    reading.temperature,
                    reading.status,
                    json.dumps(reading.to_public()),
                ),
            )

    def history(self, register_no: str, hours: int = 24, limit: int = 3000) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT ts, pv_power, load_power, grid_power, battery_power, soc, yield_today
                FROM readings
                WHERE register_no = ? AND ts >= ?
                ORDER BY ts ASC
                LIMIT ?
                """,
                (register_no, since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest(self, register_no: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT payload FROM readings WHERE register_no = ? ORDER BY ts DESC LIMIT 1",
                (register_no,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def daily_totals(self, register_no: str, days: int = 14) -> list[dict[str, Any]]:
        """Per-day energy, integrated from the sampled power series.

        ``yield_today`` is a reliable daily counter, so solar uses its maximum
        for the day. Load and grid have no daily counter in the samples we keep,
        so they are trapezoidally integrated.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT ts, pv_power, load_power, grid_power, yield_today
                FROM readings WHERE register_no = ? AND ts >= ?
                ORDER BY ts ASC
                """,
                (register_no, since),
            ).fetchall()

        buckets: dict[str, dict[str, Any]] = {}
        previous_ts: datetime | None = None
        previous_row: sqlite3.Row | None = None

        for row in rows:
            ts = datetime.fromisoformat(row["ts"])
            day = ts.date().isoformat()
            bucket = buckets.setdefault(
                day,
                {"day": day, "pv_kwh": 0.0, "load_kwh": 0.0, "import_kwh": 0.0, "export_kwh": 0.0},
            )
            if row["yield_today"] is not None:
                bucket["pv_kwh"] = max(bucket["pv_kwh"], row["yield_today"])

            if previous_ts is not None and previous_row is not None:
                hours = (ts - previous_ts).total_seconds() / 3600.0
                # Ignore gaps over 30 min: those are outages, not samples.
                if 0 < hours <= 0.5:
                    load = ((previous_row["load_power"] or 0) + (row["load_power"] or 0)) / 2
                    grid = ((previous_row["grid_power"] or 0) + (row["grid_power"] or 0)) / 2
                    bucket["load_kwh"] += load * hours / 1000
                    if grid >= 0:
                        bucket["export_kwh"] += grid * hours / 1000
                    else:
                        bucket["import_kwh"] += -grid * hours / 1000
            previous_ts, previous_row = ts, row

        return [
            {k: (round(v, 2) if isinstance(v, float) else v) for k, v in bucket.items()}
            for bucket in sorted(buckets.values(), key=lambda b: b["day"])
        ]

    def prune(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connect() as con:
            cursor = con.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            con.execute("DELETE FROM callbacks WHERE ts < ?", (cutoff,))
            con.execute("DELETE FROM alert_events WHERE ts < ?", (cutoff,))
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Alert rules
    # ------------------------------------------------------------------
    def list_rules(self, only_enabled: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alert_rules"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id ASC"
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql).fetchall()]

    def get_rule(self, rule_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
        return dict(row) if row else None

    def create_rule(self, data: dict[str, Any]) -> int:
        with self.connect() as con:
            cursor = con.execute(
                """
                INSERT INTO alert_rules
                    (name, register_no, metric, op, threshold, duration_s,
                     cooldown_s, severity, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["name"],
                    data.get("register_no", "*"),
                    data["metric"],
                    data["op"],
                    float(data["threshold"]),
                    int(data.get("duration_s", 0)),
                    int(data.get("cooldown_s", 900)),
                    data.get("severity", "warning"),
                    1 if data.get("enabled", True) else 0,
                    _now(),
                ),
            )
        return int(cursor.lastrowid)

    def update_rule(self, rule_id: int, data: dict[str, Any]) -> None:
        allowed = {
            "name", "register_no", "metric", "op", "threshold",
            "duration_s", "cooldown_s", "severity", "enabled",
        }
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as con:
            con.execute(
                f"UPDATE alert_rules SET {assignments} WHERE id = ?",
                (*fields.values(), rule_id),
            )

    def delete_rule(self, rule_id: int) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))

    def set_rule_state(
        self,
        rule_id: int,
        *,
        state: str,
        breach_since: str | None,
        last_fired_at: str | None = None,
    ) -> None:
        with self.connect() as con:
            if last_fired_at is None:
                con.execute(
                    "UPDATE alert_rules SET state = ?, breach_since = ? WHERE id = ?",
                    (state, breach_since, rule_id),
                )
            else:
                con.execute(
                    "UPDATE alert_rules SET state = ?, breach_since = ?, last_fired_at = ? "
                    "WHERE id = ?",
                    (state, breach_since, last_fired_at, rule_id),
                )

    # ------------------------------------------------------------------
    # Alert events
    # ------------------------------------------------------------------
    def add_event(self, event: dict[str, Any]) -> int:
        with self.connect() as con:
            cursor = con.execute(
                """
                INSERT INTO alert_events
                    (rule_id, rule_name, register_no, metric, value, threshold,
                     op, severity, kind, message, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("rule_id"),
                    event["rule_name"],
                    event["register_no"],
                    event["metric"],
                    event.get("value"),
                    event.get("threshold"),
                    event.get("op"),
                    event.get("severity", "warning"),
                    event.get("kind", "triggered"),
                    event["message"],
                    event.get("ts") or _now(),
                ),
            )
        return int(cursor.lastrowid)

    def list_events(self, limit: int = 100, unacked_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alert_events"
        if unacked_only:
            sql += " WHERE acknowledged = 0"
        sql += " ORDER BY ts DESC LIMIT ?"
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, (limit,)).fetchall()]

    def count_unacked(self) -> int:
        with self.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM alert_events WHERE acknowledged = 0"
            ).fetchone()
        return int(row["n"])

    def acknowledge(self, event_id: int | None = None) -> None:
        with self.connect() as con:
            if event_id is None:
                con.execute("UPDATE alert_events SET acknowledged = 1")
            else:
                con.execute("UPDATE alert_events SET acknowledged = 1 WHERE id = ?", (event_id,))

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
    # Inbound callbacks
    # ------------------------------------------------------------------
    def log_callback(self, headers: dict[str, str], body: Any) -> int:
        with self.connect() as con:
            cursor = con.execute(
                "INSERT INTO callbacks (ts, headers, body) VALUES (?, ?, ?)",
                (_now(), json.dumps(headers), json.dumps(body, default=str)),
            )
        return int(cursor.lastrowid)

    def list_callbacks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM callbacks ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
