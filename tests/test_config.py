from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ios_log_viewer.config import AppConfig
from ios_log_viewer.models import Platform


class AppConfigTests(unittest.TestCase):
    def test_process_mapping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = AppConfig(path)
            config.set_last_app_id(Platform.IOS, "com.example.game")
            config.set_package_path(Platform.IOS, r"C:\builds\game.ipa")
            config.set_process(Platform.IOS, "com.example.game", "UnityGame")
            config.set_last_app_id(Platform.ANDROID, "com.example.android")
            config.set_process(
                Platform.ANDROID,
                "com.example.android",
                "com.example.android",
            )
            config.save()

            loaded = AppConfig(path)
            self.assertEqual(
                loaded.last_app_id(Platform.IOS),
                "com.example.game",
            )
            self.assertEqual(
                loaded.package_path(Platform.IOS),
                r"C:\builds\game.ipa",
            )
            self.assertEqual(
                loaded.process_for(Platform.IOS, "com.example.game"),
                "UnityGame",
            )
            self.assertEqual(
                loaded.process_for(Platform.ANDROID, "com.example.android"),
                "com.example.android",
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["last_app_id"]["ios"],
                "com.example.game",
            )

    def test_invalid_config_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            config = AppConfig(path)
            self.assertEqual(config.last_app_id(Platform.IOS), "")
            self.assertEqual(
                config.process_for(Platform.IOS, "missing"),
                "",
            )

    def test_legacy_ios_config_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "last_bundle_id": "com.example.legacy",
                        "last_ipa": r"C:\legacy\game.ipa",
                        "process_by_bundle_id": {
                            "com.example.legacy": "LegacyGame"
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = AppConfig(path)
            self.assertEqual(
                config.last_app_id(Platform.IOS),
                "com.example.legacy",
            )
            self.assertEqual(
                config.package_path(Platform.IOS),
                r"C:\legacy\game.ipa",
            )
            self.assertEqual(
                config.process_for(Platform.IOS, "com.example.legacy"),
                "LegacyGame",
            )

    def test_filter_keyword_history_deduplicates_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = AppConfig(path)
            for index in range(12):
                self.assertTrue(config.add_filter_keyword_history(f"kw{index}"))
            self.assertEqual(
                config.filter_keyword_history(),
                [f"kw{index}" for index in range(11, 1, -1)],
            )

            self.assertTrue(config.add_filter_keyword_history("KW11"))
            history = config.filter_keyword_history()
            self.assertEqual(history[0], "KW11")
            self.assertEqual(len(history), 10)
            self.assertEqual(sum(item.casefold() == "kw11" for item in history), 1)
            self.assertFalse(config.add_filter_keyword_history("   "))
            self.assertFalse(config.add_filter_keyword_history("KW11"))

            config.save()
            loaded = AppConfig(path)
            self.assertEqual(loaded.filter_keyword_history(), history)

    def test_invalid_filter_history_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "filter_keyword_history": [
                            " Error ",
                            "",
                            "error",
                            None,
                            "Warning",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = AppConfig(path)
            self.assertEqual(
                config.filter_keyword_history(),
                ["Error", "Warning"],
            )


if __name__ == "__main__":
    unittest.main()
