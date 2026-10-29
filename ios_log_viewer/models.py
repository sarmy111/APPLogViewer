from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_LOG_ENTRIES = 50_000


class Platform(str, Enum):
    IOS = "ios"
    ANDROID = "android"

    @property
    def display_name(self) -> str:
        return "iOS" if self is Platform.IOS else "Android"


@dataclass(slots=True)
class DeviceInfo:
    platform: Platform
    device_id: str
    name: str = "未知设备"
    os_version: str = "未知"
    connected: bool = True
    detail: str = ""

    @property
    def choice_label(self) -> str:
        version = self.os_version or "未知"
        state = "" if self.connected else "（连接异常）"
        return (
            f"{self.name} · {self.platform.display_name} {version} · "
            f"{self.device_id}{state}"
        )


@dataclass(slots=True)
class AppInfo:
    platform: Platform
    app_id: str
    display_name: str
    process_name: str = ""
    source: str = "installed"

    @property
    def label(self) -> str:
        name = self.display_name or self.app_id
        prefix = {
            "ipa": "IPA",
            "apk": "APK",
            "device": "设备",
            "installed": "设备",
        }.get(self.source, "设备")
        return f"{prefix} · {name} [{self.app_id}]"


@dataclass(slots=True, frozen=True)
class LogEntry:
    timestamp: str
    process: str
    raw: str
    level: str

    @property
    def display_line(self) -> str:
        return f"[{self.level:<7}] {self.raw}"

    @property
    def search_text(self) -> str:
        return self.display_line

    @property
    def export_line(self) -> str:
        timestamp = self.timestamp or "-"
        process = self.process or "-"
        return f"[{timestamp}] [{process}] [{self.level}] {self.raw}"


@dataclass(slots=True)
class LogFilter:
    show_info: bool = True
    show_warning: bool = True
    show_error: bool = True
    keyword: str = ""
    search_keyword: str = ""

    def matches(self, entry: LogEntry) -> bool:
        if entry.level == "ERROR" and not self.show_error:
            return False
        if entry.level == "WARNING" and not self.show_warning:
            return False
        if entry.level == "INFO" and not self.show_info:
            return False
        searchable_text = entry.search_text.casefold()
        if self.keyword and self.keyword.casefold() not in searchable_text:
            return False
        if (
            self.search_keyword
            and self.search_keyword.casefold() not in searchable_text
        ):
            return False
        return True


class LogBuffer:
    """Bounded in-memory log history used by the GUI and exports."""

    def __init__(self, max_entries: int = MAX_LOG_ENTRIES) -> None:
        self.max_entries = max_entries
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self.total_received = 0
        self.dropped = 0

    def add(self, entry: LogEntry) -> None:
        if len(self._entries) == self.max_entries:
            self.dropped += 1
        self._entries.append(entry)
        self.total_received += 1

    def clear(self) -> None:
        self._entries.clear()
        self.total_received = 0
        self.dropped = 0

    def filtered(self, log_filter: LogFilter) -> list[LogEntry]:
        return [entry for entry in self._entries if log_filter.matches(entry)]

    def all_entries(self) -> Iterable[LogEntry]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
