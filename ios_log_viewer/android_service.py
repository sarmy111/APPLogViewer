from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .device_service import ToolCommandError, decode_output
from .log_parser import rank_process_candidates
from .models import AppInfo, DeviceInfo, Platform


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(slots=True)
class AdbDevice:
    serial: str
    state: str
    attributes: dict[str, str]


def parse_adb_devices(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("List of devices attached")
            or stripped.startswith("* daemon")
        ):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        attributes: dict[str, str] = {}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                attributes[key] = value
        devices.append(
            AdbDevice(
                serial=parts[0],
                state=parts[1],
                attributes=attributes,
            )
        )
    return devices


def parse_android_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for key, value in re.findall(r"^\[([^\]]+)\]:\s*\[([^\]]*)\]$", output, re.MULTILINE):
        properties[key] = value
    return properties


def parse_package_list(output: str) -> list[AppInfo]:
    apps: dict[str, AppInfo] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("package:"):
            continue
        package_name = stripped[len("package:") :].strip()
        if not package_name:
            continue
        apps[package_name] = AppInfo(
            platform=Platform.ANDROID,
            app_id=package_name,
            display_name=package_name,
            process_name=package_name,
            source="device",
        )
    return sorted(apps.values(), key=lambda app: app.app_id.casefold())


def parse_android_process_table(output: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("PID"):
            continue
        match = re.match(r"^\d+\s+(\S+)(?:\s+.*)?$", stripped)
        if not match:
            match = re.match(r"^(\S+)\s+\d+$", stripped)
        if not match:
            continue
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_adb_devices_command(adb_path: Path) -> list[str]:
    return [str(adb_path), "devices", "-l"]


def build_adb_package_list_command(adb_path: Path, serial: str) -> list[str]:
    return [
        str(adb_path),
        "-s",
        serial,
        "shell",
        "pm",
        "list",
        "packages",
        "-3",
    ]


def build_adb_pidof_command(adb_path: Path, serial: str, process_name: str) -> list[str]:
    return [
        str(adb_path),
        "-s",
        serial,
        "shell",
        "pidof",
        process_name,
    ]


def build_adb_process_table_command(adb_path: Path, serial: str) -> list[str]:
    return [
        str(adb_path),
        "-s",
        serial,
        "shell",
        "ps",
        "-A",
        "-o",
        "PID,NAME",
    ]


def build_adb_logcat_command(adb_path: Path, serial: str, pid: str) -> list[str]:
    return [
        str(adb_path),
        "-s",
        serial,
        "logcat",
        "-v",
        "threadtime",
        f"--pid={pid}",
    ]


def humanize_android_error(message: str) -> str:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    if not text:
        return "ADB 未返回错误详情。"
    if any(prefix in text for prefix in ("未发现", "设备", "ADB", "无法", "缺少")):
        if text.startswith(("未发现", "设备", "ADB", "无法", "缺少")):
            return text
    if "unauthorized" in lowered:
        return "Android 设备未授权。请开启 USB 调试，并在设备上允许此电脑的 RSA 调试密钥。"
    if "offline" in lowered:
        return "Android 设备处于离线状态，请重新插拔数据线后重试。"
    if "no permission" in lowered:
        return "当前用户没有 USB 设备访问权限，请检查 Android USB 驱动。"
    if (
        "device not found" in lowered
        or "not found" in lowered
        or "no devices" in lowered
    ):
        return "未发现目标 Android 设备，请检查数据线、USB 调试和开发者选项。"
    if "daemon" in lowered and "starting" not in lowered:
        return f"ADB 服务异常：{text[-500:]}"
    return f"ADB 执行失败：{text[-500:]}"


class AndroidLogStream:
    def __init__(
        self,
        service: AndroidDeviceService,
        serial: str,
        process_name: str,
        on_line: Callable[[str], None],
        on_end: Callable[[int], None],
        on_status: Callable[[str], None] | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.service = service
        self.serial = serial
        self.process_name = process_name
        self.on_line = on_line
        self.on_end = on_end
        self.on_status = on_status
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._logcat_process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._last_pid = ""

    def start(self) -> None:
        if self._monitor_thread is not None:
            raise RuntimeError("Android 日志流已启动。")
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="android-log-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                pid = self.service.get_pid_for_process(self.serial, self.process_name)
                consecutive_errors = 0
            except ToolCommandError as exc:
                consecutive_errors += 1
                if self.on_status:
                    self.on_status(f"Android 设备访问异常：{exc}")
                if consecutive_errors >= 3:
                    self._stop_logcat()
                    self.on_end(1)
                    return
                self._stop_event.wait(self.poll_interval)
                continue

            if self._stop_event.is_set():
                break
            needs_restart = pid != self._last_pid or (
                bool(pid) and not self._logcat_running()
            )
            if needs_restart:
                self._stop_logcat()
                self._last_pid = pid
                if pid:
                    self._start_logcat(pid)
                    if self.on_status:
                        self.on_status(f"正在采集 {self.process_name} · PID {pid}")
                elif self.on_status:
                    self.on_status("目标 App 已退出，等待重新启动…")

            self._stop_event.wait(self.poll_interval)
        self._stop_logcat()

    def _logcat_running(self) -> bool:
        with self._lock:
            process = self._logcat_process
            return process is not None and process.poll() is None

    def _start_logcat(self, pid: str) -> None:
        process = self.service.popen_factory(
            build_adb_logcat_command(self.service.adb_path, self.serial, pid),
            cwd=str(self.service.adb_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=_creation_flags(),
        )
        with self._lock:
            self._logcat_process = process
        self._reader_thread = threading.Thread(
            target=self._read_logcat,
            args=(process,),
            name="android-logcat-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _read_logcat(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
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

    def _stop_logcat(self) -> None:
        with self._lock:
            process = self._logcat_process
            self._logcat_process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if (
            self._reader_thread is not None
            and self._reader_thread is not threading.current_thread()
        ):
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None

    def stop(self, timeout: float = 2.5) -> None:
        self._stop_event.set()
        self._stop_logcat()
        if (
            self._monitor_thread is not None
            and self._monitor_thread is not threading.current_thread()
        ):
            self._monitor_thread.join(timeout=timeout)
        self._monitor_thread = None

    @property
    def running(self) -> bool:
        return bool(self._monitor_thread and self._monitor_thread.is_alive())


class AndroidDeviceService:
    platform = Platform.ANDROID

    def __init__(
        self,
        tools_dir: Path,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.tools_dir = tools_dir
        self.adb_path = (
            tools_dir / "android-platform-tools" / "platform-tools" / "adb.exe"
        )
        self.aapt_path = tools_dir / "android-build-tools" / "aapt.exe"
        self._runner = runner
        self.popen_factory = popen_factory

    def missing_tools(self) -> list[str]:
        required = (
            self.adb_path,
            self.adb_path.parent / "AdbWinApi.dll",
            self.adb_path.parent / "AdbWinUsbApi.dll",
            self.aapt_path,
        )
        return [path.name for path in required if not path.is_file()]

    def _run(self, command: list[str], timeout: float = 20.0) -> str:
        result = self._run_result(command, timeout)
        stdout = decode_output(result.stdout)
        stderr = decode_output(result.stderr)
        if result.returncode != 0:
            raise ToolCommandError(humanize_android_error(stderr or stdout))
        return stdout

    def _run_result(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._runner(
                command,
                cwd=str(self.adb_path.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=_creation_flags(),
            )
        except FileNotFoundError as exc:
            raise ToolCommandError(f"缺少运行文件：{Path(command[0]).name}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolCommandError("ADB 执行超时，请重新连接设备后再试。") from exc
        except OSError as exc:
            raise ToolCommandError(f"无法启动 ADB：{exc}") from exc

    def list_devices(self) -> list[DeviceInfo]:
        output = self._run(build_adb_devices_command(self.adb_path), timeout=15.0)
        devices: list[DeviceInfo] = []
        for device in parse_adb_devices(output):
            connected = device.state == "device"
            name = device.attributes.get("model", "未知设备").replace("_", " ")
            version = "未知"
            detail = ""
            if connected:
                try:
                    properties_output = self._run(
                        [
                            str(self.adb_path),
                            "-s",
                            device.serial,
                            "shell",
                            "getprop",
                        ],
                        timeout=12.0,
                    )
                    properties = parse_android_properties(properties_output)
                    name = properties.get("ro.product.model") or name
                    version = properties.get("ro.build.version.release") or version
                except ToolCommandError as exc:
                    connected = False
                    detail = str(exc)
            elif device.state == "unauthorized":
                detail = (
                    "USB 调试未授权，请在 Android 设备上允许此电脑的 RSA 调试密钥。"
                )
            else:
                detail = f"Android 设备状态异常：{device.state}"
            devices.append(
                DeviceInfo(
                    platform=Platform.ANDROID,
                    device_id=device.serial,
                    name=name,
                    os_version=version,
                    connected=connected,
                    detail=detail,
                )
            )
        return devices

    def list_installed_apps(self, device_id: str) -> list[AppInfo]:
        output = self._run(
            build_adb_package_list_command(self.adb_path, device_id),
            timeout=30.0,
        )
        return parse_package_list(output)

    def list_running_processes(self, device_id: str) -> list[str]:
        try:
            output = self._run(
                build_adb_process_table_command(self.adb_path, device_id),
                timeout=20.0,
            )
        except ToolCommandError:
            output = self._run(
                [
                    str(self.adb_path),
                    "-s",
                    device_id,
                    "shell",
                    "ps",
                    "-A",
                ],
                timeout=20.0,
            )
        return parse_android_process_table(output)

    def detect_process_candidates(
        self,
        device_id: str,
        app: AppInfo,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        if cancel_event is not None and cancel_event.is_set():
            return []
        try:
            processes = self.list_running_processes(device_id)
            candidates = rank_process_candidates(
                app,
                processes,
                include_unmatched=False,
            )
            if candidates:
                return candidates[:50]
        except ToolCommandError:
            pass
        pid = self.get_pid_for_process(device_id, app.process_name or app.app_id)
        return [app.process_name or app.app_id] if pid else []

    def get_pid_for_process(self, device_id: str, process_name: str) -> str:
        result = self._run_result(
            build_adb_pidof_command(self.adb_path, device_id, process_name),
            timeout=8.0,
        )
        stdout = decode_output(result.stdout).strip()
        stderr = decode_output(result.stderr).strip()
        if result.returncode == 0:
            pids = stdout.split()
            return pids[0] if pids else ""
        if not stderr and not stdout:
            return ""
        raise ToolCommandError(humanize_android_error(stderr or stdout))

    def start_log_stream(
        self,
        device_id: str,
        app: AppInfo | None,
        process_name: str,
        on_line: Callable[[str], None],
        on_end: Callable[[int], None],
        on_status: Callable[[str], None] | None = None,
    ) -> AndroidLogStream:
        stream = AndroidLogStream(
            self,
            device_id,
            process_name,
            on_line=on_line,
            on_end=on_end,
            on_status=on_status,
        )
        stream.start()
        return stream
