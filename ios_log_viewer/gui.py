from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .android_service import AndroidDeviceService
from .apk_inspector import ApkInspectionError, inspect_apk
from .config import AppConfig
from .device_service import IosDeviceService, humanize_tool_error
from .ipa_inspector import IpaInspectionError, inspect_ipa
from .log_parser import parse_android_log_line, parse_log_line
from .models import AppInfo, DeviceInfo, LogBuffer, LogEntry, LogFilter, Platform
from .platform_service import DeviceLogService, RunningLogStream


class ProcessSelectionDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        app: AppInfo,
        candidates: list[str],
    ) -> None:
        super().__init__(parent)
        self.title("确认目标 App 进程")
        self.geometry("520x390")
        self.minsize(420, 300)
        self.transient(parent)
        self.selected: str | None = None

        ttk.Label(
            self,
            text=(
                f"请选择 {app.display_name} 对应的进程。\n"
                "若目标 App 尚未启动，请先打开 App，再重新点击“识别进程”。"
            ),
            justify="left",
        ).pack(fill="x", padx=14, pady=(14, 8))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=14, pady=4)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            exportselection=False,
            activestyle="dotbox",
        )
        scrollbar.configure(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for candidate in candidates:
            self.listbox.insert("end", candidate)
        if candidates:
            self.listbox.selection_set(0)
            self.listbox.activate(0)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=14, pady=(8, 14))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self._accept).pack(
            side="right", padx=(0, 8)
        )
        self.listbox.bind("<Double-Button-1>", lambda _event: self._accept())
        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def _accept(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self.selected = str(self.listbox.get(selection[0]))
        self.destroy()


class LogViewerApp:
    POLL_INTERVAL_MS = 70
    DEVICE_REFRESH_MS = 3_000
    MAX_TEXT_LINES = 50_000

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.base_dir = Path(__file__).resolve().parent.parent
        self.tools_dir = self.base_dir / "tools"
        self.logs_dir = self.base_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.config = AppConfig(self.base_dir / "config.json")
        self.platform = Platform.IOS
        self.services: dict[Platform, DeviceLogService] = {
            Platform.IOS: IosDeviceService(self.tools_dir),
            Platform.ANDROID: AndroidDeviceService(self.tools_dir),
        }
        self.service = self.services[self.platform]
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.log_buffer = LogBuffer()

        self.device_choice_map: dict[str, DeviceInfo] = {}
        self.app_choice_map: dict[str, AppInfo] = {}
        self.selected_device_id = ""
        self.selected_app: AppInfo | None = None
        self.stream: RunningLogStream | None = None
        self.session_file: Any = None
        self.session_path: Path | None = None
        self.session_id = 0
        self.session_line_count = 0
        self.session_tail: deque[str] = deque(maxlen=5)
        self.stop_requested = False
        self.running = False
        self.closing = False
        self.refreshing_devices = False
        self.device_request_id = 0
        self.apps_request_id = 0
        self.apps_loading = False
        self.detecting_process = False
        self.detection_cancel_event = threading.Event()
        self.shown_count = 0
        self.filter_job: str | None = None
        self.search_job: str | None = None
        self.search_visible = False
        self.search_match_ranges: list[tuple[str, str]] = []
        self.search_current_index = -1
        self.search_current_start = ""
        self.search_applied_query = ""

        self._build_ui()
        self.root.bind_all("<Control-f>", self._on_find_shortcut, add="+")
        self.root.bind("<Escape>", self._on_root_escape, add="+")
        missing = self.service.missing_tools()
        if missing:
            self.status_var.set("缺少运行文件：" + "、".join(missing))
        else:
            self.status_var.set(
                f"正在查找 {self.platform.display_name} 设备…"
            )
        self.root.after(180, self._refresh_devices_async)
        self.root.after(self.DEVICE_REFRESH_MS, self._auto_refresh_devices)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if missing:
            self.root.after(
                250,
                lambda: messagebox.showerror(
                    "运行文件缺失",
                    "工具运行文件不完整：\n"
                    + "\n".join(missing)
                    + "\n\n请重新解压完整的工具目录。",
                ),
            )

    def _build_ui(self) -> None:
        self.root.title("Unity USB 日志筛选器")
        self.root.geometry("1220x780")
        self.root.minsize(980, 620)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Hint.TLabel", foreground="#5f6368")
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"))

        header = ttk.Frame(self.root, padding=(14, 12, 14, 4))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Unity USB 日志筛选器", style="Title.TLabel").pack(
            side="left"
        )
        ttk.Label(
            header,
            text="USB 实时采集 · 按 App 进程精确过滤",
            style="Hint.TLabel",
        ).pack(side="left", padx=(14, 0), pady=(5, 0))

        platform_frame = ttk.Frame(header)
        platform_frame.pack(side="right")
        ttk.Label(platform_frame, text="平台").pack(side="left", padx=(0, 6))
        self.platform_var = tk.StringVar(value=self.platform.value)
        for platform in Platform:
            ttk.Radiobutton(
                platform_frame,
                text=platform.display_name,
                value=platform.value,
                variable=self.platform_var,
                command=self._on_platform_changed,
            ).pack(side="left", padx=4)

        connection = ttk.LabelFrame(self.root, text="设备与 App", padding=10)
        connection.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 4))
        connection.columnconfigure(1, weight=1)

        ttk.Label(connection, text="设备").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            connection, textvariable=self.device_var, state="readonly", width=70
        )
        self.device_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        self.refresh_button = ttk.Button(
            connection, text="刷新设备", command=self._refresh_devices_async
        )
        self.refresh_button.grid(row=0, column=2, padx=(0, 6))
        self.package_button = ttk.Button(
            connection,
            text="选择 IPA",
            command=self._choose_app_package,
        )
        self.package_button.grid(row=0, column=3)

        ttk.Label(connection, text="App").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        self.app_var = tk.StringVar()
        self.app_combo = ttk.Combobox(
            connection, textvariable=self.app_var, state="readonly", width=70
        )
        self.app_combo.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        self.app_combo.bind("<<ComboboxSelected>>", self._on_app_selected)
        self.load_apps_button = ttk.Button(
            connection, text="读取 App 列表", command=self._load_apps_async
        )
        self.load_apps_button.grid(row=1, column=2, padx=(0, 6), pady=(8, 0))
        self.detect_button = ttk.Button(
            connection, text="识别进程", command=self._detect_process_async
        )
        self.detect_button.grid(row=1, column=3, pady=(8, 0))

        ttk.Label(connection, text="进程名").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        self.process_var = tk.StringVar()
        self.process_entry = ttk.Entry(connection, textvariable=self.process_var)
        self.process_entry.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        self.start_button = ttk.Button(
            connection,
            text="开始采集",
            style="Accent.TButton",
            command=self._start_stream,
        )
        self.start_button.grid(row=2, column=2, padx=(0, 6), pady=(8, 0))
        self.stop_button = ttk.Button(
            connection, text="停止", command=self._stop_stream, state="disabled"
        )
        self.stop_button.grid(row=2, column=3, pady=(8, 0))

        filters = ttk.LabelFrame(self.root, text="日志筛选与操作", padding=8)
        filters.grid(row=2, column=0, sticky="ew", padx=14, pady=4)
        filters.columnconfigure(4, weight=1)

        self.show_info_var = tk.BooleanVar(value=True)
        self.show_warning_var = tk.BooleanVar(value=True)
        self.show_error_var = tk.BooleanVar(value=True)
        self.pause_var = tk.BooleanVar(value=False)
        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.filter_keyword_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.search_only_var = tk.BooleanVar(value=False)
        self.search_count_var = tk.StringVar(value="0 / 0")

        ttk.Checkbutton(
            filters,
            text="Info",
            variable=self.show_info_var,
            command=self._schedule_apply_filter,
        ).grid(row=0, column=0, padx=(0, 4))
        ttk.Checkbutton(
            filters,
            text="Warning",
            variable=self.show_warning_var,
            command=self._schedule_apply_filter,
        ).grid(row=0, column=1, padx=4)
        ttk.Checkbutton(
            filters,
            text="Error",
            variable=self.show_error_var,
            command=self._schedule_apply_filter,
        ).grid(row=0, column=2, padx=4)
        ttk.Label(filters, text="过滤关键字").grid(
            row=0, column=3, padx=(16, 6)
        )
        self.filter_keyword_combo = ttk.Combobox(
            filters,
            textvariable=self.filter_keyword_var,
            state="normal",
            height=10,
            values=self.config.filter_keyword_history(),
        )
        self.filter_keyword_combo.grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 12),
        )
        self.filter_keyword_combo.bind(
            "<KeyRelease>",
            self._on_filter_keyword_key_release,
        )
        self.filter_keyword_combo.bind(
            "<Return>",
            self._on_filter_keyword_enter,
        )
        self.filter_keyword_combo.bind(
            "<<ComboboxSelected>>",
            self._on_filter_keyword_selected,
        )
        self.filter_keyword_combo.bind(
            "<FocusOut>",
            self._on_filter_keyword_focus_out,
        )
        ttk.Label(filters, text="Ctrl+F 搜索", style="Hint.TLabel").grid(
            row=0, column=5, sticky="w", padx=(0, 8)
        )
        ttk.Checkbutton(
            filters,
            text="暂停显示",
            variable=self.pause_var,
            command=self._on_pause_changed,
        ).grid(row=0, column=6, padx=4)
        ttk.Checkbutton(
            filters, text="自动滚动", variable=self.auto_scroll_var
        ).grid(row=0, column=7, padx=4)
        ttk.Button(filters, text="清空视图", command=self._clear_view).grid(
            row=0, column=8, padx=4
        )
        ttk.Button(filters, text="导出当前视图", command=self._export_view).grid(
            row=0, column=9, padx=(4, 0)
        )
        filters.columnconfigure(4, weight=1)

        log_frame = ttk.Frame(self.root, padding=(14, 4, 14, 4))
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        self.search_frame = ttk.Frame(log_frame)
        self.search_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.search_frame.columnconfigure(1, weight=1)
        ttk.Label(self.search_frame, text="搜索").grid(
            row=0, column=0, padx=(0, 6)
        )
        self.search_entry = ttk.Entry(
            self.search_frame, textvariable=self.search_var
        )
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", self._on_search_text_changed)
        self.search_entry.bind("<Return>", self._on_search_next)
        self.search_entry.bind("<Shift-Return>", self._on_search_previous)
        self.search_entry.bind("<Escape>", self._on_search_escape)
        ttk.Label(
            self.search_frame,
            textvariable=self.search_count_var,
            width=12,
            anchor="center",
        ).grid(row=0, column=2, padx=(0, 6))
        self.prev_search_button = ttk.Button(
            self.search_frame,
            text="上一条",
            command=lambda: self._navigate_search(-1),
            state="disabled",
        )
        self.prev_search_button.grid(row=0, column=3, padx=(0, 4))
        self.next_search_button = ttk.Button(
            self.search_frame,
            text="下一条",
            command=lambda: self._navigate_search(1),
            state="disabled",
        )
        self.next_search_button.grid(row=0, column=4, padx=(0, 8))
        ttk.Checkbutton(
            self.search_frame,
            text="仅显示匹配",
            variable=self.search_only_var,
            command=self._on_search_only_changed,
        ).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(
            self.search_frame,
            text="关闭",
            command=self._close_search,
        ).grid(row=0, column=6)
        self.search_frame.grid_remove()

        self.log_text = tk.Text(
            log_frame,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
            background="#fbfbfb",
            foreground="#202124",
            insertbackground="#202124",
            borderwidth=1,
            relief="solid",
        )
        vertical = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        horizontal = ttk.Scrollbar(
            log_frame, orient="horizontal", command=self.log_text.xview
        )
        self.log_text.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        self.log_text.tag_configure("ERROR", foreground="#c62828")
        self.log_text.tag_configure("WARNING", foreground="#b35c00")
        self.log_text.tag_configure("INFO", foreground="#263238")
        self.log_text.tag_configure("search_match", background="#fff0a8")
        self.log_text.tag_configure(
            "search_current",
            background="#ffb74d",
            foreground="#202124",
        )
        self.log_text.tag_raise("search_match")
        self.log_text.tag_raise("search_current")

        footer = ttk.Frame(self.root, padding=(14, 2, 14, 8))
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        ttk.Label(
            footer,
            textvariable=self.status_var,
            style="Status.TLabel",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        self.count_var = tk.StringVar(value="显示 0 / 记录 0 · 丢弃 0")
        ttk.Label(footer, textvariable=self.count_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        self._update_controls()

    def _refresh_devices_async(self) -> None:
        if self.closing or self.refreshing_devices:
            return
        self.refreshing_devices = True
        self.device_request_id += 1
        request_id = self.device_request_id
        platform = self.platform
        threading.Thread(
            target=self._refresh_devices_worker,
            args=(platform, request_id),
            name="device-refresh",
            daemon=True,
        ).start()

    def _refresh_devices_worker(
        self,
        platform: Platform,
        request_id: int,
    ) -> None:
        try:
            devices = self.services[platform].list_devices()
            self.events.put(
                ("devices", platform, request_id, devices, "")
            )
        except Exception as exc:
            self.events.put(
                ("devices", platform, request_id, [], str(exc))
            )

    def _auto_refresh_devices(self) -> None:
        if self.closing:
            return
        self._refresh_devices_async()
        self.root.after(self.DEVICE_REFRESH_MS, self._auto_refresh_devices)

    def _on_platform_changed(self) -> None:
        try:
            platform = Platform(self.platform_var.get())
        except ValueError:
            return
        if platform is self.platform:
            return
        self._commit_filter_keyword_history(apply_filter=False)

        if self.running:
            self._stop_stream()
        self.detection_cancel_event.set()
        self.detecting_process = False
        self.apps_loading = False
        self.apps_request_id += 1
        self.refreshing_devices = False
        self.device_request_id += 1

        if self.search_visible:
            self._close_search()
        self.log_buffer.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.shown_count = 0

        self.platform = platform
        self.service = self.services[platform]
        self.selected_device_id = ""
        self.selected_app = None
        self.device_choice_map.clear()
        self.app_choice_map.clear()
        self.device_var.set("")
        self.device_combo.configure(values=[])
        self.app_var.set("")
        self.app_combo.configure(values=[])
        self.process_var.set("")
        self._update_platform_labels()
        self._update_counts()
        self._update_controls()
        self.status_var.set(f"已切换到 {platform.display_name}，正在查找设备…")
        self._refresh_devices_async()

    def _update_platform_labels(self) -> None:
        self.package_button.configure(
            text="选择 IPA" if self.platform is Platform.IOS else "选择 APK"
        )

    def _handle_devices(
        self,
        platform: Platform,
        request_id: int,
        devices: list[DeviceInfo],
        error: str,
    ) -> None:
        if (
            platform is not self.platform
            or request_id != self.device_request_id
        ):
            return
        self.refreshing_devices = False
        if error:
            self.status_var.set(error)
            if self.running:
                self._stop_stream()
            return

        self.device_choice_map = {device.choice_label: device for device in devices}
        labels = list(self.device_choice_map)
        self.device_combo.configure(values=labels)

        if not devices:
            self.device_var.set("")
            if self.selected_device_id:
                self.selected_device_id = ""
                self.selected_app = None
                self.app_var.set("")
                self.app_choice_map.clear()
                self.app_combo.configure(values=[])
                self.apps_request_id += 1
                self.apps_loading = False
            if self.running:
                self._stop_stream()
            if self.platform is Platform.IOS:
                self.status_var.set(
                    "未发现 iOS 设备。请连接数据线、解锁设备并确认已信任此电脑。"
                )
            else:
                self.status_var.set(
                    "未发现 Android 设备。请开启开发者选项和 USB 调试，"
                    "并在设备上允许此电脑进行调试。"
                )
            self._update_controls()
            return

        current = next(
            (
                device
                for device in devices
                if device.device_id == self.selected_device_id
            ),
            None,
        )
        if current is None:
            current = next((device for device in devices if device.connected), devices[0])
            changed = current.device_id != self.selected_device_id
            self._set_device(current, load_apps=changed)
        else:
            self.device_var.set(current.choice_label)
            if current.connected:
                self.status_var.set(
                    f"已连接：{current.name} · "
                    f"{current.platform.display_name} {current.os_version}"
                )
            else:
                self.status_var.set(current.detail or "设备连接异常。")
        self._update_controls()

    def _on_device_selected(self, _event: tk.Event[Any] | None = None) -> None:
        device = self.device_choice_map.get(self.device_var.get())
        if device is None or device.device_id == self.selected_device_id:
            return
        self._set_device(device, load_apps=True)

    def _set_device(self, device: DeviceInfo, load_apps: bool) -> None:
        if self.running:
            self._stop_stream()
        self.apps_request_id += 1
        self.apps_loading = False
        self.selected_device_id = device.device_id
        self.selected_app = None
        self.device_var.set(device.choice_label)
        self.app_var.set("")
        self.process_var.set("")
        self.app_choice_map.clear()
        self.app_combo.configure(values=[])
        if device.connected:
            self.status_var.set(
                f"已连接：{device.name} · "
                f"{device.platform.display_name} {device.os_version}"
            )
        else:
            self.status_var.set(device.detail or "设备连接异常。")
        if load_apps and device.connected:
            self._load_apps_async()
        self._update_controls()

    def _load_apps_async(self) -> None:
        if not self.selected_device_id:
            return
        device_id = self.selected_device_id
        platform = self.platform
        self.apps_request_id += 1
        request_id = self.apps_request_id
        self.apps_loading = True
        self.status_var.set("正在读取设备已安装 App…")
        self._update_controls()
        threading.Thread(
            target=self._load_apps_worker,
            args=(platform, device_id, request_id),
            name="device-app-list",
            daemon=True,
        ).start()

    def _load_apps_worker(
        self,
        platform: Platform,
        device_id: str,
        request_id: int,
    ) -> None:
        try:
            apps = self.services[platform].list_installed_apps(device_id)
            self.events.put(
                ("apps", platform, device_id, request_id, apps, "")
            )
        except Exception as exc:
            self.events.put(
                ("apps", platform, device_id, request_id, [], str(exc))
            )

    def _handle_apps(
        self,
        platform: Platform,
        device_id: str,
        request_id: int,
        apps: list[AppInfo],
        error: str,
    ) -> None:
        if (
            platform is not self.platform
            or device_id != self.selected_device_id
            or request_id != self.apps_request_id
        ):
            return
        self.apps_loading = False
        self._update_controls()
        if error:
            self.status_var.set(
                error
                + (
                    " 可直接选择 IPA，或手工填写 App 进程名。"
                    if self.platform is Platform.IOS
                    else " 可直接选择 APK，或手工填写 Android 包名。"
                )
            )
            return

        self.app_choice_map = {app.label: app for app in apps}
        labels = list(self.app_choice_map)
        self.app_combo.configure(values=labels)
        if not apps:
            self.status_var.set(
                "设备未返回可筛选的 App，可选择 IPA 或手工填写进程名。"
                if self.platform is Platform.IOS
                else "设备未返回第三方 App，可选择 APK 或手工填写 Android 包名。"
            )
            return

        last_app_id = self.config.last_app_id(self.platform)
        selected = next(
            (app for app in apps if app.app_id == last_app_id),
            None,
        )
        if selected is not None:
            self.app_var.set(selected.label)
            self._apply_selected_app(selected)
        self.status_var.set(f"已读取 {len(apps)} 个已安装 App。")
        self._update_controls()

    def _on_app_selected(self, _event: tk.Event[Any] | None = None) -> None:
        app = self.app_choice_map.get(self.app_var.get())
        if app is None:
            return
        if self.running:
            self._stop_stream()
        self._apply_selected_app(app)

    def _apply_selected_app(self, app: AppInfo) -> None:
        self.selected_app = app
        self.config.set_last_app_id(self.platform, app.app_id)
        process_name = app.process_name or self.config.process_for(
            self.platform,
            app.app_id,
        )
        self.process_var.set(process_name)
        if not process_name:
            package_action = (
                "选择 IPA/手工填写进程名"
                if self.platform is Platform.IOS
                else "选择 APK/手工填写 Android 包名"
            )
            self.status_var.set(
                f"已选择 {app.display_name}。请点击“识别进程”，"
                f"或{package_action}。"
            )
        else:
            self.status_var.set(
                f"目标 App：{app.display_name} · 进程：{process_name}"
            )
        self._save_config_quietly()
        self._update_controls()

    def _choose_app_package(self) -> None:
        file_type = "IPA" if self.platform is Platform.IOS else "APK"
        last_package = self.config.package_path(self.platform)
        initial_dir = (
            str(Path(last_package).parent)
            if last_package
            else str(self.base_dir)
        )
        path = filedialog.askopenfilename(
            title=f"选择 Unity 打包的 {file_type}",
            initialdir=initial_dir,
            filetypes=[
                (
                    "iOS App 包" if self.platform is Platform.IOS else "Android App 包",
                    "*.ipa" if self.platform is Platform.IOS else "*.apk",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            if self.platform is Platform.IOS:
                app = inspect_ipa(path)
            else:
                android_service = self.services[Platform.ANDROID]
                if not isinstance(android_service, AndroidDeviceService):
                    raise ApkInspectionError("Android 服务未正确初始化。")
                app = inspect_apk(android_service.aapt_path, path)
        except (IpaInspectionError, ApkInspectionError) as exc:
            messagebox.showerror(f"{file_type} 解析失败", str(exc))
            return

        if self.running:
            self._stop_stream()
        self.selected_app = app
        label = app.label
        self.app_choice_map[label] = app
        values = list(self.app_combo.cget("values"))
        if label not in values:
            values.append(label)
        self.app_combo.configure(values=values)
        self.app_var.set(label)
        self.process_var.set(app.process_name)
        self.config.set_package_path(self.platform, str(Path(path).resolve()))
        self.config.set_last_app_id(self.platform, app.app_id)
        self.config.set_process(self.platform, app.app_id, app.process_name)
        self._save_config_quietly()
        self.status_var.set(
            f"{file_type} 已解析：{app.display_name} · {app.app_id} · "
            f"进程 {app.process_name}"
        )
        self._update_controls()

    def _detect_process_async(self) -> None:
        app = self.selected_app
        device_id = self.selected_device_id
        if app is None or not device_id:
            messagebox.showwarning("无法识别进程", "请先选择设备和目标 App。")
            return
        if self.running:
            self._stop_stream()
        self.detecting_process = True
        self.detection_cancel_event = threading.Event()
        cancel_event = self.detection_cancel_event
        self._update_controls()
        self.status_var.set("正在识别进程，请确认目标 App 已启动并在前台运行…")
        threading.Thread(
            target=self._detect_process_worker,
            args=(self.platform, device_id, app, cancel_event),
            name="device-process-detection",
            daemon=True,
        ).start()

    def _detect_process_worker(
        self,
        platform: Platform,
        device_id: str,
        app: AppInfo,
        cancel_event: threading.Event,
    ) -> None:
        try:
            candidates = self.services[platform].detect_process_candidates(
                device_id,
                app,
                cancel_event=cancel_event,
            )
            self.events.put(
                ("processes", platform, device_id, app, candidates, "")
            )
        except Exception as exc:
            self.events.put(
                ("processes", platform, device_id, app, [], str(exc))
            )

    def _handle_process_candidates(
        self,
        platform: Platform,
        device_id: str,
        app: AppInfo,
        candidates: list[str],
        error: str,
    ) -> None:
        self.detecting_process = False
        self._update_controls()
        if platform is not self.platform or device_id != self.selected_device_id:
            return
        if error:
            self.status_var.set(error)
            return
        if not candidates:
            file_type = "IPA" if self.platform is Platform.IOS else "APK"
            self.status_var.set(
                f"未识别到目标进程。请先启动 App 后重试，或选择 {file_type}。"
            )
            messagebox.showinfo(
                "未识别到进程",
                "请确认目标 App 已安装并正在运行，然后重新点击“识别进程”。\n"
                + (
                    "也可以选择 IPA 或直接填写进程名。"
                    if self.platform is Platform.IOS
                    else "也可以选择 APK 或直接填写 Android 包名。"
                ),
            )
            return
        if len(candidates) == 1:
            selected = candidates[0]
        else:
            dialog = ProcessSelectionDialog(self.root, app, candidates)
            self.root.wait_window(dialog)
            selected = dialog.selected
        if not selected:
            self.status_var.set("已取消进程选择。")
            return
        self.process_var.set(selected)
        self.config.set_process(self.platform, app.app_id, selected)
        self._save_config_quietly()
        self.status_var.set(f"已确认目标进程：{selected}")
        self._update_controls()

    def _start_stream(self) -> None:
        if self.running:
            return
        missing = self.service.missing_tools()
        if missing:
            messagebox.showerror("运行文件缺失", "缺少：" + "、".join(missing))
            return
        if not self.selected_device_id:
            messagebox.showwarning(
                "未连接设备",
                f"请先连接并选择 {self.platform.display_name} 设备。",
            )
            return
        process_name = self.process_var.get().strip()
        if not process_name:
            messagebox.showwarning(
                "未设置进程名",
                (
                    "请选择 IPA、从设备 App 列表识别进程，或手工填写 App 进程名。"
                    if self.platform is Platform.IOS
                    else "请选择 APK、从设备 App 列表选择包名，或手工填写 Android 包名。"
                ),
            )
            return

        if self.selected_app is not None:
            self.config.set_last_app_id(self.platform, self.selected_app.app_id)
            self.config.set_process(
                self.platform,
                self.selected_app.app_id,
                process_name,
            )
            self._save_config_quietly()

        self.session_id += 1
        session_id = self.session_id
        app_id = self.selected_app.app_id if self.selected_app else process_name
        safe_app_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", app_id)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_path = (
            self.logs_dir
            / f"{self.platform.value}_{stamp}_{safe_app_id}.log"
        )
        try:
            self.session_file = self.session_path.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            messagebox.showerror("无法创建日志文件", str(exc))
            return

        self.session_line_count = 0
        self.session_tail.clear()
        self.stop_requested = False
        try:
            self.stream = self.service.start_log_stream(
                self.selected_device_id,
                self.selected_app,
                process_name,
                on_line=lambda line: self.events.put(("log", session_id, line)),
                on_end=lambda code: self.events.put(("stream_end", session_id, code)),
                on_status=lambda text: self.events.put(
                    ("stream_status", session_id, text)
                ),
            )
        except Exception as exc:
            self._close_session_file()
            message = str(exc)
            self.status_var.set(message)
            messagebox.showerror("启动日志采集失败", message)
            return

        self.running = True
        self.status_var.set(
            f"正在采集 {process_name} · 日志文件：{self.session_path.name}"
        )
        self._update_controls()
        self.root.after(6_000, lambda: self._check_first_log(session_id))

    def _check_first_log(self, session_id: int) -> None:
        if (
            not self.closing
            and self.running
            and session_id == self.session_id
            and self.session_line_count == 0
        ):
            if self.platform is Platform.IOS:
                self.status_var.set(
                    "日志流已启动，但暂未收到日志。"
                    "请确认 App 正在运行并使用 Development Build。"
                )
            else:
                self.status_var.set(
                    "日志流已启动，但暂未收到日志。"
                    "请确认目标 Android App 正在运行，且包名与进程名一致。"
                )

    def _stop_stream(self) -> None:
        if self.stream is None:
            return
        self.stop_requested = True
        self.status_var.set("正在停止日志采集…")
        try:
            self.stream.stop()
        except Exception:
            pass
        self.stream = None
        self.running = False
        self._close_session_file()
        self.status_var.set("日志采集已停止。")
        self._update_controls()

    def _handle_stream_end(self, session_id: int, return_code: int) -> None:
        if session_id != self.session_id:
            return
        was_stopping = self.stop_requested
        self.stream = None
        self.running = False
        self._close_session_file()
        if not was_stopping and not self.closing:
            if return_code == 0:
                self.status_var.set("日志流已结束，设备可能已断开连接。")
            else:
                detail = self.session_tail[-1] if self.session_tail else ""
                if self.platform is Platform.IOS:
                    self.status_var.set(
                        humanize_tool_error(detail) + f"（退出码 {return_code}）"
                    )
                else:
                    self.status_var.set(
                        "Android 日志流已结束，请检查 USB 调试连接。"
                        f"（退出码 {return_code}）"
                    )
        self._update_controls()

    def _close_session_file(self) -> None:
        if self.session_file is not None:
            try:
                self.session_file.close()
            except OSError:
                pass
            self.session_file = None

    def _poll_events(self) -> None:
        if self.closing:
            return
        new_entries: list[LogEntry] = []
        processed = 0
        current_filter = self._current_filter()
        while processed < 2_000:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            kind = event[0]
            if kind == "log":
                session_id, raw_line = event[1], event[2]
                if session_id != self.session_id:
                    continue
                raw = str(raw_line).rstrip("\r\n")
                if not raw:
                    continue
                if raw.startswith("[connected:"):
                    self.status_var.set("日志流已连接，等待目标 App 输出。")
                    continue
                if raw.startswith("[disconnected:"):
                    self.status_var.set("设备已断开，等待日志进程退出。")
                    continue
                if self.platform is Platform.ANDROID:
                    entry = parse_android_log_line(
                        raw,
                        self.process_var.get().strip(),
                    )
                else:
                    entry = parse_log_line(raw, self.process_var.get().strip())
                self.log_buffer.add(entry)
                self.session_line_count += 1
                self.session_tail.append(raw)
                if self.session_file is not None:
                    try:
                        self.session_file.write(raw + "\n")
                    except OSError:
                        self._close_session_file()
                        self.status_var.set("会话日志文件写入失败，界面采集仍在继续。")
                if not self.pause_var.get() and current_filter.matches(entry):
                    new_entries.append(entry)
            elif kind == "devices":
                self._handle_devices(
                    event[1],
                    event[2],
                    event[3],
                    event[4],
                )
            elif kind == "apps":
                self._handle_apps(
                    event[1],
                    event[2],
                    event[3],
                    event[4],
                    event[5],
                )
            elif kind == "processes":
                self._handle_process_candidates(
                    event[1],
                    event[2],
                    event[3],
                    event[4],
                    event[5],
                )
            elif kind == "stream_status":
                if event[1] == self.session_id and self.running:
                    self.status_var.set(str(event[2]))
            elif kind == "stream_end":
                self._handle_stream_end(event[1], event[2])

        if new_entries:
            self._append_entries(new_entries)
            if self.search_visible:
                self._schedule_search_refresh(
                    reset_current=False,
                    scroll_to_match=False,
                )
        self._update_counts()
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)

    def _append_entries(self, entries: list[LogEntry]) -> None:
        self.log_text.configure(state="normal")
        for entry in entries:
            self.log_text.insert("end", entry.display_line + "\n", (entry.level,))
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.MAX_TEXT_LINES:
            remove_to = line_count - self.MAX_TEXT_LINES
            self.log_text.delete("1.0", f"{remove_to + 1}.0")
        self.log_text.configure(state="disabled")
        self.shown_count = min(
            self.MAX_TEXT_LINES,
            self.shown_count + len(entries),
        )
        if self.auto_scroll_var.get():
            self.log_text.see("end")

    def _render_filtered(self) -> None:
        entries = self.log_buffer.filtered(self._current_filter())
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.shown_count = 0
        if entries:
            self._append_entries(entries)
        self._update_counts()
        if self.search_visible:
            self._refresh_search_matches(reset_current=True, scroll_to_match=True)

    def _schedule_apply_filter(self) -> None:
        if self.filter_job is not None:
            try:
                self.root.after_cancel(self.filter_job)
            except tk.TclError:
                pass
        self.filter_job = self.root.after(220, self._apply_filter)

    def _apply_filter(self) -> None:
        self.filter_job = None
        self._render_filtered()

    def _on_filter_keyword_enter(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> str:
        self._commit_filter_keyword_history(apply_filter=True)
        return "break"

    def _on_filter_keyword_key_release(
        self,
        event: tk.Event[Any],
    ) -> None:
        if event.keysym not in {"Return", "KP_Enter"}:
            self._schedule_apply_filter()

    def _on_filter_keyword_selected(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        self._commit_filter_keyword_history(apply_filter=True)

    def _on_filter_keyword_focus_out(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        self._commit_filter_keyword_history(apply_filter=True)

    def _commit_filter_keyword_history(self, apply_filter: bool) -> None:
        raw_keyword = self.filter_keyword_var.get()
        keyword = raw_keyword.strip()
        if keyword and keyword != raw_keyword:
            self.filter_keyword_var.set(keyword)
        changed = self.config.add_filter_keyword_history(keyword)
        self.filter_keyword_combo.configure(
            values=self.config.filter_keyword_history()
        )
        if changed:
            self._save_config_quietly()
        if not apply_filter:
            return
        if self.filter_job is not None:
            try:
                self.root.after_cancel(self.filter_job)
            except tk.TclError:
                pass
            self.filter_job = None
        self._apply_filter()

    def _on_pause_changed(self) -> None:
        if not self.pause_var.get():
            self._render_filtered()

    def _on_find_shortcut(self, _event: tk.Event[Any] | None = None) -> str:
        self._show_search()
        return "break"

    def _show_search(self) -> None:
        self.search_frame.grid()
        self.search_visible = True
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        if self._search_query():
            self._refresh_search_matches(reset_current=True, scroll_to_match=True)
        else:
            self._clear_search_state()

    def _close_search(self) -> None:
        if self.search_job is not None:
            try:
                self.root.after_cancel(self.search_job)
            except tk.TclError:
                pass
            self.search_job = None
        was_only_matches = self.search_only_var.get()
        self.search_only_var.set(False)
        self.search_frame.grid_remove()
        self.search_visible = False
        self._clear_search_state()
        if was_only_matches:
            self._render_filtered()
        self.root.focus_set()

    def _on_search_escape(self, _event: tk.Event[Any] | None = None) -> str:
        self._close_search()
        return "break"

    def _on_root_escape(self, _event: tk.Event[Any] | None = None) -> str | None:
        if not self.search_visible:
            return None
        self._close_search()
        return "break"

    def _on_search_text_changed(self, _event: tk.Event[Any] | None = None) -> None:
        if not self.search_visible:
            return
        if self.search_only_var.get():
            self._schedule_apply_filter()
        else:
            self._schedule_search_refresh(
                reset_current=True,
                scroll_to_match=True,
            )

    def _on_search_next(self, _event: tk.Event[Any] | None = None) -> str:
        self._navigate_search(1)
        return "break"

    def _on_search_previous(self, _event: tk.Event[Any] | None = None) -> str:
        self._navigate_search(-1)
        return "break"

    def _on_search_only_changed(self) -> None:
        if self.filter_job is not None:
            try:
                self.root.after_cancel(self.filter_job)
            except tk.TclError:
                pass
            self.filter_job = None
        self._apply_filter()
        self.search_entry.focus_set()

    def _schedule_search_refresh(
        self,
        reset_current: bool,
        scroll_to_match: bool,
    ) -> None:
        if not self.search_visible:
            return
        if self.search_job is not None:
            if not reset_current:
                return
            try:
                self.root.after_cancel(self.search_job)
            except tk.TclError:
                pass
        self.search_job = self.root.after(
            200,
            lambda: self._run_scheduled_search(reset_current, scroll_to_match),
        )

    def _run_scheduled_search(
        self,
        reset_current: bool,
        scroll_to_match: bool,
    ) -> None:
        self.search_job = None
        self._refresh_search_matches(
            reset_current=reset_current,
            scroll_to_match=scroll_to_match,
        )

    def _refresh_search_matches(
        self,
        reset_current: bool = False,
        scroll_to_match: bool = False,
    ) -> None:
        if not self.search_visible:
            return
        if self.search_job is not None:
            try:
                self.root.after_cancel(self.search_job)
            except tk.TclError:
                pass
            self.search_job = None
        query = self._search_query()
        previous_start = "" if reset_current else self.search_current_start
        self._clear_search_state()
        if not query:
            return

        start_index = "1.0"
        stop_index = self.log_text.index("end-1c")
        ranges: list[tuple[str, str]] = []
        while True:
            match_start = self.log_text.search(
                query,
                start_index,
                stopindex=stop_index,
                nocase=True,
            )
            if not match_start:
                break
            match_end = f"{match_start}+{len(query)}c"
            ranges.append((match_start, match_end))
            start_index = match_end

        self.search_match_ranges = ranges
        self.search_applied_query = query
        if not ranges:
            return
        for range_start, range_end in ranges:
            self.log_text.tag_add("search_match", range_start, range_end)

        selected_index = 0
        if previous_start:
            for index, (range_start, _range_end) in enumerate(ranges):
                if self.log_text.compare(range_start, ">=", previous_start):
                    selected_index = index
                    break
        self._select_search_match(selected_index, scroll=scroll_to_match)

    def _navigate_search(self, delta: int) -> None:
        query = self._search_query()
        if self.search_only_var.get() and (
            self.filter_job is not None or self.search_applied_query != query
        ):
            if self.filter_job is not None:
                try:
                    self.root.after_cancel(self.filter_job)
                except tk.TclError:
                    pass
                self.filter_job = None
            self._render_filtered()
            if not self.search_match_ranges:
                return
            if delta < 0:
                self._select_search_match(
                    len(self.search_match_ranges) - 1,
                    scroll=True,
                )
            return
        if not self.search_match_ranges or self.search_applied_query != query:
            self._refresh_search_matches(
                reset_current=False,
                scroll_to_match=True,
            )
            if not self.search_match_ranges:
                return
            if delta < 0:
                self._select_search_match(
                    len(self.search_match_ranges) - 1,
                    scroll=True,
                )
            return
        if self.search_current_index < 0:
            selected_index = 0 if delta > 0 else len(self.search_match_ranges) - 1
        else:
            selected_index = (
                self.search_current_index + delta
            ) % len(self.search_match_ranges)
        self._select_search_match(selected_index, scroll=True)

    def _select_search_match(self, index: int, scroll: bool) -> None:
        if not self.search_match_ranges or index < 0:
            return
        index %= len(self.search_match_ranges)
        self.log_text.tag_remove("search_current", "1.0", "end")
        range_start, range_end = self.search_match_ranges[index]
        self.log_text.tag_add("search_current", range_start, range_end)
        self.log_text.tag_raise("search_current")
        self.search_current_index = index
        self.search_current_start = range_start
        self.search_count_var.set(f"{index + 1} / {len(self.search_match_ranges)}")
        self.prev_search_button.configure(state="normal")
        self.next_search_button.configure(state="normal")
        if scroll:
            self.log_text.see(range_start)

    def _clear_search_state(self) -> None:
        self.log_text.tag_remove("search_match", "1.0", "end")
        self.log_text.tag_remove("search_current", "1.0", "end")
        self.search_match_ranges = []
        self.search_current_index = -1
        self.search_current_start = ""
        self.search_applied_query = ""
        self.search_count_var.set("0 / 0")
        self.prev_search_button.configure(state="disabled")
        self.next_search_button.configure(state="disabled")

    def _search_query(self) -> str:
        return self.search_var.get().strip()

    def _clear_view(self) -> None:
        self.log_buffer.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.shown_count = 0
        self.status_var.set("视图与内存日志已清空，当前会话原始日志文件仍继续记录。")
        self._update_counts()
        if self.search_visible:
            self._refresh_search_matches(reset_current=True, scroll_to_match=False)

    def _export_view(self) -> None:
        entries = self.log_buffer.filtered(self._current_filter())
        if not entries:
            messagebox.showinfo("没有可导出的日志", "当前筛选结果为空。")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="导出当前筛选日志",
            defaultextension=".txt",
            initialfile=f"unity_{self.platform.value}_log_{stamp}.txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(
                "\n".join(entry.export_line for entry in entries) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status_var.set(f"已导出 {len(entries)} 条日志：{path}")

    def _current_filter(self) -> LogFilter:
        return LogFilter(
            show_info=self.show_info_var.get(),
            show_warning=self.show_warning_var.get(),
            show_error=self.show_error_var.get(),
            keyword=self.filter_keyword_var.get().strip(),
            search_keyword=(
                self._search_query()
                if self.search_visible and self.search_only_var.get()
                else ""
            ),
        )

    def _update_counts(self) -> None:
        self.count_var.set(
            f"显示 {self.shown_count} / 记录 {self.log_buffer.total_received} "
            f"· 丢弃 {self.log_buffer.dropped}"
        )

    def _update_controls(self) -> None:
        has_device = bool(self.selected_device_id)
        has_process = bool(self.process_var.get().strip())
        has_tools = not self.service.missing_tools()
        state = "disabled" if self.running else "normal"
        self.device_combo.configure(state="disabled" if self.running else "readonly")
        self.app_combo.configure(state="disabled" if self.running else "readonly")
        self.package_button.configure(state=state if has_tools else "disabled")
        self.load_apps_button.configure(
            state=(
                state
                if has_device and has_tools and not self.apps_loading
                else "disabled"
            )
        )
        self.detect_button.configure(
            state=(
                state
                if self.selected_app is not None
                and has_device
                and has_tools
                and not self.detecting_process
                else "disabled"
            )
        )
        self.process_entry.configure(state="disabled" if self.running else "normal")
        self.start_button.configure(
            state="normal" if has_device and has_process and has_tools and not self.running else "disabled"
        )
        self.stop_button.configure(state="normal" if self.running else "disabled")

    def _save_config_quietly(self) -> None:
        try:
            self.config.save()
        except OSError:
            self.status_var.set("配置保存失败，但当前操作可以继续。")

    def _on_close(self) -> None:
        if self.closing:
            return
        self._commit_filter_keyword_history(apply_filter=False)
        self.closing = True
        self.detection_cancel_event.set()
        if self.search_job is not None:
            try:
                self.root.after_cancel(self.search_job)
            except tk.TclError:
                pass
            self.search_job = None
        if self.stream is not None:
            self.stop_requested = True
            try:
                self.stream.stop()
            except Exception:
                pass
            self.stream = None
        self._close_session_file()
        self.root.destroy()
