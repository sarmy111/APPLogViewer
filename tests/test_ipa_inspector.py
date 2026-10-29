from __future__ import annotations

import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from ios_log_viewer.ipa_inspector import IpaInspectionError, inspect_ipa


class IpaInspectorTests(unittest.TestCase):
    def _write_ipa(self, fmt: plistlib.PlistFormat) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        ipa = Path(temp_dir.name) / "game.ipa"
        info = {
            "CFBundleIdentifier": "com.example.unitygame",
            "CFBundleExecutable": "UnityGame",
            "CFBundleDisplayName": "Unity 测试游戏",
            "CFBundleName": "UnityGame",
        }
        with zipfile.ZipFile(ipa, "w") as archive:
            archive.writestr(
                "Payload/UnityGame.app/Info.plist",
                plistlib.dumps(info, fmt=fmt),
            )
            archive.writestr(
                "Payload/UnityGame.app/Frameworks/Helper.framework/Info.plist",
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": "com.example.helper",
                        "CFBundleExecutable": "Helper",
                    }
                ),
            )
        return ipa

    def test_parses_xml_info_plist(self) -> None:
        app = inspect_ipa(self._write_ipa(plistlib.FMT_XML))
        self.assertEqual(app.app_id, "com.example.unitygame")
        self.assertEqual(app.process_name, "UnityGame")
        self.assertEqual(app.display_name, "Unity 测试游戏")
        self.assertEqual(app.source, "ipa")

    def test_parses_binary_info_plist(self) -> None:
        app = inspect_ipa(self._write_ipa(plistlib.FMT_BINARY))
        self.assertEqual(app.app_id, "com.example.unitygame")
        self.assertEqual(app.process_name, "UnityGame")

    def test_rejects_non_ipa(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".zip") as temp:
            with self.assertRaises(IpaInspectionError):
                inspect_ipa(temp.name)

    def test_rejects_zip_without_payload_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ipa = Path(temp_dir) / "broken.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("Other/Info.plist", plistlib.dumps({}))
            with self.assertRaises(IpaInspectionError):
                inspect_ipa(ipa)


if __name__ == "__main__":
    unittest.main()
