# Unity USB 日志筛选器

在 Windows 上通过 USB 读取 iOS 或 Android 设备中已安装 Unity App 的实时日志，并按目标 App 进程筛选。iOS 和 Android 共用设备区、日志显示、级别过滤、常驻关键字过滤、`Ctrl+F` 搜索、暂停、自动滚动和导出功能。

## 环境要求

- Windows 10/11 x64
- Python 3.11（当前机器已安装 3.11.9）
- 数据线支持数据传输

iOS：

- Apple Devices 或 iTunes 自带的 Apple Mobile Device USB 驱动
- iOS 15 及以上
- 第一次连接时解锁设备并点击“信任此电脑”

Android：

- Android 7.0/API 24 及以上
- 开启开发者选项和 USB 调试
- 连接后允许此电脑的 RSA 调试密钥
- Windows 能识别设备的 ADB USB 接口；部分手机仍需厂商 USB 驱动

## 启动

双击 `run.bat`，或在当前目录执行：

```powershell
python app.py
```

项目只使用 Python 标准库，不需要执行 `pip install`。ADB、AAPT 和 iOS 工具均已随目录携带。

## 使用步骤

1. 用数据线连接设备并完成授权。
2. 在窗口右上角选择 `iOS` 或 `Android`。
3. 在“设备”下拉框中选择目标设备；工具每 3 秒刷新连接状态。
4. 确定目标 App：
   - iOS：选择 IPA，读取 Bundle ID 和 `CFBundleExecutable`；或从已安装 App 列表选择。
   - Android：从第三方包列表选择包名，或选择 APK 解析包名和展示名称。
   - 无法自动确定进程时，先启动 App，再点击“识别进程”，也可手工填写。
5. 点击“开始采集”，然后启动或操作 Unity App。
6. 使用级别勾选、常驻“过滤关键字”、`Ctrl+F` 搜索、暂停显示和自动滚动控制界面。

Android 采集通过目标 PID 执行：

```text
adb logcat -v threadtime --pid=<PID>
```

采集期间每秒检查目标进程。App 退出后会进入等待状态，重新启动或 PID 变化时自动恢复采集。

## 日志过滤与搜索

- “过滤关键字”始终可见，持续控制基础日志范围和导出范围。
- 过滤关键字下拉框保存双平台共用的最近 10 条记录；按 `Enter`、选择历史项、离开输入框或关闭工具时提交。
- 历史记录忽略空值、不区分大小写去重，最近使用的关键字排在最前。
- 按 `Ctrl+F` 调出搜索栏，搜索栏默认隐藏。
- 搜索不区分大小写，在基础级别和过滤关键字筛选后的可见日志中查找。
- 使用“上一条”/“下一条”循环定位；`Enter` 下一条，`Shift+Enter` 上一条。
- 勾选“仅显示匹配”后，视图和导出同时满足级别、过滤关键字和搜索文本条件。
- 按 `Esc` 关闭搜索后，只移除搜索高亮和附加搜索过滤，常驻过滤关键字继续生效。

## 日志文件

- 每次开始采集都会在 `logs/` 创建 `ios_时间_<AppID>.log` 或 `android_时间_<包名>.log`。
- 会话文件持续记录目标进程原始日志，不受界面级别、关键字或搜索筛选影响。
- “清空视图”只清空界面与内存记录，不删除当前会话原始文件。
- 界面内存最多保留 50,000 条日志，超出后状态栏显示丢弃数量。
- “导出当前视图”导出当前级别、过滤关键字和搜索筛选后的日志。
- 切换 iOS/Android 平台会停止当前采集并清空当前内存日志，已写入的会话文件保留。

## 常见问题

### 未发现 iOS 设备

- 解锁设备并确认点击“信任此电脑”。
- 安装或修复 Apple Devices/iTunes，确认 Apple Mobile Device Service 正常运行。
- 重新插拔设备并刷新。

### 未发现 Android 设备

- 确认开发者选项和 USB 调试已开启。
- 解锁设备并允许此电脑的 RSA 调试密钥。
- 检查数据线是否支持数据传输，必要时安装手机厂商 USB 驱动。
- 执行 `tools\android-platform-tools\platform-tools\adb.exe devices -l` 检查设备状态。

### Android 显示 unauthorized

设备没有授权当前电脑。解锁设备，在 USB 调试授权弹窗中点击“允许”；如未出现，撤销 USB 调试授权后重新插拔。

### Android 开始采集但没有日志

- 确认目标 App 正在运行。
- 确认包名/进程名正确；Unity 主进程通常与包名一致。
- 使用 Development Build 后重新安装测试。

### 识别不到进程

- iOS 优先选择 IPA，Android 优先选择 APK。
- 先启动目标 App，再点击“识别进程”。
- 仍无法识别时可手工填写进程名或 Android 包名。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖 IPA/APK 解析、iOS/Android 设备与 App 解析、ADB 命令、Android PID 自动续采、日志级别、关键字过滤、搜索导航、配置迁移和 GUI 平台切换。

## 已知边界

- V1 不负责安装 IPA/APK、自动启动 App、无线 ADB、崩溃转储或系统诊断包。
- 同一时间只采集一个平台的一台设备和一个目标 App 进程。
- Android 使用 PID 过滤，要求 Android 7.0/API 24 及以上。
- iOS 日志依赖 unified log/syslog 服务；Android 日志依赖 ADB 与设备 USB 调试授权。

第三方组件来源、版本、哈希和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
