"""Make the Windows application icon using the same music glyph as the UI."""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qq_downloader.theme import icon


app = QApplication([])
canvas = QPixmap(256, 256)
canvas.fill(Qt.GlobalColor.transparent)
painter = QPainter(canvas)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#5345c7"))
painter.drawRoundedRect(QRectF(3, 3, 250, 250), 64, 64)
painter.drawPixmap(58, 58, icon("music", "#ffffff", 140).pixmap(140, 140))
painter.end()
if not canvas.save(sys.argv[1], "ICO"):
    raise SystemExit("Unable to save application icon")
