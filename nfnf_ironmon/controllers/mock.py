"""Mock backend for tests and for running without a controller."""

from __future__ import annotations

from .base import ControllerBackend, ControllerDevice, OpenController, PadState


class MockControllerBackend(ControllerBackend):
    backend_id = "mock"
    display_name = "Mock controller"
    status = "READY"

    def __init__(self, devices: list[ControllerDevice] | None = None):
        self.devices = devices if devices is not None else []
        self.states: dict[str, PadState] = {}

    def list_devices(self) -> list[ControllerDevice]:
        return list(self.devices)

    def open(self, device: ControllerDevice) -> "MockController":
        return MockController(self, device)


class MockController(OpenController):
    def __init__(self, backend: MockControllerBackend, device: ControllerDevice):
        self.backend, self.device = backend, device

    def poll(self) -> PadState:
        return self.backend.states.get(self.device.id, PadState())
