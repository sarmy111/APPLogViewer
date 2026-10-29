# Third-Party Notices

本工具通过独立子进程调用随目录分发的 libimobiledevice、Android Platform Tools 和 AAPT。随包的二进制文件未被本工具修改。

## Bundled runtime

- Distribution repository: <https://github.com/raise-isayan/libimobiledevice-dist>
- Release: `v2026.5`
- Asset: `libimobiledevice-win64.zip`
- Download: <https://github.com/raise-isayan/libimobiledevice-dist/releases/download/v2026.5/libimobiledevice-win64.zip>
- SHA-256: `02CED638CC2CDFC55A70D725795F8ADEC775A7D45FF68F29E478B0092A57282E`
- Runtime directory: `tools/`

### Android Platform Tools

- Project: Android Open Source Project
- Version: `37.0.1`
- Asset: `platform-tools_r37.0.1-win.zip`
- Download: <https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip>
- Archive SHA-256: `45F4D63113E895EBDE0C90F194099A4676B6AC653BD28D54314A9E022BBC1A99`
- `adb.exe` SHA-256: `B4A6B455702684652CCCF7B46258B29E653538904359A58FD4931CF3EF286B3F`
- Runtime directory: `tools/android-platform-tools/platform-tools/`
- License: Apache License 2.0
- License text: `tools/android-platform-tools/platform-tools/NOTICE.txt`

### Android Asset Packaging Tool

- Project: Android Open Source Project
- Version: Android SDK Build-Tools `35.0.0`
- `aapt.exe` SHA-256: `DB0BA2050B8F6B37185D2BA458D6E25B565AEFA3F3B96040ADF0A82C3469CE3C`
- Runtime directory: `tools/android-build-tools/`
- License: Apache License 2.0
- License text: `tools/android-build-tools/NOTICE.txt`

Release 声明的构建版本：

- libimobiledevice 1.4.0
- libplist 2.7.0
- libimobiledevice-glue 1.3.2
- libusbmuxd 2.1.1
- usbmuxd 1.1.1
- ideviceinstaller 1.2.0
- libirecovery 1.3.1
- libtatsu 1.0.5
- libideviceactivation 1.1.1
- idevicerestore 1.0.0
- ifuse 1.2.0

## Licenses

- `idevicesyslog`、`ideviceinstaller` 等工具程序：GNU GPL v2。
- `libimobiledevice`、`libplist`、`libusbmuxd`、`libimobiledevice-glue`、`libirecovery`、`libtatsu`、`libideviceactivation`：GNU LGPL v2.1。
- MinGW `libgcc_s` 和 `libstdc++` 运行库：GPL v2，附带 GCC Runtime Library Exception 3.1。
- MinGW-w64 `libwinpthread` 运行库：Zope Public License 2.1。
- `zlib1.dll`：zlib License。
- Android Platform Tools 和 AAPT：Apache License 2.0。

随工具附带的许可证文本：

- `licenses/GPL-2.0.txt`
- `licenses/LGPL-2.1.txt`
- `licenses/GCC-Runtime-Library-Exception-3.1.txt`
- `licenses/Mingw-w64-COPYING.txt`
- `licenses/Zlib.txt`

对应上游源代码：

- <https://github.com/libimobiledevice/libimobiledevice>
- <https://github.com/libimobiledevice/libplist>
- <https://github.com/libimobiledevice/libusbmuxd>
- <https://github.com/libimobiledevice/libimobiledevice-glue>
- <https://github.com/libimobiledevice/ideviceinstaller>
- <https://github.com/libimobiledevice/libirecovery>
- <https://github.com/libimobiledevice/libtatsu>
- <https://github.com/libimobiledevice/libideviceactivation>
- <https://android.googlesource.com/platform/packages/modules/adb/>
- <https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/tools/aapt/>

如需再分发此工具目录，请保留本文件、许可证文本和上游来源信息，并自行确认所在组织对 GPL/LGPL 二进制再分发的合规要求。
