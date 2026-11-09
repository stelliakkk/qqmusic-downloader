"""Desktop search, quality selection, download, and login views."""

import asyncio
from contextlib import suppress
from pathlib import Path
from threading import Event

from PySide6.QtCore import QRect, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .music import LoginRequired, MusicService, error_message
from .storage import Storage
from .theme import Hero, icon


def label(value="", name="", *, wrap=False):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, glyph=None, *, primary=False, height=36):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    if glyph:
        widget.setIcon(icon(glyph, "#ffffff" if primary else "#555071", 17))
        widget.setIconSize(QSize(17, 17))
    widget.setFixedHeight(height)
    return widget


def picture(glyph, color, size):
    widget = QLabel()
    widget.setPixmap(icon(glyph, color, size).pixmap(QSize(size, size)))
    return widget


def divider():
    line = QFrame()
    line.setObjectName("line")
    line.setFixedHeight(1)
    return line


def duration(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds else "—"


class QualityPicker(QComboBox):
    def __init__(self):
        super().__init__()
        self.arrow = icon("chevron", "#5a4c94", 17)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        self.arrow.paint(painter, QRect(self.width() - 27, (self.height() - 17) // 2, 17, 17))


class Task(QThread):
    update = Signal(str, object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.stopping = Event()
        self.result = None
        self.error = None
        self.cancelled = False

    def cancel(self):
        self.stopping.set()

    async def execute(self):
        operation = asyncio.create_task(self.operation(self.update.emit))

        async def check_cancel():
            while not self.stopping.is_set():
                await asyncio.sleep(0.08)
            operation.cancel()

        watcher = asyncio.create_task(check_cancel())
        try:
            return await operation
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    def run(self):
        try:
            self.result = asyncio.run(self.execute())
        except asyncio.CancelledError:
            self.cancelled = True
        except Exception as exc:
            self.error = exc


class LoginDialog(QDialog):
    def __init__(self, service, parent):
        super().__init__(parent)
        self.service = service
        self.task = None
        self.closing = False
        self.setWindowTitle("登录 QQ 音乐")
        self.setFixedWidth(385)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 24, 25, 22)
        layout.setSpacing(13)
        layout.addWidget(label("账号授权", "eyebrow"))
        layout.addWidget(label("登录 QQ 音乐", "sectionTitle"))
        layout.addWidget(label("用手机 QQ 扫描二维码，然后在手机上确认授权。", "muted", wrap=True))
        self.qr = label("正在获取二维码…")
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setFixedSize(208, 208)
        self.qr.setStyleSheet("background: white; border: 1px solid #e9e5f4; border-radius: 14px;")
        layout.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.state = label("正在连接 QQ 音乐…", "small", wrap=True)
        self.state.setMinimumHeight(32)
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state)
        actions = QHBoxLayout()
        self.refresh = button("刷新二维码", "refresh", primary=True)
        self.refresh.clicked.connect(self.request)
        close = button("取消")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        actions.addWidget(self.refresh, 1)
        layout.addLayout(actions)
        self.request()

    def request(self):
        if self.task:
            self.task.cancel()
            self.refresh.setEnabled(False)
            self.state.setText("正在刷新二维码…")
            return
        self.qr.setText("正在获取二维码…")
        self.state.setText("正在连接 QQ 音乐…")
        self.refresh.setEnabled(False)
        self.task = Task(self.service.qr_login, self)
        self.task.update.connect(self.on_update)
        self.task.finished.connect(self.on_done)
        self.task.start()

    def on_update(self, kind, payload):
        if self.closing or not self.task or self.task.stopping.is_set():
            return
        if kind == "qr":
            pixmap = QPixmap()
            if pixmap.loadFromData(payload):
                self.qr.setPixmap(pixmap.scaled(178, 178, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                self.refresh.setEnabled(True)
                self.state.setText("请打开手机 QQ 扫一扫")
            else:
                self.state.setText("二维码显示失败，请刷新后重试。")
                self.refresh.setEnabled(True)
        elif kind == "scan":
            self.state.setText("已扫描，请在手机上确认授权" if payload else "请打开手机 QQ 扫一扫")

    def on_done(self):
        task, self.task = self.task, None
        task.deleteLater()
        if self.closing:
            super().reject()
        elif task.result:
            self.accept()
        elif task.stopping.is_set():
            self.request()
        else:
            self.qr.setText("请刷新二维码")
            self.state.setText(error_message(task.error))
            self.refresh.setEnabled(True)

    def reject(self):
        if self.task:
            self.closing = True
            self.task.cancel()
        else:
            super().reject()


class Window(QMainWindow):
    def __init__(self, storage: Storage, service: MusicService):
        super().__init__()
        self.storage, self.service = storage, service
        self.folder = storage.download_folder()
        self.songs, self.qualities = [], []
        self.song = None
        self.task = None
        self.dialog = None
        self.closing = False
        self.setWindowTitle("QQ 音乐下载器")
        self.setWindowIcon(icon("music", "#5345c7", 24))
        self.resize(1150, 765)
        self.setMinimumSize(950, 735)

        content = QWidget()
        self.setCentralWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 18, 24, 15)
        outer.setSpacing(14)
        outer.addLayout(self.header())
        outer.addWidget(self.search_panel())
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self.results_panel(), 1)
        columns.addWidget(self.download_panel())
        outer.addLayout(columns, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(label("QQ 音乐  /  原始 MP3 · FLAC", "foot"))
        bottom.addStretch()
        bottom.addWidget(label("Ctrl + L  聚焦搜索", "foot"))
        outer.addLayout(bottom)
        self.shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        self.shortcut.activated.connect(self.focus_search)
        self.update_folder()
        self.update_login()
        self.sync_controls()
        self.query.setFocus()

    def header(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        mark = picture("music", "#ffffff", 21)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(37, 37)
        mark.setStyleSheet("background: #5345c7; border-radius: 11px;")
        row.addWidget(mark)
        row.addWidget(label("QQ 音乐下载器", "brand"))
        row.addWidget(label("个人版 1.0", "version"))
        row.addStretch()
        self.login_state = label("", "small")
        row.addWidget(self.login_state)
        self.login = button("扫码登录", "person")
        self.login.clicked.connect(lambda: self.show_login())
        row.addWidget(self.login)
        return row

    def search_panel(self):
        hero = Hero()
        hero.setObjectName("hero")
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(24, 17, 24, 19)
        layout.setSpacing(5)
        layout.addWidget(label("QQ 音乐曲库", "eyebrow"))
        layout.addWidget(label("搜索歌曲", "heroTitle"))
        layout.addWidget(label("输入歌名或歌手，从搜索结果选择歌曲和音质。", "muted"))
        layout.addSpacing(7)
        search = QHBoxLayout()
        search.setSpacing(8)
        self.query = QLineEdit()
        self.query.setObjectName("query")
        self.query.setPlaceholderText("搜索歌名或歌手，例如 焦虑型依恋")
        self.query.setAccessibleName("搜索歌名或歌手")
        self.query.setFixedHeight(42)
        self.query.setClearButtonEnabled(True)
        self.query.setMaxLength(160)
        self.query.returnPressed.connect(self.search)
        self.query.textChanged.connect(self.sync_controls)
        search.addWidget(self.query, 1)
        self.search_button = button("搜索", "search", primary=True, height=42)
        self.search_button.setFixedWidth(105)
        self.search_button.clicked.connect(self.search)
        search.addWidget(self.search_button)
        search.addStretch(1)
        layout.addLayout(search)
        return hero

    def results_panel(self):
        surface = QFrame()
        surface.setObjectName("surface")
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(16, 13, 16, 14)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(label("搜索结果", "sectionTitle"))
        heading.addStretch()
        self.count = label("", "small")
        heading.addWidget(self.count)
        layout.addLayout(heading)
        self.results = QStackedWidget()
        blank = QWidget()
        blank_layout = QVBoxLayout(blank)
        blank_layout.setSpacing(8)
        blank_layout.addStretch(1)
        mark = picture("search", "#7161d4", 24)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(54, 54)
        mark.setStyleSheet("background: #f1edff; border-radius: 17px;")
        blank_layout.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = label("还没有搜索结果", "sectionTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blank_layout.addWidget(self.empty_title)
        self.empty_help = label("输入歌名开始搜索。", "muted", wrap=True)
        self.empty_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blank_layout.addWidget(self.empty_help)
        blank_layout.addStretch(2)
        self.results.addWidget(blank)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["歌曲", "歌手", "专辑", "时长"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 62)
        self.table.horizontalHeader().setFixedHeight(34)
        self.table.itemSelectionChanged.connect(self.choose_song)
        self.results.addWidget(self.table)
        layout.addWidget(self.results, 1)
        return surface

    def download_panel(self):
        surface = QFrame()
        surface.setObjectName("surface")
        surface.setFixedWidth(326)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(16, 13, 16, 14)
        layout.setSpacing(6)
        layout.addWidget(label("下载选项", "sectionTitle"))
        tile = QFrame()
        tile.setObjectName("trackTile")
        tile.setFixedHeight(65)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(12, 9, 12, 9)
        tile_layout.setSpacing(3)
        self.track = label("请选择一首歌曲", "trackTitle")
        self.artist = label("歌曲和歌手信息会显示在这里", "small")
        self.track.setMinimumHeight(26)
        self.artist.setMinimumHeight(18)
        tile_layout.addWidget(self.track)
        tile_layout.addWidget(self.artist)
        layout.addWidget(tile)
        quality_heading = QHBoxLayout()
        quality_heading.addWidget(label("音质", "small"))
        quality_heading.addStretch()
        self.quality_count = label("", "small")
        quality_heading.addWidget(self.quality_count)
        layout.addLayout(quality_heading)
        self.quality = QualityPicker()
        self.quality.setAccessibleName("下载音质")
        self.quality.setFixedHeight(38)
        self.quality.addItem("请先选择歌曲")
        self.quality.setMaxVisibleItems(7)
        layout.addWidget(self.quality)
        self.quality_help = label("显示当前账号可用的 MP3 和 FLAC。", "small", wrap=True)
        layout.addWidget(self.quality_help)
        layout.addWidget(divider())
        folder_header = QHBoxLayout()
        folder_header.addWidget(label("保存位置", "small"))
        folder_header.addStretch()
        self.change_folder = button("更改", height=26)
        self.change_folder.clicked.connect(self.pick_folder)
        folder_header.addWidget(self.change_folder)
        layout.addLayout(folder_header)
        self.folder_text = label("", "folder")
        layout.addWidget(self.folder_text)
        layout.addStretch(1)
        self.notice = label("", "notice", wrap=True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(7)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.progress_detail = label("", "small")
        self.progress_detail.hide()
        layout.addWidget(self.progress_detail)
        self.download_button = button("下载歌曲", "download", primary=True, height=40)
        self.download_button.clicked.connect(self.download)
        layout.addWidget(self.download_button)
        self.open_button = button("打开下载目录", "folder", height=33)
        self.open_button.clicked.connect(self.open_folder)
        layout.addWidget(self.open_button)
        return surface

    def focus_search(self):
        self.query.setFocus()
        self.query.selectAll()

    def update_login(self, *, expired=False):
        saved = not expired and self.storage.load_credential() is not None
        self.login_state.setText("已保存登录" if saved else ("登录已失效" if expired else "尚未登录"))
        self.login.setText("重新扫码" if saved else "扫码登录")

    def update_folder(self):
        self.folder_text.setToolTip(str(self.folder))
        self.folder_text.setText(self.folder_text.fontMetrics().elidedText(str(self.folder), Qt.TextElideMode.ElideMiddle, 289))

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择下载目录", str(self.folder))
        if folder:
            try:
                self.storage.set_download_folder(Path(folder))
                self.folder = Path(folder)
                self.update_folder()
            except OSError as exc:
                self.set_notice(error_message(exc), "error")

    def open_folder(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))):
                self.set_notice("无法打开下载目录，请检查保存位置。", "error")
        except OSError as exc:
            self.set_notice(error_message(exc), "error")

    def set_notice(self, text="", tone="normal"):
        self.notice.setProperty("tone", tone)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setText(text)
        self.notice.setToolTip("")
        self.notice.setMinimumHeight(max(37, self.notice.heightForWidth(290)))
        self.notice.setVisible(bool(text))

    def sync_controls(self):
        if not hasattr(self, "download_button"):
            return
        busy = self.task is not None
        downloading = busy and self.job == "download"
        self.query.setEnabled(not busy)
        self.search_button.setEnabled(not busy and bool(self.query.text().strip()))
        self.table.setEnabled(not busy)
        self.quality.setEnabled(not busy and bool(self.qualities))
        self.login.setEnabled(not busy)
        self.change_folder.setEnabled(not busy)
        self.download_button.setText("取消下载" if downloading else "下载歌曲")
        self.download_button.setEnabled((downloading and not self.task.stopping.is_set()) or (not busy and bool(self.qualities)))

    def start(self, job, operation, complete, retry=None):
        if self.task:
            return
        self.job, self.on_result, self.retry = job, complete, retry
        self.task = Task(operation, self)
        self.task.update.connect(self.on_update)
        self.task.finished.connect(self.on_done)
        self.sync_controls()
        self.task.start()

    def on_update(self, kind, payload):
        if kind == "status":
            self.set_notice(payload)
        elif kind == "progress":
            count, total = payload
            self.progress.setRange(0, 100 if total else 0)
            if total:
                self.progress.setValue(min(100, count * 100 // total))
            self.progress_detail.setText(f"{count / 1048576:.1f} / {total / 1048576:.1f} MiB" if total else f"{count / 1048576:.1f} MiB")

    def on_done(self):
        task, self.task = self.task, None
        task.deleteLater()
        self.sync_controls()
        if self.closing:
            self.close()
            return
        self.progress.hide()
        self.progress_detail.hide()
        if task.cancelled:
            self.set_notice("已取消下载。")
        elif task.error:
            self.set_notice(error_message(task.error), "error")
            if self.job == "search":
                self.empty_title.setText("搜索未完成")
                self.empty_help.setText(error_message(task.error))
            elif self.job == "quality":
                self.quality.clear()
                self.quality.addItem("未获取到可用音质")
            if isinstance(task.error, LoginRequired):
                self.update_login(expired=True)
                self.show_login(self.retry)
        else:
            self.update_login()
            self.on_result(task.result)

    def show_login(self, retry=None):
        if self.dialog or self.task:
            return
        self.dialog = LoginDialog(self.service, self)

        def done(result):
            dialog, self.dialog = self.dialog, None
            dialog.deleteLater()
            if result == QDialog.DialogCode.Accepted:
                self.update_login()
                self.set_notice("登录成功。", "success")
                if retry:
                    retry()
                elif self.song:
                    self.load_quality(self.song)

        self.dialog.finished.connect(done)
        self.dialog.open()

    def search(self):
        query = self.query.text().strip()
        if not query or self.task:
            return
        self.table.clearSelection()
        self.show_track(None)
        self.results.setCurrentIndex(0)
        self.count.clear()
        self.empty_title.setText("正在搜索歌曲…")
        self.empty_help.setText(query)
        self.set_notice()
        self.start("search", lambda _: self.service.search(query), self.show_songs, self.search)

    def show_songs(self, songs):
        self.songs = songs
        self.table.blockSignals(True)
        self.table.setRowCount(len(songs))
        self.table.clearSelection()
        for row, song in enumerate(songs):
            for col, value in enumerate((song.name, song.artists, song.album or "—", duration(song.seconds))):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 0:
                    font = self.table.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.count.setText(f"{len(songs)} 首歌曲")
        self.empty_title.setText("没有找到相关歌曲")
        self.empty_help.setText("可以换个关键词，或同时输入歌名和歌手。")
        self.results.setCurrentIndex(1 if songs else 0)

    def choose_song(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.task:
            return
        self.load_quality(self.songs[rows[0].row()])

    def show_track(self, song):
        self.song, self.qualities = song, []
        for widget, value in (
            (self.track, song.name if song else "请选择一首歌曲"),
            (self.artist, f"{song.artists}  ·  {duration(song.seconds)}" if song else "歌曲和歌手信息会显示在这里"),
        ):
            widget.setText(widget.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, 265))
            widget.setToolTip(value)
        self.quality_count.clear()
        self.quality.clear()
        self.quality.addItem("正在查询可用音质…" if song else "请先选择歌曲")
        self.sync_controls()

    def load_quality(self, song):
        self.show_track(song)
        self.set_notice()
        self.start("quality", lambda _: self.service.qualities(song), self.show_qualities, lambda: self.load_quality(song))

    def show_qualities(self, qualities):
        self.qualities = qualities
        self.quality.clear()
        if qualities:
            self.quality.addItem("自动选最高音质")
            for quality in qualities:
                self.quality.addItem(quality.name, quality)
            self.quality_count.setText(f"{len(qualities)} 个档位")
        else:
            self.quality.addItem("暂无 MP3 或 FLAC 音源")
            self.set_notice("当前账号没有可用的 MP3 或 FLAC 音源，可以试试同一首歌的其他版本。", "error")
        self.sync_controls()

    def download(self):
        if self.task:
            if self.job == "download":
                self.task.cancel()
                self.set_notice("正在取消下载…")
                self.sync_controls()
            return
        if not self.song or not self.qualities:
            return
        song, quality, folder = self.song, self.quality.currentData(), self.folder
        self.progress.setRange(0, 0)
        self.progress.show()
        self.progress_detail.setText("正在准备下载")
        self.progress_detail.show()
        self.set_notice("正在准备下载…")
        self.start("download", lambda update: self.service.download(song, quality, folder, update), self.downloaded, lambda: self.load_quality(song))

    def downloaded(self, saved):
        spec = f"{saved.bits} bit / " if saved.bits else ""
        spec += f"{saved.sample_rate / 1000:g} kHz"
        self.set_notice(f"下载完成 · {saved.quality.name}\n{spec}  ·  {saved.size / 1048576:.1f} MiB", "success")
        self.notice.setToolTip(str(saved.path))

    def closeEvent(self, event):
        if self.task:
            self.closing = True
            self.task.cancel()
            event.ignore()
        else:
            event.accept()
