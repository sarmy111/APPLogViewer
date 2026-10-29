from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .device_service import decode_output
from .models import AppInfo, Platform


class ApkInspectionError(ValueError):
    pass


def parse_aapt_badging(output: str) -> AppInfo:
    package_match = re.search(r"package:\s+name='([^']+)'", output)
    if not package_match:
        raise ApkInspectionError("AAPT 输出中未找到 Android 包名。")
    package_name = package_match.group(1).strip()
    label_match = re.search(
        r"application-label(?:-[^:]+)?:'([^']*)'",
        output,
    )
    display_name = label_match.group(1).strip() if label_match else package_name
    return AppInfo(
        platform=Platform.ANDROID,
        app_id=package_name,
        display_name=display_name or package_name,
        process_name=package_name,
        source="apk",
    )


def inspect_apk(aapt_path: Path, apk_path: str | Path) -> AppInfo:
    apk = Path(apk_path)
    if not apk.is_file():
        raise ApkInspectionError(f"APK 文件不存在：{apk}")
    if apk.suffix.lower() != ".apk":
        raise ApkInspectionError("请选择 .apk 文件。")
    if not aapt_path.is_file():
        raise ApkInspectionError(f"缺少 APK 解析工具：{aapt_path}")

    try:
        result = subprocess.run(
            [str(aapt_path), "dump", "badging", str(apk)],
            cwd=str(aapt_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=30.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApkInspectionError(f"执行 AAPT 失败：{exc}") from exc

    output = decode_output(result.stdout)
    error = decode_output(result.stderr)
    if result.returncode != 0:
        raise ApkInspectionError(
            "APK 解析失败：" + (error.strip() or output.strip() or "AAPT 返回错误")
        )
    return parse_aapt_badging(output)
