"""Background generation of simulated live telemetry (owner: feature BE1).

Contract used by am.main: start_ticker(ctx) returns a handle (or None when nothing was
started); stop_ticker(handle) stops it and must accept None.
"""

from __future__ import annotations

from typing import Any


def start_ticker(ctx: Any) -> Any:
    return None


def stop_ticker(handle: Any) -> None:
    return None
