"""Threshold alert engine.

Each rule watches one metric on one device (or all of them). A rule fires when
the comparison holds continuously for ``duration_s`` seconds, and will not fire
again until ``cooldown_s`` has passed. When the condition clears, a ``resolved``
event is recorded so the timeline reads as pairs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .models import METRIC_META, Reading
from .storage import Storage

log = logging.getLogger(__name__)

OPS: dict[str, Any] = {
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
    "eq": lambda value, threshold: value == threshold,
    "neq": lambda value, threshold: value != threshold,
}

OP_LABELS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "neq": "≠"}

SEVERITIES = ("info", "warning", "critical")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class AlertEngine:
    def __init__(self, storage: Storage, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    async def evaluate(self, reading: Reading) -> list[dict[str, Any]]:
        """Run every applicable rule against one reading; return fired events."""
        now = datetime.now(timezone.utc)
        fired: list[dict[str, Any]] = []

        for rule in self.storage.list_rules(only_enabled=True):
            if rule["register_no"] not in ("*", reading.register_no):
                continue

            value = reading.metric(rule["metric"])
            if value is None:
                continue

            compare = OPS.get(rule["op"])
            if compare is None:
                log.warning("Rule %s has an unknown operator %r", rule["id"], rule["op"])
                continue

            event = self._advance(rule, compare(value, rule["threshold"]), value, reading, now)
            if event:
                fired.append(event)

        for event in fired:
            await self._notify(event)
        return fired

    # ------------------------------------------------------------------
    def _advance(
        self,
        rule: dict[str, Any],
        breaching: bool,
        value: float,
        reading: Reading,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Update one rule's state machine; return an event if it produced one."""
        rule_id = rule["id"]
        breach_since = _parse(rule["breach_since"])
        last_fired = _parse(rule["last_fired_at"])
        state = rule["state"]

        if not breaching:
            if state == "firing":
                event = self._build_event(rule, value, reading, now, kind="resolved")
                self.storage.set_rule_state(rule_id, state="ok", breach_since=None)
                self.storage.add_event(event)
                return event
            if breach_since is not None:
                self.storage.set_rule_state(rule_id, state="ok", breach_since=None)
            return None

        # Breaching: start or continue the sustain window.
        if breach_since is None:
            breach_since = now
            self.storage.set_rule_state(
                rule_id, state="pending", breach_since=breach_since.isoformat()
            )

        if (now - breach_since).total_seconds() < rule["duration_s"]:
            return None
        if state == "firing":
            return None  # already firing; wait for it to resolve
        if last_fired and now - last_fired < timedelta(seconds=rule["cooldown_s"]):
            return None  # still cooling down

        event = self._build_event(rule, value, reading, now, kind="triggered")
        self.storage.set_rule_state(
            rule_id,
            state="firing",
            breach_since=breach_since.isoformat(),
            last_fired_at=now.isoformat(),
        )
        self.storage.add_event(event)
        return event

    def _build_event(
        self,
        rule: dict[str, Any],
        value: float,
        reading: Reading,
        now: datetime,
        *,
        kind: str,
    ) -> dict[str, Any]:
        meta = METRIC_META.get(rule["metric"], {"label": rule["metric"], "unit": ""})
        op = OP_LABELS.get(rule["op"], rule["op"])
        unit = meta["unit"]

        if kind == "resolved":
            message = (
                f"[{reading.register_no}] {meta['label']} back to normal: "
                f"{value:g}{unit} (threshold {op} {rule['threshold']:g}{unit})"
            )
        else:
            message = (
                f"[{reading.register_no}] {meta['label']} is {value:g}{unit} "
                f"{op} {rule['threshold']:g}{unit}"
            )
            if rule["duration_s"]:
                message += f" for {rule['duration_s']}s"

        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "register_no": reading.register_no,
            "metric": rule["metric"],
            "value": value,
            "threshold": rule["threshold"],
            "op": rule["op"],
            "severity": rule["severity"],
            "kind": kind,
            "message": message,
            "ts": now.isoformat(),
        }

    # ------------------------------------------------------------------
    async def _notify(self, event: dict[str, Any]) -> None:
        """Best-effort outbound webhook; a bad hook never breaks polling."""
        url = self.settings.alert_webhook_url
        if not url:
            return

        payload: dict[str, Any] = {**event, "text": event["message"]}
        # Slack takes `text`; Discord wants `content`.
        if "discord" in url:
            payload = {"content": event["message"]}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            log.warning("Alert webhook failed: %s", exc)


DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "name": "Inverter overheating",
        "metric": "temperature",
        "op": "gt",
        "threshold": 65,
        "duration_s": 60,
        "cooldown_s": 1800,
        "severity": "critical",
    },
]


def seed_default_rules(storage: Storage) -> None:
    """Populate starter rules the first time the app runs."""
    if storage.list_rules():
        return
    for rule in DEFAULT_RULES:
        storage.create_rule(rule)
    log.info("Seeded %d default alert rules", len(DEFAULT_RULES))
