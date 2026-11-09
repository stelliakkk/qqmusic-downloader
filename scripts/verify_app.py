"""Exercise normal desktop controls with the actual QQ Music account."""

import hashlib
import json
import shutil
import sys
import tempfile
import time
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qqmusic_api.modules.search import SearchType

from qq_downloader import __version__
from qq_downloader.music import MusicService
from qq_downloader.storage import Storage
from qq_downloader.window import Window
from qq_downloader.workspace import DONE, EXISTS, WAITING, Options
from qq_music_app import create_app


def wait_until(condition, description, timeout=55):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise TimeoutError(description)
        QApplication.processEvents()
        time.sleep(0.025)


def main():
    reports = ROOT / "reports" / f"v{__version__}"
    reports.mkdir(parents=True, exist_ok=True)
    app = create_app()
    run = Path(tempfile.mkdtemp(prefix="live-", dir=reports))
    storage = Storage(run / "data")
    storage.data.mkdir()
    original = Storage()
    for path in (original.credential_path, original.device_path):
        if path.is_file():
            shutil.copy2(path, storage.data / path.name)
    service = MusicService(storage)
    window = Window(storage, service)
    window.folder = run / "audio"
    window.update_folder()
    window.show()
    report = {"version": __version__, "source_ui": "started"}
    try:
        assert storage.load_credential() is not None
        QTest.qWait(200)
        assert window.grab().save(str(reports / "UI-start.png"))
        window.query.setText("焦虑型依恋")
        QTest.keyClick(window.query, Qt.Key.Key_Return)
        wait_until(lambda: window.task is None, "Search did not finish")
        assert window.songs, window.empty_help.text()
        row = next(i for i, song in enumerate(window.songs) if song.mid == "002Z0HkT2YoCvz")
        rect = window.table.visualItemRect(window.table.item(row, 1))
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        wait_until(lambda: window.task is None, "Quality lookup did not finish")
        assert window.qualities, window.notice.text()
        report["song"] = window.song.name
        report["artists"] = window.song.artists
        report["available_qualities"] = [quality.name for quality in window.qualities]
        report["search_results"] = len(window.songs)
        QTest.qWait(120)
        assert window.grab().save(str(reports / "UI-results.png"))

        window.options = Options(cover=True, lyric=True, layout="artist_album")
        window.update_preferences()
        for extension in (".flac", ".mp3"):
            available = next(i for i, quality in enumerate(window.qualities, 1) if quality.file_type.e == extension)
            window.quality.setCurrentIndex(available)
            selected = window.quality.currentData().name
            QTest.mouseClick(window.download_button, Qt.MouseButton.LeftButton)
            wait_until(lambda: window.download_task is None, "Download did not finish", timeout=90)
            job = window.workspace.jobs[-1]
            assert job.state == DONE, job.detail
            output = Path(job.path)
            assert output.suffix == extension and output.is_file()
            assert output.relative_to(window.folder).parts[:-1] == (window.song.artists, window.song.album)
            report[extension[1:]] = {
                "file": output.name, "quality": selected, "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "full_decode": True, "detail": job.detail,
                "jpeg": output.with_suffix(".jpg").is_file(), "lrc": output.with_suffix(".lrc").is_file(),
            }
            if extension == ".flac":
                QTest.qWait(120)
                assert window.grab().save(str(reports / "UI-download.png"))

        QTest.mouseClick(window.download_button, Qt.MouseButton.LeftButton)
        wait_until(lambda: window.download_task is None, "Dedupe did not finish")
        assert window.workspace.jobs[-1].state == EXISTS and len(window.workspace.history) == 2
        assert not list(window.folder.rglob("*.part"))
        report["dedupe"] = "reused the selected actual quality"
        window.tabs.setCurrentIndex(1)
        assert window.grab().save(str(reports / "UI-queue.png"))
        window.tabs.setCurrentIndex(2)
        assert window.grab().save(str(reports / "UI-history.png"))

        async def public_ids():
            async with asyncio.timeout(18), service._client() as client:
                result = await service._with_login(client, lambda: client.search.search_by_type(
                    keyword="周杰伦", num=8, search_type=SearchType.SONGLIST))
                return [item.id for item in sorted(result.songlist, key=lambda item: item.songnum, reverse=True) if item.id]

        from qq_downloader.music import error_message
        try:
            ids = asyncio.run(public_ids())
        except Exception as exc:
            ids = []
            report["playlist_search_error"] = error_message(exc)
        for playlist_id in ids[:3]:
            window.load_playlist(f"https://y.qq.com/n/ryqq/playlist/{playlist_id}")
            wait_until(lambda: window.task is None, "Playlist did not finish", timeout=65)
            if window.songs:
                break
        report["playlist"] = ({"id": playlist_id, "title": window.result_title.toolTip(), "tracks": len(window.songs)}
                              if ids and window.songs else {"error": window.empty_help.text() if ids else "未获得公开歌单 ID"})
        if ids and window.songs:
            window.queue_paused = True
            for row in (0, 1):
                item = window.table.item(row, 0)
                QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=window.table.visualItemRect(item).center())
            assert window.checked_rows() == [0, 1]
            assert window.grab().save(str(reports / "UI-playlist.png"))
            QTest.mouseClick(window.batch_add, Qt.MouseButton.LeftButton)
            assert all(job.state == WAITING for job in window.workspace.jobs[-2:])
            report["playlist"]["selected_and_queued"] = 2

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
        assert window.grab().save(str(reports / "UI-compact.png"))
        (reports / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if window.dialog:
            window.dialog.reject()
            wait_until(lambda: window.dialog is None, "QR cleanup did not finish")
        window.close()
        app.processEvents()
        for path in storage.data.glob("qqmusic_*"):
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
