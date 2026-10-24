"""Device adapter interfaces and a simulated living-room light."""

from typing import Protocol


class Device(Protocol):
    device_id: str

    def execute(self, action: str) -> str:
        """Execute a supported action and return a verified outcome description."""


class SimulatedLight:
    """In-memory light used for development and deterministic tests."""

    def __init__(self, device_id: str = "living-room-light", is_on: bool = True):
        self.device_id = device_id
        self.is_on = is_on

    def execute(self, action: str) -> str:
        if action == "turn_on":
            self.is_on = True
        elif action == "turn_off":
            self.is_on = False
        else:
            raise ValueError(f"Unsupported light action: {action}")
        return f"{self.device_id} is {'on' if self.is_on else 'off'}"
