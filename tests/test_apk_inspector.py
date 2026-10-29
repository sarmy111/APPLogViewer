from __future__ import annotations

import unittest

from ios_log_viewer.apk_inspector import ApkInspectionError, parse_aapt_badging
from ios_log_viewer.models import Platform


class ApkInspectorTests(unittest.TestCase):
    def test_parses_aapt_badging(self) -> None:
        output = """package: name='com.example.unitygame' versionCode='42' versionName='1.2.3'
sdkVersion:'24'
application-label:'Unity 测试游戏'
launchable-activity: name='com.unity3d.player.UnityPlayerActivity'  label='' icon=''
"""
        app = parse_aapt_badging(output)
        self.assertEqual(app.platform, Platform.ANDROID)
        self.assertEqual(app.app_id, "com.example.unitygame")
        self.assertEqual(app.display_name, "Unity 测试游戏")
        self.assertEqual(app.process_name, "com.example.unitygame")
        self.assertEqual(app.source, "apk")

    def test_missing_package_is_rejected(self) -> None:
        with self.assertRaises(ApkInspectionError):
            parse_aapt_badging("application-label:'No Package'")


if __name__ == "__main__":
    unittest.main()
