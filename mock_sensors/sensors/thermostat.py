"""Thermostat (one per room) — responds to UI temperature slider.

Command payload (from DeviceQuickControl / scenes):
    {"target_temp": 16-30}
State echoed on home/{room}/thermostat/state as {"target_temp", "mode"}.
"""

from base import Actuator


class ThermostatActuator(Actuator):
    kind = "thermostat"

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-thermostat-{room}", room)
        self._target = 22.0

    def apply(self, payload: dict) -> None:
        if "target_temp" in payload:
            try:
                t = float(payload["target_temp"])
            except (TypeError, ValueError):
                t = self._target
            self._target = max(16.0, min(30.0, round(t * 2) / 2))  # clamp + 0.5° step

    def state_payload(self) -> dict:
        return {"target_temp": self._target, "mode": "heat"}
