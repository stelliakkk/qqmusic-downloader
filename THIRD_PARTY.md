# 第三方组件和对应源码

第二版使用 QQMusicApi（GPL-3.0-or-later）和 miniaudio（MIT），桌面界面使用 PySide6 与 Qt。各组件保持其原许可证。应用源码按 GPL-3.0-or-later 提供，许可证全文见 `LICENSE`。

- 对应项目源码和构建脚本见分发包的 `source/project`。`requirements.lock` 记录构建所用的 Python 依赖版本。
- `source/upstream` 包含对应版本的 QQMusicApi、miniaudio、PySide、Qt Base、Qt SVG、CPython、OpenSSL 以及锁定的 Python 依赖源码。`manifest.json` 记录下载地址和 SHA-256。
- Qt 动态库位于 `_internal/PySide6`。可按随包的 Qt 和 PySide 源码构建兼容库；Qt 的许可文本位于对应源码归档中。
- `licenses` 收录构建环境中随第三方包提供的许可证文件。其余组件原许可随各自的上游源码保留。

上游项目：[QQMusicApi](https://github.com/L-1124/QQMusicApi) · [pyminiaudio](https://github.com/irmen/pyminiaudio) · [Qt](https://download.qt.io/official_releases/qt/) · [PySide](https://download.qt.io/official_releases/QtForPython/pyside6/) · [CPython](https://www.python.org/) · [PyInstaller](https://pyinstaller.org/)。
