"""Windows application entry point."""

import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from qq_downloader.music import MusicService
from qq_downloader.storage import DATA, Storage
from qq_downloader.theme import STYLE
from qq_downloader.window import Window


def create_app():
    app = QApplication(sys.argv)
    app.setApplicationName("QQ 音乐下载器")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def main():
    app = create_app()
    DATA.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DATA / "running.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "QQ 音乐下载器", "程序已在运行。请切换到已打开的窗口。")
        return 0
    storage = Storage()
    window = Window(storage, MusicService(storage))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
