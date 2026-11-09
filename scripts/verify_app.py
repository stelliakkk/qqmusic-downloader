"""Exercise normal desktop controls with the actual QQ Music account."""

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from qq_downloader.music import MusicService
from qq_downloader.storage import Storage
from qq_downloader.window import Window
from qq_music_app import create_app


def wait_until(condition, description, timeout=55):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise TimeoutError(description)
        QApplication.processEvents()
        time.sleep(0.025)


def main():
    (ROOT / "reports").mkdir(exist_ok=True)
    app = create_app()
    storage = Storage()
    window = Window(storage, MusicService(storage))
    window.folder = ROOT / "reports" / "audio"
    window.update_folder()
    window.show()
    report = {"version": "1.0.0", "source_ui": "started"}
    try:
        assert storage.load_credential() is not None
        QTest.qWait(200)
        assert window.grab().save(str(ROOT / "reports" / "UI-start.png"))
        window.query.setText("焦虑型依恋")
        QTest.keyClick(window.query, Qt.Key.Key_Return)
        wait_until(lambda: window.task is None, "Search did not finish")
        assert window.songs, window.empty_help.text()
        row = next(i for i, song in enumerate(window.songs) if song.mid == "002Z0HkT2YoCvz")
        rect = window.table.visualItemRect(window.table.item(row, 0))
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        wait_until(lambda: window.task is None, "Quality lookup did not finish")
        assert window.qualities, window.notice.text()
        report["song"] = window.song.name
        report["artists"] = window.song.artists
        report["available_qualities"] = [quality.name for quality in window.qualities]
        report["search_results"] = len(window.songs)
        QTest.qWait(120)
        assert window.grab().save(str(ROOT / "reports" / "UI-results.png"))

        for extension in (".flac", ".mp3"):
            available = next(i for i, quality in enumerate(window.qualities, 1) if quality.file_type.e == extension)
            window.quality.setCurrentIndex(available)
            selected = window.quality.currentData().name
            QTest.mouseClick(window.download_button, Qt.MouseButton.LeftButton)
            wait_until(lambda: window.task is None, "Download did not finish", timeout=90)
            assert window.notice.property("tone") == "success", window.notice.text()
            output = Path(window.notice.toolTip())
            assert output.suffix == extension and output.is_file()
            report[extension[1:]] = {
                "file": output.name, "quality": selected, "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "full_decode": True, "detail": window.notice.text(),
            }
            if extension == ".flac":
                QTest.qWait(120)
                assert window.grab().save(str(ROOT / "reports" / "UI-download.png"))

        window.quality.setCurrentIndex(0)
        QTest.mouseClick(window.download_button, Qt.MouseButton.LeftButton)
        assert window.task is not None
        QTest.mouseClick(window.download_button, Qt.MouseButton.LeftButton)
        wait_until(lambda: window.task is None, "Cancellation did not finish")
        assert window.notice.text() == "已取消下载。", window.notice.text()
        assert not list(window.folder.glob("*.part"))
        report["cancel"] = "passed"

        QTest.mouseClick(window.login, Qt.MouseButton.LeftButton)
        wait_until(lambda: window.dialog and window.dialog.refresh.isEnabled(), "QQ QR did not arrive")
        first = window.dialog.qr.pixmap().cacheKey()
        assert not window.dialog.qr.pixmap().isNull(), window.dialog.state.text()
        QTest.mouseClick(window.dialog.refresh, Qt.MouseButton.LeftButton)
        wait_until(lambda: window.dialog.refresh.isEnabled(), "Refreshed QQ QR did not arrive")
        assert not window.dialog.qr.pixmap().isNull() and window.dialog.qr.pixmap().cacheKey() != first
        window.dialog.reject()
        wait_until(lambda: window.dialog is None, "QR cancellation did not finish")
        report["qr"] = "live generation and refresh passed; phone confirmation not performed"
        assert storage.load_credential() is not None
        report["encrypted_login"] = "loaded after QR cancellation"
        window.resize(window.minimumSize())
        QTest.qWait(200)
        assert window.grab().save(str(ROOT / "reports" / "UI-compact.png"))
        (ROOT / "reports" / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if window.dialog:
            window.dialog.reject()
            wait_until(lambda: window.dialog is None, "QR cleanup did not finish")
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
