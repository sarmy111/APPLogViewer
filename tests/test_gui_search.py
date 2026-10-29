from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from ios_log_viewer.config import AppConfig
from ios_log_viewer.gui import LogViewerApp
from ios_log_viewer.models import LogEntry, Platform


class GuiSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk unavailable: {exc}") from exc
        root.destroy()

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LogViewerApp(self.root)
        self.config_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_temp.cleanup)
        self.app.config = AppConfig(
            Path(self.config_temp.name) / "config.json"
        )
        self.app.filter_keyword_combo.configure(
            values=self.app.config.filter_keyword_history()
        )
        self._load_entries()

    def tearDown(self) -> None:
        if not self.app.closing:
            self.app._on_close()

    def _entry(self, level: str, message: str) -> LogEntry:
        return LogEntry(
            timestamp="2026-10-28 12:00:00",
            process="UnityGame",
            raw=f"2026-10-28 12:00:00 UnityGame[42] <Notice>: {message}",
            level=level,
        )

    def _load_entries(self) -> None:
        self.app.log_buffer.clear()
        self.app.log_buffer.add(self._entry("ERROR", "Battle failed alpha"))
        self.app.log_buffer.add(self._entry("WARNING", "battle warning beta"))
        self.app.log_buffer.add(self._entry("INFO", "idle"))
        self.app._render_filtered()

    def test_ctrl_f_shows_search_and_highlights_matches(self) -> None:
        self.root.deiconify()
        self.root.update()
        self.root.focus_force()
        self.root.event_generate("<Control-f>")
        self.root.update()
        self.assertTrue(self.app.search_visible)
        self.assertNotEqual(self.app.search_frame.winfo_manager(), "")

        self.app.search_var.set("battle")
        self.app._refresh_search_matches(reset_current=True, scroll_to_match=True)
        self.assertEqual(len(self.app.search_match_ranges), 2)
        self.assertEqual(self.app.search_count_var.get(), "1 / 2")
        self.assertTrue(self.app.log_text.tag_ranges("search_current"))

    def test_base_filter_remains_visible_when_search_is_hidden(self) -> None:
        self.assertNotEqual(self.app.filter_keyword_combo.winfo_manager(), "")
        self.assertEqual(self.app.search_frame.winfo_manager(), "")

        self.app.filter_keyword_var.set("idle")
        self.app._render_filtered()
        self.assertEqual(self.app.shown_count, 1)

        self.app._show_search()
        self.app.search_var.set("battle")
        self.app._refresh_search_matches(reset_current=True, scroll_to_match=True)
        self.assertEqual(self.app.shown_count, 1)
        self.assertEqual(self.app.search_count_var.get(), "0 / 0")

    def test_next_and_previous_wrap_around(self) -> None:
        self.app._show_search()
        self.app.search_var.set("battle")
        self.app._refresh_search_matches(reset_current=True, scroll_to_match=False)
        self.app._navigate_search(1)
        self.assertEqual(self.app.search_count_var.get(), "2 / 2")
        self.app._navigate_search(1)
        self.assertEqual(self.app.search_count_var.get(), "1 / 2")
        self.app._navigate_search(-1)
        self.assertEqual(self.app.search_count_var.get(), "2 / 2")

    def test_first_enter_selects_first_unsearched_match(self) -> None:
        self.app._show_search()
        self.app.search_var.set("battle")
        self.app._on_search_next()
        self.assertEqual(self.app.search_count_var.get(), "1 / 2")

    def test_enter_applies_pending_only_match_filter_before_navigation(self) -> None:
        self.app._show_search()
        self.app.search_only_var.set(True)
        self.app._on_search_only_changed()
        self.app.search_var.set("battle")
        self.app._on_search_next()
        self.assertEqual(self.app.shown_count, 2)
        self.assertEqual(self.app.search_count_var.get(), "1 / 2")

    def test_only_matching_filters_view_and_close_restores_all(self) -> None:
        self.app.filter_keyword_var.set("battle")
        self.app._render_filtered()
        self.app._show_search()
        self.app.search_var.set("failed")
        self.app.search_only_var.set(True)
        self.app._on_search_only_changed()
        self.assertEqual(self.app.shown_count, 1)
        self.assertEqual(
            len(self.app.log_buffer.filtered(self.app._current_filter())),
            1,
        )

        self.app._close_search()
        self.assertFalse(self.app.search_visible)
        self.assertFalse(self.app.search_only_var.get())
        self.assertEqual(self.app.filter_keyword_var.get(), "battle")
        self.assertEqual(self.app.shown_count, 2)

    def test_only_matching_intersects_base_and_search_text(self) -> None:
        self.app.filter_keyword_var.set("battle")
        self.app._render_filtered()
        self.app._show_search()
        self.app.search_var.set("beta")
        self.app.search_only_var.set(True)
        self.app._on_search_only_changed()
        self.assertEqual(self.app.shown_count, 1)

        self.app.search_var.set("idle")
        self.app._on_search_only_changed()
        self.assertEqual(self.app.shown_count, 0)

    def test_no_matches_disables_navigation(self) -> None:
        self.app._show_search()
        self.app.search_var.set("not-present")
        self.app._refresh_search_matches(reset_current=True, scroll_to_match=False)
        self.assertEqual(self.app.search_count_var.get(), "0 / 0")
        self.assertEqual(str(self.app.prev_search_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.next_search_button.cget("state")), "disabled")

    def test_live_entries_respect_only_matching_filter(self) -> None:
        self.app._show_search()
        self.app.search_var.set("battle")
        self.app.search_only_var.set(True)
        self.app._on_search_only_changed()

        self.app.log_buffer.add(self._entry("INFO", "unrelated"))
        self.app._render_filtered()
        self.assertEqual(self.app.shown_count, 2)

        self.app.log_buffer.add(self._entry("ERROR", "another Battle"))
        self.app._render_filtered()
        self.assertEqual(self.app.shown_count, 3)

    def test_platform_switch_updates_button_and_clears_log_view(self) -> None:
        self.app.config.add_filter_keyword_history("battle")
        self.app.filter_keyword_var.set("battle")
        self.app.platform_var.set(Platform.ANDROID.value)
        self.app._on_platform_changed()
        self.assertEqual(self.app.platform, Platform.ANDROID)
        self.assertEqual(str(self.app.package_button.cget("text")), "选择 APK")
        self.assertEqual(len(self.app.log_buffer), 0)
        self.assertEqual(self.app.log_text.get("1.0", "end").strip(), "")
        self.assertEqual(self.app.shown_count, 0)
        self.assertEqual(self.app.filter_keyword_var.get(), "battle")
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["battle"],
        )

    def test_enter_commits_filter_history_and_moves_item_to_top(self) -> None:
        self.app.config.add_filter_keyword_history("idle")
        self.app.config.add_filter_keyword_history("battle")
        self.app.filter_keyword_var.set("idle")
        self.app._on_filter_keyword_enter()
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["idle", "battle"],
        )
        self.assertEqual(
            tuple(self.app.filter_keyword_combo.cget("values")),
            ("idle", "battle"),
        )
        self.assertEqual(self.app.shown_count, 1)

    def test_focus_out_commits_trimmed_filter_and_ignores_blank(self) -> None:
        self.app.filter_keyword_var.set("  beta  ")
        self.app._on_filter_keyword_focus_out()
        self.assertEqual(self.app.filter_keyword_var.get(), "beta")
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["beta"],
        )

        self.app.filter_keyword_var.set("   ")
        self.app._on_filter_keyword_focus_out()
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["beta"],
        )

    def test_selecting_history_applies_filter_without_touching_search(self) -> None:
        self.app.config.add_filter_keyword_history("battle")
        self.app.config.add_filter_keyword_history("idle")
        self.app.filter_keyword_combo.configure(
            values=self.app.config.filter_keyword_history()
        )
        self.app.filter_keyword_var.set("battle")
        self.app.search_var.set("unchanged-search")
        self.app._on_filter_keyword_selected()
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["battle", "idle"],
        )
        self.assertEqual(self.app.search_var.get(), "unchanged-search")
        self.assertEqual(self.app.shown_count, 2)

    def test_search_text_is_not_added_to_filter_history(self) -> None:
        self.app._show_search()
        self.app.search_var.set("search-only-value")
        self.app._close_search()
        self.assertEqual(self.app.config.filter_keyword_history(), [])

    def test_close_commits_current_filter(self) -> None:
        self.app.filter_keyword_var.set("final-filter")
        self.app._on_close()
        self.assertEqual(
            self.app.config.filter_keyword_history(),
            ["final-filter"],
        )


if __name__ == "__main__":
    unittest.main()
