from __future__ import annotations

import plistlib
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .log_parser import parse_process_list, rank_process_candidates
from .models import AppInfo, DeviceInfo, Platform


class ToolCommandError(RuntimeError):
    pass


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def decode_output(payload: bytes | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def build_syslog_command(
    tools_dir: Path,
    udid: str,
    executable: str = "",
    exit_on_disconnect: bool = True,
) -> list[str]:
    command = [str(tools_dir / "idevicesyslog.exe"), "-u", udid]
    if executable:
        command.extend(["-p", executable])
    if exit_on_disconnect:
        command.append("-x")
    command.append("--no-colors")
    return command


def build_installed_apps_command(tools_dir: Path, udid: str) -> list[str]:
    return [
        str(tools_dir / "ideviceinstaller.exe"),
        "-u",
        udid,
        "list",
        "--xml",
    ]


def parse_installed_apps(payload: bytes | str | Sequence[dict[str, Any]]) -> list[AppInfo]:
    if isinstance(payload, (bytes, str)):
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        start = raw.find(b"<?xml")
        if start < 0:
            start = raw.find(b"<plist")
        if start > 0:
            raw = raw[start:]
        try:
            data = plistlib.loads(raw)
        except (ValueError, plistlib.InvalidFileException) as exc:
            raise ToolCommandError("无法解析设备返回到已安装 App 列表。") from exc
    else:
        data = list(payload)

    if isinstance(data, dict):
        for key in ("Applications", "applications", "applist"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        raise ToolCommandError("设备返回的 App 列表格式不受支持。")

    apps: dict[str, AppInfo] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        bundle_id = str(item.get("CFBundleIdentifier") or "").strip()
        if not bundle_id:
            continue
        display_name = str(
            item.get("CFBundleDisplayName")
            or item.get("CFBundleName")
            or bundle_id
        ).strip()
        executable = str(item.get("CFBundleExecutable") or "").strip()
        apps[bundle_id] = AppInfo(
            platform=Platform.IOS,
            app_id=bundle_id,
            display_name=display_name,
            process_name=executable,
            source="installed",
        )
    return sorted(apps.values(), key=lambda app: (app.display_name.casefold(), app.app_id))


def humanize_tool_error(message: str) -> str:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    if not text:
        return "iOS 工具未返回错误详情。"
    if text.startswith(
        (
            "未发现",
            "设备尚未",
            "设备已锁定",
            "无法连接设备服务",
            "设备未启用开发者模式",
            "iOS 工具执行失败",
            "iOS 工具执行超时",
            "无法启动 iOS 工具",
            "无法解析",
            "缺少运行文件",
        )
    ):
        return text
    if (
        "no device" in lowered
        or "device not found" in lowered
        or "device with udid" in lowered
    ):
        return "未发现 iOS 设备，请检查数据线并确认设备已连接。"
    if "pair" in lowered or "trust" in lowered:
        return "设备尚未与此电脑配对或信任，请解锁设备并点击“信任”。"
    if "passcode" in lowered or "locked" in lowered:
        return "设备已锁定，请解锁后再试。"
    if "could not connect to lockdownd" in lowered:
        return "无法连接设备服务，请确认 Apple Mobile Device 驱动正常并重新插拔设备。"
    if "unable to retrieve device list" in lowered or "usbmux" in lowered:
        return (
            "无法访问 iOS 设备服务。请安装并启动 Apple Devices（或 iTunes），"
            "然后重新连接设备。"
        )
    if "developer mode" in lowered:
        return "设备未启用开发者模式；如仅读取日志请检查连接，或按设备提示启用。"
    return f"iOS 工具执行失败：{text[-500:]}"


class LogStream:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        on_line: Callable[[str], None],
        on_end: Callable[[int], None],
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.on_line = on_line
        self.on_end = on_end
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("日志流已启动。")
        self._process = self._popen_factory(
            self.command,
            cwd=str(self.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=_creation_flags(),
        )
        self._reader = threading.Thread(
            target=self._read_loop,
            name="device-log-reader",
            daemon=True,
        )
        self._reader.start()

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in iter(process.stdout.readline, b""):
                if self._stop_event.is_set():
                    break
                line = decode_output(raw_line)
                if line:
                    self.on_line(line)
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass
            return_code = process.wait()
            self.on_end(return_code)

    def stop(self, timeout: float = 2.5) -> None:
        self._stop_event.set()
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=1.0)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None


class IosDeviceService:
    platform = Platform.IOS

    def __init__(
        self,
        tools_dir: Path,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.tools_dir = tools_dir
        self._runner = runner
        self._popen_factory = popen_factory

    def missing_tools(self) -> list[str]:
        required = ("idevice_id.exe", "ideviceinfo.exe", "ideviceinstaller.exe", "idevicesyslog.exe")
        return [name for name in required if not (self.tools_dir / name).is_file()]

    def _run(self, command: list[str], timeout: float = 20.0) -> str:
        try:
            result = self._runner(
                command,
                cwd=str(self.tools_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=_creation_flags(),
            )
        except FileNotFoundError as exc:
            raise ToolCommandError(f"缺少运行文件：{Path(command[0]).name}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolCommandError("iOS 工具执行超时，请重新连接设备后再试。") from exc
        except OSError as exc:
            raise ToolCommandError(f"无法启动 iOS 工具：{exc}") from exc

        stdout = decode_output(result.stdout)
        stderr = decode_output(result.stderr)
        if result.returncode != 0:
            raise ToolCommandError(humanize_tool_error(stderr or stdout))
        return stdout

    def list_devices(self) -> list[DeviceInfo]:
        output = self._run([str(self.tools_dir / "idevice_id.exe"), "-l"], timeout=10.0)
        udids = [line.strip() for line in output.splitlines() if line.strip()]
        devices: list[DeviceInfo] = []
        for udid in udids:
            name = "未知设备"
            version = "未知"
            connected = True
            detail = ""
            try:
                info_output = self._run(
                    [str(self.tools_dir / "ideviceinfo.exe"), "-u", udid, "-x"],
                    timeout=10.0,
                )
                raw_info = info_output.encode("utf-8")
                xml_start = raw_info.find(b"<?xml")
                if xml_start < 0:
                    xml_start = raw_info.find(b"<plist")
                if xml_start > 0:
                    raw_info = raw_info[xml_start:]
                info = plistlib.loads(raw_info)
                name = str(info.get("DeviceName") or name)
                version = str(info.get("ProductVersion") or version)
            except Exception as exc:
                connected = False
                detail = humanize_tool_error(str(exc))
            devices.append(
                DeviceInfo(
                    platform=Platform.IOS,
                    device_id=udid,
                    name=name,
                    os_version=version,
                    connected=connected,
                    detail=detail,
                )
            )
        return devices

    def list_installed_apps(self, udid: str) -> list[AppInfo]:
        output = self._run(build_installed_apps_command(self.tools_dir, udid), timeout=45.0)
        return parse_installed_apps(output)

    def list_running_processes(self, udid: str) -> list[str]:
        output = self._run(
            [str(self.tools_dir / "idevicesyslog.exe"), "-u", udid, "pidlist"],
            timeout=20.0,
        )
        return parse_process_list(output)

    def detect_process_candidates(
        self,
        udid: str,
        app: AppInfo,
        observe_seconds: float = 6.0,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        if cancel_event is not None and cancel_event.is_set():
            return []
        try:
            running = self.list_running_processes(udid)
            candidates = rank_process_candidates(app, running, include_unmatched=True)
            if candidates:
                return candidates[:50]
        except Exception:
            pass

        if cancel_event is not None and cancel_event.is_set():
            return []
        observed: set[str] = set()
        command = build_syslog_command(self.tools_dir, udid, exit_on_disconnect=True)
        process = self._popen_factory(
            command,
            cwd=str(self.tools_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=_creation_flags(),
        )

        def collect() -> None:
            if process.stdout is None:
                return
            for raw_line in iter(process.stdout.readline, b""):
                line = decode_output(raw_line)
                names = parse_process_list(line)
                if names:
                    observed.update(names)
                else:
                    match = re.search(r"(?<!\S)([A-Za-z0-9_.+\-/]+)\[\d+\]", line)
                    if match:
                        observed.add(match.group(1))

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        deadline = time.monotonic() + max(1.0, observe_seconds)
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.wait(0.1):
                break
            if cancel_event is None:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
        reader.join(timeout=1.0)
        return rank_process_candidates(app, observed, include_unmatched=True)[:50]

    def start_log_stream(
        self,
        device_id: str,
        app: AppInfo | None,
        process_name: str,
        on_line: Callable[[str], None],
        on_end: Callable[[int], None],
        on_status: Callable[[str], None] | None = None,
    ) -> LogStream:
        stream = LogStream(
            build_syslog_command(
                self.tools_dir,
                device_id,
                executable=process_name,
            ),
            self.tools_dir,
            on_line=on_line,
            on_end=on_end,
            popen_factory=self._popen_factory,
        )
        stream.start()
        return stream


DeviceService = IosDeviceService
