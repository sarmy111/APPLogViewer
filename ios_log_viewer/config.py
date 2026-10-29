from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Platform


def _empty_platform_map() -> dict[str, str]:
    return {platform.value: "" for platform in Platform}


def _empty_process_map() -> dict[str, dict[str, str]]:
    return {platform.value: {} for platform in Platform}


class AppConfig:
    MAX_FILTER_KEYWORD_HISTORY = 10

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {
            "last_app_id": _empty_platform_map(),
            "last_package_path": _empty_platform_map(),
            "process_by_platform_app": _empty_process_map(),
            "filter_keyword_history": [],
        }
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return
        if not isinstance(loaded, dict):
            return

        last_app_id = loaded.get("last_app_id")
        if isinstance(last_app_id, dict):
            for platform in Platform:
                value = last_app_id.get(platform.value)
                if isinstance(value, str):
                    self._data["last_app_id"][platform.value] = value

        last_package_path = loaded.get("last_package_path")
        if isinstance(last_package_path, dict):
            for platform in Platform:
                value = last_package_path.get(platform.value)
                if isinstance(value, str):
                    self._data["last_package_path"][platform.value] = value

        process_map = loaded.get("process_by_platform_app")
        if isinstance(process_map, dict):
            for platform in Platform:
                mapping = process_map.get(platform.value)
                if not isinstance(mapping, dict):
                    continue
                self._data["process_by_platform_app"][platform.value] = {
                    str(key): str(value)
                    for key, value in mapping.items()
                    if isinstance(key, str)
                    and isinstance(value, str)
                    and value
                }

        self._data["filter_keyword_history"] = self._sanitize_filter_history(
            loaded.get("filter_keyword_history")
        )
        self._migrate_legacy_ios_fields(loaded)

    def _sanitize_filter_history(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        history: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            keyword = item.strip()
            folded = keyword.casefold()
            if not keyword or folded in seen:
                continue
            seen.add(folded)
            history.append(keyword)
            if len(history) >= self.MAX_FILTER_KEYWORD_HISTORY:
                break
        return history

    def _migrate_legacy_ios_fields(self, loaded: dict[str, Any]) -> None:
        old_app_id = loaded.get("last_bundle_id")
        if isinstance(old_app_id, str) and old_app_id:
            self._data["last_app_id"]["ios"] = (
                self._data["last_app_id"]["ios"] or old_app_id
            )

        old_package_path = loaded.get("last_ipa")
        if isinstance(old_package_path, str) and old_package_path:
            self._data["last_package_path"]["ios"] = (
                self._data["last_package_path"]["ios"] or old_package_path
            )

        old_process_map = loaded.get("process_by_bundle_id")
        if not isinstance(old_process_map, dict):
            return
        ios_map = self._data["process_by_platform_app"]["ios"]
        for app_id, process_name in old_process_map.items():
            if (
                isinstance(app_id, str)
                and isinstance(process_name, str)
                and process_name
            ):
                ios_map.setdefault(app_id, process_name)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def last_app_id(self, platform: Platform) -> str:
        return str(self._data["last_app_id"].get(platform.value, ""))

    def set_last_app_id(self, platform: Platform, value: str) -> None:
        self._data["last_app_id"][platform.value] = value

    def package_path(self, platform: Platform) -> str:
        return str(self._data["last_package_path"].get(platform.value, ""))

    def set_package_path(self, platform: Platform, value: str) -> None:
        self._data["last_package_path"][platform.value] = value

    def process_for(self, platform: Platform, app_id: str) -> str:
        mapping = self._data["process_by_platform_app"].get(platform.value, {})
        if not isinstance(mapping, dict):
            return ""
        return str(mapping.get(app_id, ""))

    def set_process(self, platform: Platform, app_id: str, process_name: str) -> None:
        if not app_id or not process_name:
            return
        mapping = self._data["process_by_platform_app"].setdefault(
            platform.value,
            {},
        )
        mapping[app_id] = process_name

    def filter_keyword_history(self) -> list[str]:
        return list(self._data["filter_keyword_history"])

    def add_filter_keyword_history(self, keyword: str) -> bool:
        keyword = keyword.strip()
        if not keyword:
            return False
        current = self.filter_keyword_history()
        folded = keyword.casefold()
        updated = [keyword] + [
            item for item in current if item.casefold() != folded
        ]
        updated = updated[: self.MAX_FILTER_KEYWORD_HISTORY]
        if updated == current:
            return False
        self._data["filter_keyword_history"] = updated
        return True
