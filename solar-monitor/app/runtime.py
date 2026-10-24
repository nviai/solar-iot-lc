"""Settings that are edited from the UI rather than from ``.env``.

The ``.env`` file holds things that identify the installation — credentials,
the gateway — which change only when the account does. Anything an operator
wants to tune while the app runs (the polling schedule, quiet hours) lives here
instead: declared once as a :class:`Field`, persisted as JSON in SQLite, and
served to the front-end so the form and the validator can never drift apart.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Local time
# ----------------------------------------------------------------------
def get_zone(name: str) -> ZoneInfo:
    """The configured zone, falling back to UTC rather than crashing a loop."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown time zone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, _, minute = str(value).partition(":")
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


def in_window(now: time, start: time, end: time) -> bool:
    """Is ``now`` inside [start, end)? Windows that wrap midnight are supported.

    A window whose ends are equal is read as "always", which is what someone
    typing 00:00-00:00 means.
    """
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def seconds_until(now: datetime, target: time) -> float:
    """Seconds from ``now`` to the next occurrence of the local time ``target``."""
    candidate = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


# ----------------------------------------------------------------------
# Field declarations
# ----------------------------------------------------------------------
class Field:
    """One setting: how to coerce it, how to validate it, how to label it."""

    def __init__(
        self,
        key: str,
        default: Any,
        kind: str,
        label: str,
        *,
        help: str = "",
        unit: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
        choices: Iterable[str] | None = None,
    ) -> None:
        self.key = key
        self.default = default
        self.kind = kind  # bool | int | float | time | enum
        self.label = label
        self.help = help
        self.unit = unit
        self.minimum = minimum
        self.maximum = maximum
        self.choices = tuple(choices) if choices else None

    # ------------------------------------------------------------------
    def coerce(self, value: Any) -> Any:
        """Best-effort conversion, used when reading back from the database."""
        try:
            return self._coerce(value)
        except (TypeError, ValueError):
            return self.default

    def _coerce(self, value: Any) -> Any:
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if self.kind == "int":
            return self._clamp(int(float(value)))
        if self.kind == "float":
            return self._clamp(float(value))
        if self.kind == "time":
            parsed = parse_hhmm(value, parse_hhmm(self.default, time(0, 0)))
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        if self.kind == "enum":
            text = str(value)
            return text if (self.choices and text in self.choices) else self.default
        return value

    def _clamp(self, value: float) -> Any:
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return int(value) if self.kind == "int" else float(value)

    def validate(self, value: Any) -> Any:
        """Strict conversion for user input; raises ``ValueError`` on nonsense."""
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in (
                "1", "0", "true", "false", "yes", "no", "on", "off",
            ):
                return value.strip().lower() in ("1", "true", "yes", "on")
            raise ValueError(f"{self.label}: expected true or false")

        if self.kind in ("int", "float"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{self.label}: expected a number") from None
            if number != number or number in (float("inf"), float("-inf")):
                raise ValueError(f"{self.label}: expected a finite number")
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f"{self.label}: must be at least {self.minimum:g}")
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f"{self.label}: must be at most {self.maximum:g}")
            return int(number) if self.kind == "int" else number

        if self.kind == "time":
            text = str(value).strip()
            parts = text.split(":")
            if len(parts) != 2:
                raise ValueError(f"{self.label}: expected HH:MM")
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError:
                raise ValueError(f"{self.label}: expected HH:MM") from None
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"{self.label}: {text} is not a valid time")
            return f"{hour:02d}:{minute:02d}"

        if self.kind == "enum":
            text = str(value)
            if self.choices and text not in self.choices:
                raise ValueError(
                    f"{self.label}: expected one of {', '.join(self.choices)}"
                )
            return text

        return value

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "help": self.help,
            "unit": self.unit,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "choices": list(self.choices) if self.choices else None,
        }


# ----------------------------------------------------------------------
# The catalogue
# ----------------------------------------------------------------------
SCHEDULE_FIELDS: tuple[Field, ...] = (
    Field(
        "poll_interval_seconds", 60, "int", "Normal interval", unit="s",
        minimum=15, maximum=86400,
        help="Daytime polling interval. Each cycle costs 2-3 API calls per "
             "device against your service package's quota.",
    ),
    Field(
        "quiet_enabled", True, "bool", "Quiet hours",
        help="Poll less often — or not at all — overnight, when nothing is "
             "being produced and there is nothing to see.",
    ),
    Field("quiet_start", "23:00", "time", "Quiet from"),
    Field("quiet_end", "05:00", "time", "Quiet until"),
    Field(
        "quiet_mode", "interval", "enum", "During quiet hours",
        choices=("interval", "pause"),
        help="'interval' keeps polling at the slower rate below; 'pause' stops "
             "calling the API completely until the window ends.",
    ),
    Field(
        "quiet_interval_seconds", 900, "int", "Quiet interval", unit="s",
        minimum=60, maximum=86400,
        help="Used when the quiet-hours mode is 'interval'.",
    ),
)


# ----------------------------------------------------------------------
class RuntimeConfig:
    """Reads and writes one namespace of UI-editable settings.

    Values are cached in memory because the poller reads them every cycle;
    writes go straight through to SQLite and refresh the cache, so a restart
    keeps whatever was last saved.
    """

    def __init__(
        self,
        storage: Any,
        namespace: str,
        fields: tuple[Field, ...],
        *,
        seed: dict[str, Any] | None = None,
        invariants: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.storage = storage
        self.namespace = namespace
        self.fields = fields
        self._by_key = {f.key: f for f in fields}
        self._invariants = invariants
        self._cache: dict[str, Any] | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        #: Values from ``.env`` that stand in until something is saved from the
        #: UI, so an existing deployment's configuration is not quietly ignored.
        self._seed = {
            key: self._by_key[key].coerce(value)
            for key, value in (seed or {}).items()
            if key in self._by_key
        }

    # ------------------------------------------------------------------
    def _db_key(self, key: str) -> str:
        return f"{self.namespace}.{key}"

    def all(self) -> dict[str, Any]:
        if self._cache is None:
            stored = self.storage.get_settings_blob()
            self._cache = {
                field.key: (
                    field.coerce(stored[self._db_key(field.key)])
                    if self._db_key(field.key) in stored
                    else self._seed.get(field.key, field.default)
                )
                for field in self.fields
            }
        return dict(self._cache)

    def get(self, key: str) -> Any:
        return self.all()[key]

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist a partial update. Raises ``ValueError``."""
        unknown = set(patch) - set(self._by_key)
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")

        clean = {key: self._by_key[key].validate(value) for key, value in patch.items()}
        merged = {**self.all(), **clean}
        if self._invariants is not None:
            self._invariants(merged)

        self.storage.put_settings_blob(
            {self._db_key(key): value for key, value in clean.items()}
        )
        self._cache = merged
        for listener in self._listeners:
            try:
                listener(dict(merged))
            except Exception:
                log.exception("Settings listener failed for %s", self.namespace)
        return dict(merged)

    def on_change(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def describe(self) -> list[dict[str, Any]]:
        return [field.describe() for field in self.fields]
