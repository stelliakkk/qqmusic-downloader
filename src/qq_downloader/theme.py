"""Shared typography, colors, and vector icons for the desktop UI."""

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame


GLYPHS = {
    "music": '<path d="M9 18V5l12-2v13M9 18a3 2 0 1 1-6 0 3 2 0 0 1 6 0zm12-2a3 2 0 1 1-6 0 3 2 0 0 1 6 0z"/>',
    "search": '<circle cx="10.5" cy="10.5" r="5.8"/><path d="m15 15 4.5 4.5"/>',
    "download": '<path d="M12 3v11m-4-4 4 4 4-4M5 15v5h14v-5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "person": '<circle cx="12" cy="8" r="3"/><path d="M5 19c0-6 14-6 14 0"/>',
    "refresh": '<path d="M20 7v5h-5M20 12a8 8 0 1 0-2 5"/>',
    "chevron": '<path d="m7 10 5 5 5-5"/>',
}


def icon(name: str, color: str = "#555071", size: int = 20) -> QIcon:
    drawing = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<g fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{GLYPHS[name]}</g></svg>'
    )
    renderer = QSvgRenderer(QByteArray(drawing.encode("utf-8")))
    pixmap = QPixmap(QSize(size * 2, size * 2))
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


class Hero(QFrame):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = self.rect().adjusted(2, 2, -2, -2)
        painter.setClipRect(outline)
        painter.setPen(QPen(QColor("#d9d4fb"), 1.2))
        x = self.width() - 104
        for radius in (43, 62, 81, 100, 119):
            painter.drawEllipse(QRectF(x - radius, 60 - radius, radius * 2, radius * 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#aaa1ef"))
        painter.drawEllipse(QRectF(x + 47, 111, 8, 8))
        painter.setBrush(QColor("#c4bafb"))
        painter.drawEllipse(QRectF(x - 80, 28, 7, 7))


STYLE = """
QWidget { color: #201c39; font: 13px 'Microsoft YaHei UI'; }
QMainWindow, QDialog { background: #f6f7fb; }
QLabel { background: transparent; }
QLabel#brand { font-size: 18px; font-weight: 700; }
QLabel#version { color: #5d51bf; background: #eeebff; border-radius: 9px; padding: 3px 9px; font-size: 11px; font-weight: 700; }
QLabel#eyebrow { color: #5548c3; font-size: 11px; font-weight: 700; }
QLabel#heroTitle { color: #211846; font-size: 31px; font-weight: 700; }
QLabel#sectionTitle { font-size: 18px; font-weight: 700; }
QLabel#trackTitle { font-size: 19px; font-weight: 700; }
QLabel#muted { color: #625e77; }
QLabel#small { color: #625e77; font-size: 12px; }
QLabel#foot { color: #68637d; font-size: 12px; }
QLabel#folder { color: #4c4768; font-size: 12px; }
QLabel#badge { color: #5145b8; background: #f0edff; border-radius: 9px; padding: 4px 8px; font-size: 11px; }
QLabel#notice { color: #5145ad; background: #f1eeff; border-radius: 10px; padding: 10px 12px; }
QLabel#notice[tone='error'] { color: #8e3044; background: #fceff1; }
QLabel#notice[tone='success'] { color: #23684a; background: #e9f7ef; }
QFrame#hero { background: #eeecff; border: 1px solid #e3dfff; border-radius: 18px; }
QFrame#surface { background: white; border: 1px solid #e7e5f1; border-radius: 15px; }
QFrame#trackTile { background: #f6f4fe; border-radius: 11px; }
QFrame#line { background: #eeecf4; min-height: 1px; max-height: 1px; }
QLineEdit, QComboBox { background: #fff; border: 1px solid #dedbec; border-radius: 9px; padding: 0 12px; selection-background-color: #5448c9; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #6557e0; }
QLineEdit#query { font-size: 15px; }
QLineEdit:disabled, QComboBox:disabled { background: #f8f7fc; color: #827d95; }
QComboBox::drop-down { width: 30px; border: 0; }
QComboBox QAbstractItemView { background: white; border: 1px solid #dedbec; selection-background-color: #ebe7ff; selection-color: #211846; padding: 4px; }
QPushButton { border: 1px solid #e0ddeb; border-radius: 9px; background: white; padding: 0 12px; font-weight: 600; }
QPushButton:hover { background: #f5f2ff; border-color: #d5cff7; }
QPushButton:pressed { background: #ebe6ff; }
QPushButton:focus { border-color: #6557e0; }
QPushButton:disabled { background: #f8f7fb; color: #938da5; border-color: #e9e7ef; }
QPushButton#primary { background: #5345c7; color: #fff; border-color: #5345c7; }
QPushButton#primary:hover { background: #4537b0; border-color: #4537b0; }
QPushButton#primary:pressed { background: #382b96; }
QPushButton#primary:focus { border-color: #2f237e; }
QPushButton#primary:disabled { background: #e9e5fa; border-color: #e9e5fa; color: #756b9e; }
QTableWidget { background: white; border: 0; alternate-background-color: #faf9fe; outline: 0; selection-background-color: #ece8ff; selection-color: #261a68; }
QTableWidget::item { padding: 0 8px; border: 0; }
QTableWidget::item:hover { background: #f3efff; }
QTableWidget::item:selected { background: #ece8ff; color: #261a68; }
QHeaderView { background: #f7f6fb; border-radius: 6px; }
QHeaderView::section { background: #f7f6fb; color: #625c78; font-size: 12px; font-weight: 600; padding: 8px; border: 0; }
QScrollBar:vertical { border: 0; background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #d7d3e8; min-height: 30px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QProgressBar { border: 0; background: #eeeaf9; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #6555da; border-radius: 4px; }
QToolTip { color: #241d40; background: white; border: 1px solid #dcd7ef; }
"""
