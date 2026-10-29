from __future__ import annotations

import plistlib
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ios_log_viewer.device_service import (
    LogStream,
    build_installed_apps_command,
    build_syslog_command,
    decode_output,
    humanize_tool_error,
    parse_installed_apps,
)


class DeviceServiceTests(unittest.TestCase):
    def test_build_syslog_command_uses_exact_process_filter(self) -> None:
        command = build_syslog_command(
            Path(r"C:\Tool\iOSUnityLogViewer\tools"),
            "device-udid",
            "UnityGame",
        )
        self.assertEqual(
            command,
            [
                r"C:\Tool\iOSUnityLogViewer\tools\idevicesyslog.exe",
                "-u",
                "device-udid",
                "-p",
                "UnityGame",
                "-x",
                "--no-colors",
            ],
        )

    def test_build_installed_apps_command(self) -> None:
        command = build_installed_apps_command(Path(r"C:\tools"), "abc")
        self.assertEqual(
            command,
            [r"C:\tools\ideviceinstaller.exe", "-u", "abc", "list", "--xml"],
        )

    def test_parse_installed_apps_xml_and_binary(self) -> None:
        apps = [
            {
                "CFBundleIdentifier": "com.example.game",
                "CFBundleDisplayName": "Example Game",
                "CFBundleExecutable": "ExampleGame",
            },
            {
                "CFBundleIdentifier": "com.apple.Preferences",
                "CFBundleName": "Settings",
            },
        ]
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            parsed = parse_installed_apps(plistlib.dumps(apps, fmt=fmt))
            self.assertEqual([app.app_id for app in parsed], [
                "com.example.game",
                "com.apple.Preferences",
            ])
            self.assertEqual(parsed[0].process_name, "ExampleGame")
            self.assertEqual(parsed[1].display_name, "Settings")

    def test_parse_installed_apps_skips_tool_noise(self) -> None:
        payload = (
            b"WARNING: test noise\n"
            + plistlib.dumps(
                [
                    {
                        "CFBundleIdentifier": "com.example.noise",
                        "CFBundleDisplayName": "Noise",
                    }
                ]
            )
        )
        apps = parse_installed_apps(payload)
        self.assertEqual(apps[0].app_id, "com.example.noise")

    def test_decode_and_error_translation(self) -> None:
        self.assertIn("设备", decode_output("设备".encode("gb18030")))
        self.assertIn("信任", humanize_tool_error("Please trust this computer"))
        self.assertIn("未发现", humanize_tool_error("ERROR: No device found"))

    def test_log_stream_reads_and_stops_subprocess(self) -> None:
        lines: queue.Queue[str] = queue.Queue()
        ended = threading.Event()
        script = (
            "import time; "
            "print('2026-10-28 12:00:00 UnityGame[42] <Notice>: warning test', flush=True); "
            "time.sleep(30)"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            stream = LogStream(
                [sys.executable, "-u", "-c", script],
                Path(temp_dir),
                on_line=lines.put,
                on_end=lambda _code: ended.set(),
                popen_factory=subprocess.Popen,
            )
            stream.start()
            line = lines.get(timeout=5)
            self.assertIn("UnityGame[42]", line)
            self.assertTrue(stream.running)
            stream.stop()
            self.assertTrue(ended.wait(timeout=5))
            self.assertFalse(stream.running)


if __name__ == "__main__":
    unittest.main()
