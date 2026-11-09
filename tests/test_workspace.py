"""Regression tests for the queue, local files, and the actual Qt controls."""

import asyncio
import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from qq_downloader.music import (
    QUALITIES, LoginRequired, MusicError, MusicService, SavedAudio, Song, Source,
    _save_without_overwrite, lrc_payload, playlist_reference, track_folder,
)
from qq_downloader.storage import Storage
from qq_downloader.window import Window
from qq_downloader.workspace import CANCELLED, DONE, FAILED, RUNNING, WAITING, Options, Workspace
from qq_music_app import create_app


SONG = Song("trackA", "晴天", "周杰伦", "叶惠美", 269)
SECOND = Song("trackB", "晴天", "周杰伦", "演唱会", 278)
HIGH, LOW = QUALITIES[2], QUALITIES[-1]


class LocalTests(unittest.TestCase):
    def test_recover_jobs_and_keep_valid_entries_from_partial_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            path = Path(tmp)
            store = Storage(path / "data")
            queue = Workspace(store)
            jobs = queue.add([SONG, SONG, SECOND], HIGH, path, Options(lyric=True))
            self.assertEqual(len(jobs), 2)
            self.assertFalse(queue.add([SONG], HIGH, path, Options()))
            jobs[0].state = RUNNING
            queue.save()
            raw = store.read_object(store.workspace_path)
            raw["queue"].append({"song": {"mid": 0}})
            store.write_object(store.workspace_path, raw)
            rebuilt = Workspace(store)
            self.assertEqual([job.state for job in rebuilt.jobs], [WAITING, WAITING])
            self.assertTrue(rebuilt.jobs[0].options.lyric)
            self.assertEqual(rebuilt.jobs[0].requested, HIGH)

    def test_history_exact_version_and_quality_and_changed_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            path = Path(tmp)
            store = Storage(path / "data")
            queue = Workspace(store)
            output = path / "song.flac"
            output.write_bytes(b"abc")
            queue.record(SONG, SavedAudio(output, HIGH, 3, 1, 48000, 24))
            queue.save()
            queue = Workspace(store)
            first, other = queue.add([SONG, SECOND], None, path, Options())
            self.assertEqual(set(queue.candidates(first)), {HIGH.file_type.name})
            self.assertFalse(queue.candidates(other))
            self.assertTrue(queue.history[0].reusable().reused)
            output.write_bytes(b"changed")
            self.assertIsNone(queue.history[0].reusable())
            output.unlink()
            self.assertIsNone(queue.history[0].reusable())

    def test_files_keep_distinct_sidecar_names_and_safe_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            path = Path(tmp)
            song = Song("x", "相遇", "CON.txt", "../AUX", 0)
            output = track_folder(path, song, "artist_album")
            self.assertTrue(output.is_relative_to(path))
            self.assertEqual(output.parent.name, "_CON.txt")
            output.mkdir(parents=True)
            paths = []
            for ext in (".flac", ".mp3", ".flac"):
                part = output / "incoming.part"
                part.write_bytes(ext.encode())
                paths.append(_save_without_overwrite(part, output, song, ext))
            self.assertEqual(len({audio.stem for audio in paths}), 3)
            self.assertEqual(paths[0].read_bytes(), b".flac")

    def test_official_playlist_urls_and_timed_lyrics(self):
        for value in (
            "123", "https://y.qq.com/n/ryqq/playlist/123",
            "https://y.qq.com/n/ryqq_v2/playlist/123?x=y",
            "https://y.qq.com/n/yqq/playlist/123.html",
            "https://i.y.qq.com/n/m/detail/taoge/index.html?id=123",
            "https://i.y.qq.com/n2/m/share/details/taoge.html?ADTAG=pc_v17&id=123&openinqqmusic=1",
        ):
            self.assertEqual(playlist_reference(value)[1], 123, value)
        self.assertEqual(playlist_reference("https://c6.y.qq.com/base/fcgi-bin/u?__=abc")[1], None)
        for invalid in ("http://y.qq.com/playlist/123", "https://y.qq.com.evil.net/playlist/123",
                        "https://y.qq.com@evil.net/playlist/123", "https://y.qq.com:80/playlist/123",
                        "https://y.qq.com/n/ryqq/songDetail/abc?id=123",
                        "https://i.y.qq.com/n2/m/share/details/song.html?id=123"):
            with self.assertRaises(MusicError):
                playlist_reference(invalid)
        raw = "[00:01.00]晴天 &amp; 风"
        self.assertEqual(lrc_payload(base64.b64encode(raw.encode()).decode()).decode(), "[00:01.00]晴天 & 风\n")
        with self.assertRaises(MusicError):
            lrc_payload("这里没有时间戳")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_highest_authorized_still_wins_over_lower_cached(self):
        svc = MusicService(Storage())
        svc._sources = AsyncMock(return_value=[Source(HIGH, "high"), Source(LOW, "low")])
        download = SavedAudio(Path("new.flac"), HIGH, 1, 1, 48000, 24)
        svc._download_one = AsyncMock(return_value=download)
        cached = SavedAudio(Path("old.mp3"), LOW, 1, 1, 48000, 0, reused=True)
        entry = SimpleNamespace(reusable=lambda: cached)
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            root = Path(tmp)
            self.assertIs(await svc.download(SONG, None, root, lambda *_: None,
                                            candidates={LOW.file_type.name: [entry]}), download)
            svc._download_one.assert_awaited_once()
            svc._download_one.side_effect = TimeoutError()
            self.assertIs(await svc.download(SONG, None, root, lambda *_: None,
                                            candidates={LOW.file_type.name: [entry]}), cached)
            with self.assertRaisesRegex(MusicError, "校验失败"):
                await svc.download(SONG, HIGH, root, lambda *_: None,
                                   candidates={LOW.file_type.name: [entry]})

    async def test_share_redirect_never_requests_external_host(self):
        requested = []

        class Redirect:
            status = 302
            headers = {"Location": "https://not-qq.invalid/playlist/123"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            def get(self, url, *, allow_redirects):
                requested.append((url, allow_redirects))
                return self

        with patch("qq_downloader.music.aiohttp.ClientSession", return_value=Redirect()):
            with self.assertRaises(MusicError):
                await MusicService._resolve_playlist("https://u.y.qq.com/short")
            self.assertEqual(requested, [("https://u.y.qq.com/short", False)])
            Redirect.headers = {"Location": "https://y.qq.com/n/ryqq/playlist/123"}
            self.assertEqual(await MusicService._resolve_playlist("https://u.y.qq.com/short"), 123)

            requested.clear()
            desktop_share = "https://c6.y.qq.com/base/fcgi-bin/u?__=test"
            Redirect.headers = {"Location": "https://i.y.qq.com/n2/m/share/details/taoge.html?ADTAG=pc_v17&id=123"}
            self.assertEqual(await MusicService._resolve_playlist(desktop_share), 123)
            self.assertEqual(requested, [(desktop_share, False)])


class FakeService:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.login_required = False
        self.hold = False

    async def search(self, query):
        return [SONG, SECOND] if query == "一" else [Song("new", "另一首", "歌手", "", 45)]

    async def qualities(self, song):
        return [HIGH, LOW]

    async def download(self, song, requested, folder, update, *, candidates):
        self.calls.append(song.mid)
        while self.hold:
            await asyncio.sleep(0.03)
        if self.login_required:
            raise LoginRequired("需要扫码")
        if self.fail and song == SONG:
            raise MusicError("指定音质不可用")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{song.mid}.flac"
        path.write_bytes(b"audio")
        return SavedAudio(path, HIGH, 5, song.seconds, 48000, 24)

    async def attachments(self, song, path, **options):
        return ["同步歌词未保存"] if options["lyric"] else []


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        path = Path(self.temp.name)
        self.service = FakeService()
        self.window = Window(Storage(path / "data"), self.service)
        self.window.folder = path / "downloads"
        self.window.update_folder()
        self.window.show()
        QTest.qWait(30)

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: not self.window.task and not self.window.download_task)
        self.app.processEvents()
        self.temp.cleanup()

    def wait_for(self, condition, timeout=3):
        stop = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > stop:
                self.fail("Qt operation did not finish")
            QTest.qWait(10)

    def search(self, text):
        self.window.query.setText(text)
        QTest.keyClick(self.window.query, Qt.Key.Key_Return)
        self.wait_for(lambda: self.window.task is None)

    def test_check_rows_search_while_downloading_retry_and_clear(self):
        w = self.window
        self.service.fail = self.service.hold = True
        self.search("一")
        cell = w.table.visualItemRect(w.table.item(0, 0))
        QTest.mouseClick(w.table.viewport(), Qt.MouseButton.LeftButton, pos=cell.center() - QPoint(8, 0))
        self.assertEqual(w.checked_rows(), [0])
        QTest.mouseClick(w.check_all, Qt.MouseButton.LeftButton)
        self.assertEqual(w.checked_rows(), [0, 1])
        QTest.mouseClick(w.batch_add, Qt.MouseButton.LeftButton)
        self.assertEqual(w.tabs.currentIndex(), 1)
        self.search("二")
        self.assertTrue(w.download_task)
        QTest.mouseClick(w.check_all, Qt.MouseButton.LeftButton)
        QTest.mouseClick(w.batch_add, Qt.MouseButton.LeftButton)
        self.service.hold = False
        self.wait_for(lambda: w.download_task is None)
        self.assertEqual([job.state for job in w.workspace.jobs], [FAILED, DONE, DONE])
        self.assertEqual(self.service.calls, ["trackA", "trackB", "new"])
        w.queue_table.selectRow(0)
        self.service.fail = False
        QTest.mouseClick(w.job_action, Qt.MouseButton.LeftButton)
        self.wait_for(lambda: w.download_task is None)
        self.assertEqual(len(w.workspace.history), 3)
        w.tabs.setCurrentIndex(2)
        w.history_table.selectRow(0)
        self.assertTrue(w.open_history_button.isEnabled())
        w.tabs.setCurrentIndex(1)
        QTest.mouseClick(w.clear_jobs, Qt.MouseButton.LeftButton)
        self.assertFalse(w.workspace.jobs)
        self.assertEqual(len(w.workspace.history), 3)

    def test_pause_cancel_and_login_stops_queue(self):
        w = self.window
        self.service.hold = True
        w.enqueue([SONG, SECOND], None)
        w.tabs.setCurrentIndex(1)
        w.toggle_pause()
        w.queue_table.selectRow(0)
        QTest.mouseClick(w.job_action, Qt.MouseButton.LeftButton)
        self.wait_for(lambda: w.download_task is None)
        self.assertEqual([job.state for job in w.workspace.jobs], [CANCELLED, WAITING])
        self.service.hold = False
        self.service.login_required = True
        w.toggle_pause()
        self.wait_for(lambda: w.download_task is None)
        self.assertTrue(w.queue_login)
        self.assertEqual(w.workspace.jobs[1].state, WAITING)
        self.assertIn("扫码", w.pause_button.text())


if __name__ == "__main__":
    unittest.main()
