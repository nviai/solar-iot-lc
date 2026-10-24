"""Settings that are edited from the UI rather than from ``.env``.

``.env`` and ``devices.json`` hold things that identify the installation —
where the Solar Monitor lives, each switch's IP and local key — which change
only when hardware does. Anything an operator tunes while the app runs
(thresholds, the solar and off-peak windows, the time zone they are read in,
whether automation is armed at all) lives here
instead: declared once as a :class:`Field`, persisted as JSON in SQLite under a
per-device namespace, and served to the front-end so the form and the
validator can never drift apart.
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


def zone_exists(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def seconds_until(now: datetime, target: time) -> float:
    """Seconds from ``now`` to the next occurrence of the local time ``target``."""
    candidate = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


def seconds_since(now: datetime, target: time) -> float:
    """Seconds from the last occurrence of the local time ``target`` to ``now``."""
    candidate = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate > now:
        candidate -= timedelta(days=1)
    return (now - candidate).total_seconds()


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
        self.kind = kind  # bool | int | float | time | enum | zone
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
        if self.kind == "zone":
            text = str(value).strip()
            return text if zone_exists(text) else self.default
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

        if self.kind == "zone":
            text = str(value).strip()
            if not text or not zone_exists(text):
                raise ValueError(f"{self.label}: {text or 'empty'} is not a known IANA time zone")
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
AUTOMATION_FIELDS: tuple[Field, ...] = (
    Field(
        "auto_enabled", False, "bool", "Automation armed",
        help="Master switch. When off, both schedules and the thresholds below "
             "are ignored entirely and the device only moves when you move it.",
    ),
    # --- Solar mode (daytime) ---------------------------------------------
    Field(
        "solar_enabled", True, "bool", "Solar mode",
        help="Inside the solar window, follow production: on above one "
             "threshold, off below another.",
    ),
    Field(
        "on_threshold_w", 5000.0, "float", "Switch on at", unit="W",
        minimum=0, maximum=100000,
        help="Switch the device on once solar production reaches this.",
    ),
    Field(
        "off_threshold_w", 1000.0, "float", "Switch off below", unit="W",
        minimum=0, maximum=100000,
        help="Switch the device off once solar production drops to this. Must "
             "be lower than the switch-on threshold; the gap between them is "
             "what stops the relay chattering around a single value.",
    ),
    Field(
        "window_start", "08:30", "time", "Solar window opens",
        help="Solar mode only ever switches the device on between these times.",
    ),
    Field("window_end", "16:30", "time", "Solar window closes"),
    Field(
        "off_outside_window", True, "bool", "Switch off when the solar window closes",
        help="Turn the device off at the end of the solar window even if solar "
             "is still strong.",
    ),
    # --- Off-peak mode (night tariff) -------------------------------------
    Field(
        "offpeak_enabled", False, "bool", "Off-peak mode",
        help="Switch the device on for the whole off-peak tariff window and off "
             "the moment it ends, whatever the sun is doing.",
    ),
    Field(
        "offpeak_start", "20:30", "time", "Off-peak starts",
        help="Start of the cheap-rate window. It may cross midnight.",
    ),
    Field("offpeak_end", "04:30", "time", "Off-peak ends"),
    # --- Shared -----------------------------------------------------------
    Field(
        "timezone", "Asia/Ho_Chi_Minh", "zone", "Time zone",
        help="IANA zone both windows are read in, e.g. Asia/Ho_Chi_Minh.",
    ),
    Field(
        "sustain_s", 120, "int", "Confirm for", unit="s", minimum=0, maximum=3600,
        help="A solar threshold has to stay crossed this long before the relay "
             "moves. Passing clouds otherwise cycle it every few minutes. "
             "Off-peak edges act immediately.",
    ),
    Field(
        "manual_override_minutes", 30, "int", "Manual override lasts",
        unit="min", minimum=0, maximum=1440,
        help="After you press On or Off by hand, automation stands down for this "
             "long. Set to 0 to have automation take back over immediately.",
    ),
    Field(
        "stale_after_s", 600, "int", "Treat telemetry as stale after",
        unit="s", minimum=60, maximum=86400,
        help="With no fresh inverter reading for this long, solar mode switches "
             "the device off rather than keep running on unknown data.",
    ),
    Field(
        "tick_seconds", 30, "int", "Evaluate every", unit="s",
        minimum=5, maximum=600,
        help="How often the relay is read and the rules re-evaluated. This is a "
             "LAN call to the switch plus a read of the Solar Monitor's cached "
             "figures; neither costs SolaX API quota.",
    ),
)


def check_thresholds(values: dict[str, Any]) -> None:
    """Cross-field rules for :data:`AUTOMATION_FIELDS`."""
    if values["off_threshold_w"] >= values["on_threshold_w"]:
        raise ValueError(
            "The switch-off threshold must be lower than the switch-on "
            "threshold, otherwise the relay would chatter."
        )
    # Equal ends read as "always" for the solar window, but an off-peak window
    # that never ends would just pin the relay on.
    if values["offpeak_start"] == values["offpeak_end"]:
        raise ValueError("Off-peak must start and end at different times.")


# ----------------------------------------------------------------------
class RuntimeConfig:
    """Reads and writes one namespace of UI-editable settings.

    Values are cached in memory because the automation loop reads them every
    tick; writes go straight through to SQLite and refresh the cache, so a
    restart keeps whatever was last saved.
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
