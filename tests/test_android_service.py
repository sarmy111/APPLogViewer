from __future__ import annotations

import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ios_log_viewer.android_service import (
    AndroidDeviceService,
    AndroidLogStream,
    build_adb_logcat_command,
    build_adb_package_list_command,
    humanize_android_error,
    parse_adb_devices,
    parse_android_process_table,
    parse_android_properties,
    parse_package_list,
)
from ios_log_viewer.log_parser import parse_android_log_line


class AndroidParserTests(unittest.TestCase):
    def test_parse_adb_device_states(self) -> None:
        output = """List of devices attached
R58M123 device product:beyond1 model:SM_G991B device:beyond1 transport_id:1
R59M456 unauthorized usb:1-1 transport_id:2
emulator-5554 offline transport_id:3
"""
        devices = parse_adb_devices(output)
        self.assertEqual([device.serial for device in devices], [
            "R58M123",
            "R59M456",
            "emulator-5554",
        ])
        self.assertEqual(devices[0].attributes["model"], "SM_G991B")
        self.assertEqual(devices[1].state, "unauthorized")
        self.assertEqual(devices[2].state, "offline")

    def test_parse_properties_packages_and_processes(self) -> None:
        properties = parse_android_properties(
            "[ro.product.model]: [Pixel 8]\n"
            "[ro.build.version.release]: [15]\n"
        )
        self.assertEqual(properties["ro.product.model"], "Pixel 8")
        self.assertEqual(properties["ro.build.version.release"], "15")

        apps = parse_package_list(
            "package:com.example.one\n"
            "package:com.example.two\n"
            "package:com.example.one\n"
        )
        self.assertEqual([app.app_id for app in apps], [
            "com.example.one",
            "com.example.two",
        ])

        processes = parse_android_process_table(
            "  PID NAME\n"
            " 1234 com.example.one\n"
            " 5678 com.example.one:remote\n"
        )
        self.assertEqual(processes, [
            "com.example.one",
            "com.example.one:remote",
        ])

    def test_threadtime_log_parsing_and_levels(self) -> None:
        warning = parse_android_log_line(
            "10-28 12:34:56.789  4321  4330 W Unity   : low memory",
            "com.example.game",
        )
        self.assertEqual(warning.timestamp, "10-28 12:34:56.789")
        self.assertEqual(warning.process, "com.example.game")
        self.assertEqual(warning.level, "WARNING")

        error = parse_android_log_line(
            "10-28 12:34:57.001  4321  4330 E Unity   : crash",
            "com.example.game",
        )
        self.assertEqual(error.level, "ERROR")

    def test_build_logcat_and_package_commands(self) -> None:
        adb = Path(r"C:\tools\adb.exe")
        self.assertEqual(
            build_adb_package_list_command(adb, "serial"),
            [
                str(adb),
                "-s",
                "serial",
                "shell",
                "pm",
                "list",
                "packages",
                "-3",
            ],
        )
        self.assertEqual(
            build_adb_logcat_command(adb, "serial", "123"),
            [
                str(adb),
                "-s",
                "serial",
                "logcat",
                "-v",
                "threadtime",
                "--pid=123",
            ],
        )

    def test_error_translation(self) -> None:
        self.assertIn("授权", humanize_android_error("error: device unauthorized"))
        self.assertIn("离线", humanize_android_error("error: device offline"))

    def test_list_devices_marks_unauthorized_and_reads_connected_info(self) -> None:
        def runner(command, **_kwargs):
            if command[-2:] == ["devices", "-l"]:
                output = (
                    b"List of devices attached\n"
                    b"R58 device product:test model:Pixel_8 device:test transport_id:1\n"
                    b"R59 unauthorized usb:1-1 transport_id:2\n"
                )
                return subprocess.CompletedProcess(command, 0, output, b"")
            output = (
                b"[ro.product.model]: [Pixel 8]\n"
                b"[ro.build.version.release]: [15]\n"
            )
            return subprocess.CompletedProcess(command, 0, output, b"")

        service = AndroidDeviceService(Path("tools"), runner=runner)
        devices = service.list_devices()
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[0].connected)
        self.assertEqual(devices[0].name, "Pixel 8")
        self.assertEqual(devices[0].os_version, "15")
        self.assertFalse(devices[1].connected)
        self.assertIn("授权", devices[1].detail)


class FakeAndroidService:
    def __init__(self, adb_path: Path, pids: list[str]) -> None:
        self.adb_path = adb_path
        self._pids = pids
        self._pid_index = 0
        self._lock = threading.Lock()
        self.started_commands: list[list[str]] = []
        self.popen_factory = self._popen

    def get_pid_for_process(self, _serial: str, _process_name: str) -> str:
        with self._lock:
            index = min(self._pid_index, len(self._pids) - 1)
            pid = self._pids[index]
            self._pid_index += 1
            return pid

    def _popen(self, command: list[str], **kwargs):
        self.started_commands.append(command)
        script = "import time; print('10-28 12:00:00.000  1  2 I Unity: line', flush=True); time.sleep(30)"
        return subprocess.Popen([sys.executable, "-u", "-c", script], **kwargs)


class AndroidLogStreamTests(unittest.TestCase):
    def test_pid_change_restarts_logcat_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adb_path = Path(temp_dir) / "adb.exe"
            fake = FakeAndroidService(adb_path, ["100", "", "200", "200"])
            statuses: queue.Queue[str] = queue.Queue()
            ended = threading.Event()
            stream = AndroidLogStream(
                fake,  # type: ignore[arg-type]
                "serial",
                "com.example.game",
                on_line=lambda _line: None,
                on_end=lambda _code: ended.set(),
                on_status=statuses.put,
                poll_interval=0.03,
            )
            stream.start()
            deadline = time.monotonic() + 5
            saw_restart = False
            while time.monotonic() < deadline:
                try:
                    status = statuses.get(timeout=0.2)
                except queue.Empty:
                    continue
                if "PID 200" in status:
                    saw_restart = True
                    break
            stream.stop()
            self.assertTrue(saw_restart)
            self.assertGreaterEqual(len(fake.started_commands), 2)
            self.assertFalse(ended.is_set())


if __name__ == "__main__":
    unittest.main()
