"""Adapter between the controller and the cabin hardware.

The MVP has NO real hardware: SimulatedCabin keeps the state in EnvControl rows, reads it back with
simulated noise and drift, and honours injected sensor/actuator faults. Real hardware would implement
the same CabinActuator protocol.
"""

from __future__ import annotations

import random
from typing import Protocol

from am.cabin.envelope import load_envelope
from am.db.base import utcnow
from am.db.models_cabin import EnvControl


class ActuatorFault(Exception):
    """The actuator did not accept the command."""


class SensorFault(Exception):
    """No valid reading."""


class CabinActuator(Protocol):
    def apply(self, control: EnvControl, value: float) -> None: ...
    def read(self, control: EnvControl) -> float: ...


class SimulatedCabin:
    """SIMULATED cabin: the actuator reaches its setpoint at once; readings = setpoint + drift + noise."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def apply(self, control: EnvControl, value: float) -> None:
        if control.fault == "actuator":
            raise ActuatorFault(f"{control.variable} actuator did not respond (SIMULATED fault)")
        control.setpoint = value

    def read(self, control: EnvControl) -> float:
        if control.fault == "sensor":
            raise SensorFault(f"{control.variable} sensor returned no valid reading (SIMULATED fault)")
        lim = load_envelope().variables[control.variable]
        value = control.setpoint + (control.drift or 0.0) + self.rng.gauss(0, lim.noise)
        decimals = 2 if control.variable == "pressure" else 1
        control.last_reading, control.last_read_at = round(value, decimals), utcnow()
        return control.last_reading
