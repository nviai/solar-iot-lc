"""Threshold alert rules and the events they produce."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..alerts import OP_LABELS, SEVERITIES
from ..deps import get_poller, get_storage
from ..models import METRIC_META
from ..poller import Poller
from ..schemas import AckRequest, RuleCreate, RuleUpdate
from ..storage import Storage
from ..telemetry import known_devices

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

OPERATOR_TEXT = {
    "gt": "greater than", "gte": "at least", "lt": "less than",
    "lte": "at most", "eq": "equals", "neq": "not equal",
}


@router.get("/catalogue", summary="Metrics, operators and devices a rule can use")
async def catalogue(poller: Poller = Depends(get_poller)):
    return {
        "metrics": [{"key": key, **meta} for key, meta in METRIC_META.items()],
        "operators": [
            {"key": key, "symbol": OP_LABELS[key], "label": f"{OP_LABELS[key]} {text}"}
            for key, text in OPERATOR_TEXT.items()
        ],
        "severities": list(SEVERITIES),
        "devices": known_devices(poller),
    }


# ----------------------------------------------------------------------
@router.get("/rules")
async def list_rules(storage: Storage = Depends(get_storage)):
    return {"rules": storage.list_rules()}


@router.post("/rules", status_code=201)
async def create_rule(rule: RuleCreate, storage: Storage = Depends(get_storage)):
    return storage.get_rule(storage.create_rule(rule.model_dump()))


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: int, patch: RuleUpdate, storage: Storage = Depends(get_storage)
):
    if storage.get_rule(rule_id) is None:
        raise HTTPException(404, "Rule not found")
    changes = patch.model_dump(exclude_unset=True)
    if "enabled" in changes:
        changes["enabled"] = 1 if changes["enabled"] else 0
    storage.update_rule(rule_id, changes)
    return storage.get_rule(rule_id)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, storage: Storage = Depends(get_storage)):
    if storage.get_rule(rule_id) is None:
        raise HTTPException(404, "Rule not found")
    storage.delete_rule(rule_id)


# ----------------------------------------------------------------------
@router.get("/events")
async def list_events(
    limit: int = Query(100, ge=1, le=1000),
    unacked: bool = Query(False),
    storage: Storage = Depends(get_storage),
):
    return {
        "events": storage.list_events(limit, unacked_only=unacked),
        "unacked": storage.count_unacked(),
    }


@router.post("/events/ack")
async def ack_events(
    body: AckRequest | None = None, storage: Storage = Depends(get_storage)
):
    storage.acknowledge(body.id if body else None)
    return {"unacked": storage.count_unacked()}
