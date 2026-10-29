from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path, PurePosixPath

from .models import AppInfo, Platform


class IpaInspectionError(ValueError):
    pass


def inspect_ipa(path: str | Path) -> AppInfo:
    ipa_path = Path(path)
    if not ipa_path.is_file():
        raise IpaInspectionError(f"IPA 文件不存在：{ipa_path}")
    if ipa_path.suffix.lower() != ".ipa":
        raise IpaInspectionError("请选择 .ipa 文件。")

    try:
        with zipfile.ZipFile(ipa_path) as archive:
            info_entries = [
                entry
                for entry in archive.infolist()
                if _is_app_info_plist(entry.filename)
            ]
            if not info_entries:
                raise IpaInspectionError("IPA 中未找到 Payload/*.app/Info.plist。")

            errors: list[str] = []
            for entry in info_entries:
                try:
                    payload = plistlib.loads(archive.read(entry))
                    bundle_id = str(payload.get("CFBundleIdentifier", "")).strip()
                    executable = str(payload.get("CFBundleExecutable", "")).strip()
                    if not bundle_id or not executable:
                        raise IpaInspectionError(
                            "Info.plist 缺少 CFBundleIdentifier 或 CFBundleExecutable。"
                        )
                    display_name = str(
                        payload.get("CFBundleDisplayName")
                        or payload.get("CFBundleName")
                        or bundle_id
                    ).strip()
                    return AppInfo(
                        platform=Platform.IOS,
                        app_id=bundle_id,
                        display_name=display_name,
                        process_name=executable,
                        source="ipa",
                    )
                except (KeyError, ValueError, plistlib.InvalidFileException) as exc:
                    errors.append(str(exc))
            detail = errors[0] if errors else "无法解析 Info.plist。"
            raise IpaInspectionError(detail)
    except zipfile.BadZipFile as exc:
        raise IpaInspectionError("IPA 文件损坏或不是有效的 ZIP 包。") from exc
    except OSError as exc:
        raise IpaInspectionError(f"读取 IPA 失败：{exc}") from exc


def _is_app_info_plist(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    if len(parts) != 3:
        return False
    return (
        parts[0] == "Payload"
        and parts[1].lower().endswith(".app")
        and parts[2] == "Info.plist"
    )
