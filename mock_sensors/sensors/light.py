"""Dimmable smart light (one per room) — responds to UI brightness slider.

Command payloads (from DeviceQuickControl / scenes):
    {"state": "on" | "off"}
    {"state": "on", "brightness": 0-100}
State echoed on home/{room}/light/state as {"state", "brightness"}.
"""

from base import Actuator


class LightActuator(Actuator):
    kind = "light"

    def __init__(self, room: str) -> None:
        super().__init__(f"mock-light-{room}", room)
        self._on = False
        self._brightness = 80

    def apply(self, payload: dict) -> None:
        if "state" in payload:
            self._on = str(payload["state"]).strip().lower() in ("on", "true", "1")
        if "brightness" in payload:
            try:
                b = int(round(float(payload["brightness"])))
            except (TypeError, ValueError):
                b = self._brightness
            self._brightness = max(0, min(100, b))
            # A brightness command implies the light is on; 0 means off.
            self._on = self._brightness > 0

    def state_payload(self) -> dict:
        return {
            "state": "on" if self._on else "off",
            "brightness": self._brightness if self._on else 0,
        }
