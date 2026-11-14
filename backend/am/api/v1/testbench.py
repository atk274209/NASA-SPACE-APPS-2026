"""Test bench: simulated live telemetry, mission clock, end of day (owner: feature BE1)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["testbench"])
