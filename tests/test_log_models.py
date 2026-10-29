from __future__ import annotations

import unittest

from ios_log_viewer.log_parser import (
    infer_log_level,
    parse_log_line,
    parse_process_list,
    rank_process_candidates,
)
from ios_log_viewer.models import AppInfo, LogBuffer, LogEntry, LogFilter, Platform


class LogParsingTests(unittest.TestCase):
    def test_parses_iso_timestamp_and_process(self) -> None:
        entry = parse_log_line(
            "2026-10-28 12:34:56.123 UnityGame[4321] <Notice>: "
            "NullReferenceException: object reference"
        )
        self.assertEqual(entry.timestamp, "2026-10-28 12:34:56.123")
        self.assertEqual(entry.process, "UnityGame")
        self.assertEqual(entry.level, "ERROR")

    def test_parses_apple_timestamp_and_fallback_process(self) -> None:
        entry = parse_log_line(
            "Oct 28 12:34:56.123 UnknownProcess[99] <Warning>: low memory",
            fallback_process="Fallback",
        )
        self.assertEqual(entry.timestamp, "Oct 28 12:34:56.123")
        self.assertEqual(entry.process, "UnknownProcess")
        self.assertEqual(entry.level, "WARNING")

    def test_level_inference(self) -> None:
        self.assertEqual(infer_log_level("Debug.LogError: failed"), "ERROR")
        self.assertEqual(infer_log_level("fatal crash detected"), "ERROR")
        self.assertEqual(infer_log_level("Debug.LogWarning: slow"), "WARNING")
        self.assertEqual(infer_log_level("player started"), "INFO")

    def test_parse_process_list_variants(self) -> None:
        output = """
123 UnityGame
456 SpringBoard (456)
789 kernel 789
123 UnityGame
"""
        self.assertEqual(
            parse_process_list(output),
            ["UnityGame", "SpringBoard", "kernel"],
        )

    def test_rank_process_candidates_prefers_executable(self) -> None:
        app = AppInfo(
            platform=Platform.IOS,
            app_id="com.example.unitygame",
            display_name="Unity Game",
            process_name="UnityGame",
        )
        ranked = rank_process_candidates(
            app,
            ["SpringBoard", "UnityGameHelper", "UnityGame"],
        )
        self.assertEqual(ranked[0], "UnityGame")


class LogFilterAndBufferTests(unittest.TestCase):
    def _entry(
        self,
        level: str,
        raw: str,
        process: str = "UnityGame",
    ) -> LogEntry:
        return LogEntry(
            timestamp="2026-10-28 12:00:00",
            process=process,
            raw=raw,
            level=level,
        )

    def test_filter_level_and_keyword(self) -> None:
        error = self._entry("ERROR", "battle failed")
        info = self._entry("INFO", "battle started")
        log_filter = LogFilter(
            show_info=False,
            show_warning=True,
            show_error=True,
            keyword="FAILED",
        )
        self.assertTrue(log_filter.matches(error))
        self.assertFalse(log_filter.matches(info))

    def test_filter_keyword_uses_full_display_line_case_insensitively(self) -> None:
        entry = self._entry("ERROR", "battle failed")
        self.assertTrue(LogFilter(keyword="error").matches(entry))
        self.assertTrue(LogFilter(keyword="FAILED").matches(entry))
        self.assertFalse(LogFilter(keyword="missing").matches(entry))

    def test_filter_keyword_combines_with_level_filter(self) -> None:
        warning = self._entry("WARNING", "low memory")
        error = self._entry("ERROR", "low memory")
        log_filter = LogFilter(show_warning=False, keyword="low")
        self.assertFalse(log_filter.matches(warning))
        self.assertTrue(log_filter.matches(error))

    def test_search_keyword_is_independent_from_base_filter(self) -> None:
        entry = self._entry("ERROR", "Battle failed alpha")
        self.assertTrue(
            LogFilter(
                keyword="battle",
                search_keyword="alpha",
            ).matches(entry)
        )
        self.assertFalse(
            LogFilter(
                keyword="battle",
                search_keyword="missing",
            ).matches(entry)
        )
        self.assertFalse(
            LogFilter(
                keyword="missing",
                search_keyword="battle",
            ).matches(entry)
        )

    def test_buffer_drops_oldest_and_counts(self) -> None:
        buffer = LogBuffer(max_entries=2)
        first = self._entry("INFO", "one")
        second = self._entry("INFO", "two")
        third = self._entry("INFO", "three")
        buffer.add(first)
        buffer.add(second)
        buffer.add(third)
        self.assertEqual(len(buffer), 2)
        self.assertEqual(buffer.dropped, 1)
        self.assertEqual(buffer.filtered(LogFilter())[0].raw, "two")


if __name__ == "__main__":
    unittest.main()
