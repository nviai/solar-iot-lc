"""Request models for the JSON API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SwitchRequest(BaseModel):
    on: bool = Field(description="True to switch the device on, false for off.")
