"""Desktop search, quality selection, download, and login views."""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, QProcess, QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton,
    QStackedWidget, QStyle, QStyleOptionViewItem, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .music import LoginRequired, MusicService, error_message, track_folder
from .storage import Storage
from .theme import Hero, icon
from .workspace import (
    CANCELLED, DONE, EXISTS, FAILED, LAYOUTS, QUALITY_KEYS, RETRYABLE, RUNNING, STATES, WAITING,
    Options, Workspace,
)


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


def compact_quality(text):
    return text.replace("高品质 MP3", "MP3").replace("标准 MP3", "MP3")


def table_for(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(37)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setFixedHeight(34)
    return table


def table_cell(table, row, col, value, *, tooltip=None, color=None):
    item = QTableWidgetItem(value)
    item.setToolTip(value if tooltip is None else tooltip)
    if color:
        item.setForeground(QColor(color))
    table.setItem(row, col, item)
    return item


def blank_view(glyph, title, description):
    blank = QWidget()
    layout = QVBoxLayout(blank)
    layout.setSpacing(8)
    layout.addStretch(1)
    mark = picture(glyph, "#7161d4", 24)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark.setFixedSize(52, 52)
    mark.setStyleSheet("background: #f1edff; border-radius: 16px;")
    layout.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
    title_widget = label(title, "sectionTitle")
    title_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(title_widget)
    description_widget = label(description, "muted", wrap=True)
    description_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(description_widget)
    layout.addStretch(2)
    return blank, title_widget, description_widget


def paint_check(painter, rect, state, *, focused=False, enabled=True):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    active = state != Qt.CheckState.Unchecked
    x, y = rect.x(), rect.y()
    painter.setPen(QPen(QColor("#5345c7" if active or focused else "#958cae"), 1))
    painter.setBrush(QColor("#5345c7" if active else "white"))
    if not enabled:
        painter.setOpacity(0.5)
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    if active:
        path = QPainterPath()
        if state == Qt.CheckState.PartiallyChecked:
            path.moveTo(x + 4, y + 8)
            path.lineTo(x + 12, y + 8)
        else:
            path.moveTo(x + 4, y + 8)
            path.lineTo(x + 7, y + 11)
            path.lineTo(x + 12, y + 5)
        painter.setPen(QPen(QColor("#ffffff"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
    painter.restore()


class SelectionCheck(QCheckBox):
    def paintEvent(self, event):
        painter = QPainter(self)
        paint_check(painter, QRectF(0, (self.height() - 16) / 2, 16, 16),
                    self.checkState(), focused=self.hasFocus(), enabled=self.isEnabled())
        painter.setPen(QColor("#4e4767" if self.isEnabled() else "#938da5"))
        painter.drawText(self.rect().adjusted(23, 0, 0, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


class CheckCell(QStyledItemDelegate):
    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        styled.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        rect = QRectF(option.rect.center().x() - 7.5, option.rect.center().y() - 7.5, 16, 16)
        paint_check(painter, rect, Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole)))

    def editorEvent(self, event, model, option, index):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonRelease:
            activate = event.button() == Qt.MouseButton.LeftButton and option.rect.contains(event.position().toPoint())
        elif kind == QEvent.Type.KeyPress:
            activate = event.key() == Qt.Key.Key_Space
        else:
            return False
        if activate:
            current = index.data(Qt.ItemDataRole.CheckStateRole)
            state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked.value else Qt.CheckState.Checked
            return model.setData(index, state.value, Qt.ItemDataRole.CheckStateRole)
        return False


class PreferencesDialog(QDialog):
    def __init__(self, options, parent):
        super().__init__(parent)
        self.setWindowTitle("保存偏好")
        self.setMinimumWidth(405)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(23, 20, 23, 20)
        layout.setSpacing(11)
        layout.addWidget(label("保存偏好", "sectionTitle"))
        layout.addWidget(label("应用到之后加入队列的歌曲。", "muted"))
        self.cover = SelectionCheck("保存同名专辑封面（.jpg）")
        self.lyric = SelectionCheck("保存同名同步歌词（.lrc）")
        self.dedupe = SelectionCheck("跳过本地已有的同版本、同音质文件")
        for widget, checked in ((self.cover, options.cover), (self.lyric, options.lyric), (self.dedupe, options.dedupe)):
            widget.setChecked(checked)
            layout.addWidget(widget)
        layout.addWidget(divider())
        layout.addWidget(label("目录结构", "small"))
        self.layout_picker = QualityPicker()
        self.layout_picker.setFixedHeight(38)
        for key, title in LAYOUTS.items():
            self.layout_picker.addItem(title, key)
        self.layout_picker.setCurrentIndex(self.layout_picker.findData(options.layout))
        layout.addWidget(self.layout_picker)
        layout.addWidget(label("附加文件与原音频同目录；已有文件会保留。", "small"))
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = button("应用设置", primary=True)
        save.clicked.connect(self.accept)
        actions.addWidget(save)
        layout.addLayout(actions)

    def options(self):
        return Options(self.cover.isChecked(), self.lyric.isChecked(), self.layout_picker.currentData(), self.dedupe.isChecked())


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
        self.refresh_requested = Event()
        self.stop_requested = Event()
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
            self.refresh_requested.set()
            self.qr.setText("正在更新二维码…")
            self.refresh.setEnabled(False)
            self.state.setText("正在刷新二维码…")
            return
        self.qr.setText("正在获取二维码…")
        self.state.setText("正在连接 QQ 音乐…")
        self.refresh.setEnabled(False)
        self.task = Task(
            lambda update: self.service.qr_login(update, self.refresh_requested, self.stop_requested), self
        )
        self.task.update.connect(self.on_update)
        self.task.finished.connect(self.on_done)
        self.task.start()

    def on_update(self, kind, payload):
        if self.closing or not self.task or self.refresh_requested.is_set():
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
        else:
            self.qr.setText("请刷新二维码")
            self.state.setText(error_message(task.error))
            self.refresh.setEnabled(True)

    def reject(self):
        if self.task:
            self.closing = True
            self.stop_requested.set()
            self.refresh.setEnabled(False)
            self.state.setText("正在关闭登录…")
        else:
            super().reject()


class Window(QMainWindow):
    def __init__(self, storage: Storage, service: MusicService):
        super().__init__()
        self.storage, self.service = storage, service
        self.folder = storage.download_folder()
        self.workspace = Workspace(storage)
        self.options = Options.read(storage.read_object(storage.settings_path).get("options"))
        self.queue_paused = any(job.state == WAITING for job in self.workspace.jobs)
        self.queue_login = False
        self.login_retry = None
        self.download_task = self.active_job = self.active_audio = None
        self.queue_progress = ""
        self.songs, self.qualities = [], []
        self.song = None
        self.task = None
        self.dialog = None
        self.closing = False
        self.setWindowTitle("QQ 音乐下载器")
        self.setWindowIcon(icon("music", "#5345c7", 24))
        self.resize(1170, 785)
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
        columns.addWidget(self.workspace_panel(), 1)
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
        self.update_preferences()
        self.refresh_queue()
        self.refresh_history()
        self.update_login()
        if storage.load_credential() is None:
            self.set_notice("首次使用请点右上角「扫码登录」，用自己的 QQ 确认授权。")
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
        row.addWidget(label(f"便携版 {__version__}", "version"))
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
        layout.addWidget(label("搜索单曲或导入歌单，按你选择的音质保存原音频。", "muted"))
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
        self.playlist_button = button("导入歌单", "link", height=42)
        self.playlist_button.clicked.connect(self.import_playlist)
        search.addWidget(self.playlist_button)
        search.addSpacing(35)
        layout.addLayout(search)
        return hero

    def workspace_panel(self):
        surface = QFrame()
        surface.setObjectName("surface")
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(16, 13, 16, 14)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setAccessibleName("歌曲下载工作区")
        self.tabs.addTab(self.results_panel(), icon("music"), "歌曲")
        self.tabs.addTab(self.queue_panel(), icon("list"), "下载队列")
        self.tabs.addTab(self.history_panel(), icon("history"), "下载历史")
        self.tabs.currentChanged.connect(lambda _: self.refresh_history() if self.tabs.currentIndex() == 2 else None)
        layout.addWidget(self.tabs)
        return surface

    def results_panel(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        self.result_title = label("搜索结果", "sectionTitle")
        self.result_title.setMinimumWidth(0)
        heading.addWidget(self.result_title, 1)
        heading.addStretch()
        self.count = label("", "small")
        self.count.setContentsMargins(2, 0, 7, 0)
        heading.addWidget(self.count)
        layout.addLayout(heading)
        self.results = QStackedWidget()
        blank, self.empty_title, self.empty_help = blank_view(
            "search", "还没有搜索结果", "输入歌名开始搜索，也可以导入 QQ 音乐歌单。",
        )
        self.results.addWidget(blank)
        self.table = table_for(["选", "歌曲", "歌手", "专辑", "时长"])
        self.table.setItemDelegateForColumn(0, CheckCell(self.table))
        for col, width in ((0, 36), (4, 57)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.cellClicked.connect(lambda _row, col: self.choose_song() if col else None)
        self.table.cellActivated.connect(lambda _row, col: self.choose_song() if col else None)
        self.table.itemChanged.connect(lambda _: self.sync_controls())
        self.results.addWidget(self.table)
        layout.addWidget(self.results, 1)
        self.batch_bar = QFrame()
        self.batch_bar.setObjectName("batchBar")
        bar = QHBoxLayout(self.batch_bar)
        bar.setContentsMargins(9, 6, 9, 6)
        bar.setSpacing(6)
        self.check_all = SelectionCheck("全选")
        self.check_all.setAccessibleName("全选当前结果")
        self.check_all.clicked.connect(self.toggle_checks)
        bar.addWidget(self.check_all)
        self.checked_count = label("已选 0 首", "tabNote")
        bar.addWidget(self.checked_count)
        bar.addStretch()
        self.batch_quality = QualityPicker()
        self.batch_quality.setAccessibleName("批量下载音质")
        self.batch_quality.setFixedHeight(32)
        self.batch_quality.setMinimumWidth(147)
        self.batch_quality.addItem("自动最高音质")
        for quality in QUALITY_KEYS.values():
            self.batch_quality.addItem(quality.name, quality)
        bar.addWidget(self.batch_quality)
        self.batch_add = button("加入队列", "download", primary=True, height=32)
        self.batch_add.clicked.connect(self.add_checked)
        bar.addWidget(self.batch_add)
        self.batch_bar.hide()
        layout.addWidget(self.batch_bar)
        return page

    def queue_panel(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.queue_summary = label("", "tabNote", wrap=True)
        layout.addWidget(self.queue_summary)
        self.queue_views = QStackedWidget()
        self.queue_views.addWidget(blank_view("list", "队列空着", "从搜索结果或歌单勾选歌曲，加入队列。")[0])
        self.queue_table = table_for(["歌曲", "音质", "状态", "详情"])
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.queue_table.setColumnWidth(2, 73)
        self.queue_table.itemSelectionChanged.connect(self.sync_controls)
        self.queue_views.addWidget(self.queue_table)
        layout.addWidget(self.queue_views, 1)
        bar = QHBoxLayout()
        self.pause_button = button("暂停队列", height=32)
        self.pause_button.clicked.connect(self.toggle_pause)
        bar.addWidget(self.pause_button)
        self.job_action = button("重试选中", height=32)
        self.job_action.clicked.connect(self.act_on_job)
        bar.addWidget(self.job_action)
        self.clear_jobs = button("清理已完成", height=32)
        self.clear_jobs.clicked.connect(self.clear_completed)
        bar.addWidget(self.clear_jobs)
        bar.addStretch()
        layout.addLayout(bar)
        return page

    def history_panel(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.history_summary = label("", "tabNote")
        layout.addWidget(self.history_summary)
        self.history_views = QStackedWidget()
        self.history_views.addWidget(blank_view("history", "还没有下载记录", "完成下载后，可在这里打开文件或定位文件。")[0])
        self.history_table = table_for(["歌曲", "实际音质", "下载时间", "文件"])
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.history_table.setColumnWidth(3, 73)
        self.history_table.itemSelectionChanged.connect(self.sync_controls)
        self.history_table.cellDoubleClicked.connect(lambda *_: self.open_history())
        self.history_views.addWidget(self.history_table)
        layout.addWidget(self.history_views, 1)
        bar = QHBoxLayout()
        self.open_history_button = button("打开文件", primary=True, height=32)
        self.open_history_button.clicked.connect(lambda: self.open_history())
        bar.addWidget(self.open_history_button)
        self.reveal_history_button = button("定位文件", "folder", height=32)
        self.reveal_history_button.clicked.connect(lambda: self.open_history(reveal=True))
        bar.addWidget(self.reveal_history_button)
        bar.addStretch()
        layout.addLayout(bar)
        return page

    def download_panel(self):
        surface = QFrame()
        surface.setObjectName("surface")
        surface.setFixedWidth(326)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(16, 13, 16, 14)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label("下载选项", "sectionTitle"))
        top.addStretch()
        self.preferences_button = button("偏好", "settings", height=28)
        self.preferences_button.setAccessibleName("保存偏好")
        self.preferences_button.clicked.connect(self.edit_preferences)
        top.addWidget(self.preferences_button)
        layout.addLayout(top)
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
        self.pref_text = label("", "small", wrap=True)
        layout.addWidget(self.pref_text)
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
        expired = expired or self.queue_login
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

    def edit_preferences(self):
        dialog = PreferencesDialog(self.options, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            options = dialog.options()
            try:
                self.storage.set_preferences(options=asdict(options))
                self.options = options
                self.update_preferences()
            except OSError as exc:
                self.set_notice(error_message(exc), "error")
        dialog.deleteLater()

    def update_preferences(self):
        extras = [name for name, enabled in (("封面", self.options.cover), ("歌词", self.options.lyric)) if enabled]
        self.pref_text.setText("  ·  ".join([LAYOUTS[self.options.layout], *extras, "跳过重复" if self.options.dedupe else "允许重复"]))

    def checked_rows(self):
        return [row for row in range(self.table.rowCount())
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def toggle_checks(self, _):
        state = Qt.CheckState.Unchecked if len(self.checked_rows()) == len(self.songs) else Qt.CheckState.Checked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.table.blockSignals(False)
        self.sync_controls()

    def selected_job(self):
        rows = self.queue_table.selectionModel().selectedRows()
        key = self.queue_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None
        return next((job for job in self.workspace.jobs if job.key == key), None)

    def selected_history(self):
        rows = self.history_table.selectionModel().selectedRows()
        return self.workspace.history[rows[0].row()] if rows and rows[0].row() < len(self.workspace.history) else None

    def sync_controls(self):
        if not hasattr(self, "open_button"):
            return
        busy = self.task is not None
        self.query.setEnabled(not busy)
        self.search_button.setEnabled(not busy and bool(self.query.text().strip()))
        self.playlist_button.setEnabled(not busy)
        self.quality.setEnabled(not busy and bool(self.qualities))
        self.login.setEnabled(not busy and self.download_task is None and self.dialog is None)
        self.download_button.setText("下载歌曲")
        self.download_button.setEnabled(not busy and bool(self.qualities))
        checked = len(self.checked_rows())
        self.checked_count.setText(f"已选 {checked} 首")
        self.check_all.setCheckState(Qt.CheckState.Checked if checked and checked == len(self.songs)
                                     else Qt.CheckState.PartiallyChecked if checked else Qt.CheckState.Unchecked)
        self.batch_add.setEnabled(bool(checked))
        job = self.selected_job()
        actions = {RUNNING: "取消当前", WAITING: "移除选中", FAILED: "重试选中", CANCELLED: "重试选中"}
        self.job_action.setText(actions.get(job.state, "重试选中") if job else "重试选中")
        self.job_action.setEnabled(bool(job and job.state in actions and
                                       (job.state != RUNNING or self.download_task and not self.download_task.stopping.is_set())))
        self.pause_button.setText("扫码并继续" if self.queue_login else "继续队列" if self.queue_paused else "暂停队列")
        self.pause_button.setEnabled(any(job.state in {WAITING, RUNNING} for job in self.workspace.jobs))
        self.clear_jobs.setEnabled(any(job.state in {DONE, EXISTS} for job in self.workspace.jobs))
        item = self.selected_history()
        present = bool(item and self.file_present(item.path))
        self.open_history_button.setEnabled(present)
        self.reveal_history_button.setEnabled(present)

    def start(self, job, operation, complete, retry=None):
        if self.task:
            return
        self.job, self.on_result, self.retry = job, complete, retry
        self.task = Task(operation, self)
        self.task.finished.connect(self.on_done)
        self.sync_controls()
        self.task.start()

    def on_done(self):
        task, self.task = self.task, None
        task.deleteLater()
        self.sync_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)
            return
        if task.error:
            self.set_notice(error_message(task.error), "error")
            if self.job in {"search", "playlist"}:
                self.empty_title.setText("歌单导入未完成" if self.job == "playlist" else "搜索未完成")
                self.empty_help.setText(error_message(task.error))
            elif self.job == "quality":
                self.quality.clear()
                self.quality.addItem("未获取到可用音质")
            if isinstance(task.error, LoginRequired):
                self.update_login(expired=True)
                self.show_login(self.retry)
        elif not task.cancelled:
            self.update_login()
            self.on_result(task.result)
        self.start_next()

    def show_login(self, retry=None):
        if self.dialog or self.task or self.download_task:
            if retry is not None:
                self.login_retry = retry
            return
        self.dialog = LoginDialog(self.service, self)
        self.sync_controls()

        def done(result):
            dialog, self.dialog = self.dialog, None
            dialog.deleteLater()
            self.sync_controls()
            if self.closing:
                QTimer.singleShot(0, self.close)
                return
            if result == QDialog.DialogCode.Accepted:
                if self.queue_login:
                    self.queue_login = self.queue_paused = False
                    self.refresh_queue()
                self.update_login()
                self.set_notice("登录成功。", "success")
                resume = retry or getattr(self, "login_retry", None)
                self.login_retry = None
                if resume:
                    resume()
                elif self.song:
                    self.load_quality(self.song)
                self.start_next()
            else:
                self.login_retry = None

        self.dialog.finished.connect(done)
        self.dialog.open()

    def search(self):
        query = self.query.text().strip()
        if not query or self.task:
            return
        self.prepare_results("搜索结果", "正在搜索歌曲…", query)
        self.start("search", lambda _: self.service.search(query), self.show_songs, self.search)

    def import_playlist(self):
        if self.task:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导入 QQ 音乐歌单")
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.addWidget(label("导入 QQ 音乐歌单", "sectionTitle"))
        layout.addWidget(label("粘贴 QQ 音乐的歌单分享链接，或输入数字歌单 ID。", "muted"))
        reference = QLineEdit()
        reference.setAccessibleName("歌单链接或 ID")
        reference.setPlaceholderText("https://y.qq.com/n/ryqq/playlist/…")
        reference.setMaxLength(4096)
        reference.setFixedHeight(38)
        layout.addWidget(reference)
        actions = QHBoxLayout()
        actions.addStretch()
        dismiss = button("取消")
        dismiss.clicked.connect(dialog.reject)
        actions.addWidget(dismiss)
        accept = button("读取歌单", "link", primary=True)
        accept.setEnabled(False)
        accept.setDefault(True)
        accept.clicked.connect(dialog.accept)
        reference.textChanged.connect(lambda text: accept.setEnabled(bool(text.strip())))
        actions.addWidget(accept)
        layout.addLayout(actions)
        reference.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.load_playlist(reference.text().strip())
        dialog.deleteLater()

    def load_playlist(self, reference):
        self.prepare_results("QQ 音乐歌单", "正在读取歌单…", "正在获取曲目，稍后可选择加入队列。")
        self.start("playlist", lambda _: self.service.playlist(reference), self.show_playlist,
                   lambda: self.load_playlist(reference))

    def prepare_results(self, title, status, hint):
        self.tabs.setCurrentIndex(0)
        self.show_songs([])
        self.show_track(None)
        self.result_title.setText(title)
        self.result_title.setToolTip("")
        self.count.clear()
        self.empty_title.setText(status)
        self.empty_help.setText(hint)
        if not self.download_task:
            self.set_notice()

    def show_playlist(self, playlist):
        self.result_title.setText(self.result_title.fontMetrics().elidedText(
            playlist.name, Qt.TextElideMode.ElideRight, max(120, self.result_title.width() - 35)))
        self.result_title.setToolTip(playlist.name)
        self.show_songs(playlist.songs)
        self.count.setText(f"已列出 {len(playlist.songs)} / {playlist.total} 首" if playlist.truncated
                           else f"歌单 · {len(playlist.songs)} 首")
        self.count.setToolTip("最多读取歌单前 500 首；相同曲目只显示一次。" if playlist.truncated else "")
        self.empty_title.setText("这个歌单没有可列出的歌曲")
        self.empty_help.setText("可检查歌单的可见范围或改用关键词搜索。")

    def show_songs(self, songs):
        self.songs = songs
        self.table.blockSignals(True)
        self.table.setRowCount(len(songs))
        self.table.clearSelection()
        for row, song in enumerate(songs):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setToolTip(f"勾选《{song.name}》")
            self.table.setItem(row, 0, check)
            for col, value in enumerate((song.name, song.artists, song.album or "—", duration(song.seconds)), 1):
                item = table_cell(self.table, row, col, value)
                if col == 1:
                    font = self.table.font()
                    font.setBold(True)
                    item.setFont(font)
        self.table.blockSignals(False)
        self.count.setText(f"{len(songs)} 首歌曲")
        self.count.setToolTip("")
        self.empty_title.setText("没有找到相关歌曲")
        self.empty_help.setText("可以换个关键词，或同时输入歌名和歌手。")
        self.results.setCurrentIndex(1 if songs else 0)
        self.batch_bar.setVisible(bool(songs))
        self.sync_controls()

    def choose_song(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.task or self.song == self.songs[rows[0].row()] and self.qualities:
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
        if not self.download_task:
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
        if self.task or not self.song or not self.qualities:
            return
        self.enqueue([self.song], self.quality.currentData())

    def add_checked(self):
        songs = [self.songs[row] for row in self.checked_rows()]
        if not songs:
            return
        self.enqueue(songs, self.batch_quality.currentData())
        self.clear_checks()
        self.tabs.setCurrentIndex(1)

    def clear_checks(self):
        self.table.blockSignals(True)
        for row in self.checked_rows():
            self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.sync_controls()

    def enqueue(self, songs, quality):
        added = self.workspace.add(songs, quality, self.folder, self.options)
        if added:
            extra = len(songs) - len(added)
            detail = f"已加入队列 · {len(added)} 首"
            if extra:
                detail += f"（{extra} 首已在队列）"
            self.set_notice(detail, "success")
            self.refresh_queue(added[0].key)
            self.persist_workspace()
            self.start_next()
        else:
            self.set_notice("所选歌曲已在队列中。")

    def persist_workspace(self):
        try:
            self.workspace.save()
        except OSError:
            self.set_notice("无法保存队列和历史，请检查程序目录的写入权限。", "error")

    def refresh_queue(self, key=None):
        selected = self.selected_job()
        key = key or (selected.key if selected else None)
        jobs = self.workspace.jobs
        counts = {state: 0 for state in STATES}
        self.queue_table.blockSignals(True)
        self.queue_table.clearSelection()
        self.queue_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            counts[job.state] += 1
            title = f"{job.song.name} · {job.song.artists}"
            table_cell(self.queue_table, row, 0, title).setData(Qt.ItemDataRole.UserRole, job.key)
            requested = job.requested.name if job.requested else "自动最高"
            table_cell(self.queue_table, row, 1, compact_quality(requested), tooltip=requested)
            color = {DONE: "#287252", EXISTS: "#287252", FAILED: "#a13c52", RUNNING: "#5645c5"}.get(job.state, "#625e77")
            table_cell(self.queue_table, row, 2, STATES[job.state], color=color)
            details = job.detail or "—"
            table_cell(self.queue_table, row, 3, compact_quality(details),
                       tooltip=f"{details}\n{job.path}" if job.path else details)
            if job.key == key:
                self.queue_table.selectRow(row)
        self.queue_table.blockSignals(False)
        self.queue_views.setCurrentIndex(int(bool(jobs)))
        active = counts[WAITING] + counts[RUNNING]
        self.tabs.setTabText(1, f"下载队列  {active}" if active else "下载队列")
        prefix = "需要扫码 · " if self.queue_login else "已暂停 · " if self.queue_paused else ""
        if not jobs:
            self.queue_summary.setText("歌曲会依次下载，单项失败后会继续下一项。")
        else:
            self.queue_summary.setText(f"{prefix}{counts[WAITING]} 等待  ·  {counts[RUNNING]} 处理中  ·  "
                                       f"{counts[DONE] + counts[EXISTS]} 完成或已存在  ·  {counts[FAILED]} 失败")
        self.sync_controls()

    def refresh_history(self):
        entries = self.workspace.history
        self.history_table.blockSignals(True)
        self.history_table.clearSelection()
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            table_cell(self.history_table, row, 0, f"{entry.song.name} · {entry.song.artists}")
            quality = QUALITY_KEYS[entry.quality].name
            table_cell(self.history_table, row, 1, compact_quality(quality), tooltip=quality)
            try:
                stamp = datetime.fromisoformat(entry.date).astimezone().strftime("%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                stamp = "—"
            table_cell(self.history_table, row, 2, stamp, tooltip=entry.date)
            present = self.file_present(entry.path)
            table_cell(self.history_table, row, 3, "本地" if present else "已缺失",
                       tooltip=entry.path, color="#287252" if present else "#a13c52")
        self.history_table.blockSignals(False)
        self.history_summary.setText(f"{len(entries)} 条记录  ·  双击打开音频")
        self.history_views.setCurrentIndex(int(bool(entries)))
        self.sync_controls()

    @staticmethod
    def file_present(path):
        try:
            return Path(path).is_file()
        except (OSError, ValueError):
            return False

    def open_history(self, *, reveal=False):
        entry = self.selected_history()
        if not entry:
            return
        if not self.file_present(entry.path):
            self.refresh_history()
            self.set_notice("文件已移动或删除，可以重新下载。", "error")
            return
        if reveal:
            opened, _ = QProcess.startDetached("explorer.exe", [f"/select,{Path(entry.path)}"])
        else:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(entry.path))
        if not opened:
            self.set_notice("无法打开这个本地文件。", "error")

    def toggle_pause(self):
        if self.queue_login:
            self.show_login()
            return
        self.queue_paused = not self.queue_paused
        self.refresh_queue()
        self.start_next()

    def act_on_job(self):
        job = self.selected_job()
        if not job:
            return
        if job.state == RUNNING and self.download_task:
            self.download_task.cancel()
            self.set_notice("正在取消当前任务…")
            self.sync_controls()
            return
        if job.state == WAITING:
            self.workspace.jobs.remove(job)
        elif job.state in RETRYABLE:
            if any(other.state in {WAITING, RUNNING} and other.song.mid == job.song.mid
                   and other.quality == job.quality for other in self.workspace.jobs):
                self.set_notice("相同歌曲和音质已在队列中。")
                return
            job.state, job.detail, job.path = WAITING, "", ""
        else:
            return
        if not any(other.state == WAITING for other in self.workspace.jobs) and self.queue_login:
            self.queue_login = self.queue_paused = False
        self.refresh_queue()
        self.persist_workspace()
        self.start_next()

    def clear_completed(self):
        self.workspace.jobs[:] = [job for job in self.workspace.jobs if job.state not in {DONE, EXISTS}]
        self.refresh_queue()
        self.persist_workspace()

    def start_next(self):
        if self.closing or self.download_task or self.dialog:
            return
        if not self.task and self.login_retry:
            self.show_login(self.login_retry)
            return
        if self.queue_paused:
            return
        job = next((item for item in self.workspace.jobs if item.state == WAITING), None)
        if not job:
            return
        candidates = self.workspace.candidates(job)

        async def operation(update):
            folder = track_folder(Path(job.folder), job.song, job.options.layout)
            saved = await self.service.download(job.song, job.requested, folder, update, candidates=candidates)
            update("saved", saved)
            warnings = []
            if job.options.cover or job.options.lyric:
                update("status", "正在保存封面或歌词…")
                warnings = await self.service.attachments(job.song, saved.path, cover=job.options.cover, lyric=job.options.lyric)
            return saved, warnings

        self.active_job, self.active_audio = job, None
        job.state, job.detail = RUNNING, "准备下载"
        self.queue_progress = ""
        self.progress.setRange(0, 0)
        self.progress.show()
        self.progress_detail.setText("正在准备下载")
        self.progress_detail.show()
        self.set_notice(f"正在处理《{job.song.name}》…")
        self.download_task = Task(operation, self)
        self.download_task.update.connect(self.queue_update)
        self.download_task.finished.connect(self.queue_done)
        self.refresh_queue()
        self.persist_workspace()
        self.download_task.start()

    def queue_update(self, kind, payload):
        job = self.active_job
        if not job:
            return
        if kind == "saved":
            self.active_audio = payload
            job.path = str(payload.path)
            try:
                self.workspace.record(job.song, payload)
            except OSError:
                self.set_notice("音频已保存，但无法读取本地文件信息。", "error")
            self.persist_workspace()
            self.refresh_history()
            return
        if kind == "status":
            job.detail = payload
            if not self.queue_progress:
                self.progress_detail.setText(payload)
        elif kind == "progress":
            count, total = payload
            self.progress.setRange(0, 100 if total else 0)
            if total:
                self.progress.setValue(min(100, count * 100 // total))
            self.queue_progress = f"{count / 1048576:.1f} / {total / 1048576:.1f} MiB" if total else f"{count / 1048576:.1f} MiB"
            self.progress_detail.setText(self.queue_progress)
        row = self.workspace.jobs.index(job)
        item = self.queue_table.item(row, 3)
        if item:
            item.setText(job.detail if not self.queue_progress else f"{job.detail} · {self.queue_progress}")
            item.setToolTip(item.text())

    def queue_done(self):
        task, self.download_task = self.download_task, None
        job, self.active_job = self.active_job, None
        saved, self.active_audio = self.active_audio, None
        task.deleteLater()
        self.progress.hide()
        self.progress_detail.hide()
        if task.result or saved:
            if task.result:
                saved, warnings = task.result
            else:
                warnings = ["附加文件未完成"]
            job.path = str(saved.path)
            job.state = EXISTS if saved.reused else DONE
            job.detail = " · ".join([saved.quality.name, *warnings])
            headline = "本地已有" if saved.reused else "下载完成"
            self.set_notice(f"{headline} · {job.detail}", "warning" if warnings else "success")
            self.notice.setToolTip(str(saved.path))
        elif isinstance(task.error, LoginRequired):
            job.state, job.detail = WAITING, "等待扫码登录"
            self.queue_login = self.queue_paused = True
            self.update_login(expired=True)
            self.set_notice("队列已暂停，请扫码登录后继续。", "error")
        elif task.cancelled:
            job.state, job.detail = (WAITING, "待继续") if self.closing else (CANCELLED, "已取消下载")
            self.set_notice("已取消下载。")
        else:
            job.state, job.detail = FAILED, error_message(task.error)
            self.set_notice(job.detail, "error")
        self.refresh_queue()
        self.persist_workspace()
        if self.closing:
            QTimer.singleShot(0, self.close)
        else:
            self.start_next()

    def closeEvent(self, event):
        if self.dialog:
            self.closing = True
            self.dialog.reject()
            event.ignore()
            return
        if self.task or self.download_task:
            self.closing = True
            if self.task:
                self.task.cancel()
            if self.download_task:
                self.download_task.cancel()
            event.ignore()
        else:
            event.accept()
