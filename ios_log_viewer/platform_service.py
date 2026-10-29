from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from .models import AppInfo, DeviceInfo, Platform


class RunningLogStream(Protocol):
    @property
    def running(self) -> bool: ...

    def stop(self, timeout: float = 2.5) -> None: ...


class DeviceLogService(Protocol):
    platform: Platform

    def missing_tools(self) -> list[str]: ...

    def list_devices(self) -> list[DeviceInfo]: ...

    def list_installed_apps(self, device_id: str) -> list[AppInfo]: ...

    def detect_process_candidates(
        self,
        device_id: str,
        app: AppInfo,
        cancel_event: threading.Event | None = None,
    ) -> list[str]: ...

    def start_log_stream(
        self,
        device_id: str,
        app: AppInfo | None,
        process_name: str,
        on_line: Callable[[str], None],
        on_end: Callable[[int], None],
        on_status: Callable[[str], None] | None = None,
    ) -> RunningLogStream: ...
