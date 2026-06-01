"""Mission-console theme: deep navy + cyan/amber accents.

Matches the style of kb_mirror_v8.py and mcs2_control_redesigned.py.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

# Palette
BG_DEEP = QColor("#0a1424")
BG_PANEL = QColor("#0e1c33")
BG_CARD = QColor("#142640")
ACCENT_CYAN = QColor("#22d3ee")
ACCENT_AMBER = QColor("#facc15")
TEXT_PRIMARY = QColor("#e5e7eb")
TEXT_DIM = QColor("#94a3b8")
STATUS_OK = QColor("#10b981")
STATUS_WARN = QColor("#f59e0b")
STATUS_ERR = QColor("#ef4444")

STYLESHEET = """
QWidget {
    background-color: #0a1424;
    color: #e5e7eb;
    font-family: 'JetBrains Mono', 'Source Code Pro', 'DejaVu Sans Mono', monospace;
    font-size: 11pt;
}
QFrame#card {
    background-color: #142640;
    border: 1px solid #1e3a5f;
    border-radius: 4px;
}
QLabel#cardTitle {
    color: #22d3ee;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 4px 6px;
}
QLabel#statusValue {
    color: #facc15;
    font-size: 14pt;
}
QPushButton {
    background-color: #1e3a5f;
    color: #e5e7eb;
    border: 1px solid #22d3ee;
    border-radius: 3px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #294a73;
}
QPushButton:disabled {
    background-color: #1a2438;
    color: #4b5563;
    border-color: #2a3a52;
}
QPushButton#liveButton:checked {
    background-color: #0f5132;
    border-color: #10b981;
    color: #10b981;
}
QPushButton#stopButton:enabled {
    border-color: #f59e0b;
    color: #f59e0b;
}
QPushButton#toggleButton:checked {
    background-color: #0f5132;
    border-color: #10b981;
    color: #10b981;
    font-weight: bold;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #0e1c33;
    border: 1px solid #1e3a5f;
    border-radius: 2px;
    padding: 2px 6px;
    color: #e5e7eb;
}
QTabBar::tab {
    background: #1e3a5f;
    color: #94a3b8;
    padding: 6px 18px;
    margin-right: 2px;
    border: 1px solid #1e3a5f;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #142640;
    color: #22d3ee;
    border-bottom: 2px solid #22d3ee;
}
QPlainTextEdit {
    background-color: #0a1424;
    border: 1px solid #1e3a5f;
    color: #94a3b8;
    font-size: 10pt;
}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, BG_DEEP)
    pal.setColor(QPalette.ColorRole.WindowText, TEXT_PRIMARY)
    pal.setColor(QPalette.ColorRole.Base, BG_PANEL)
    pal.setColor(QPalette.ColorRole.AlternateBase, BG_CARD)
    pal.setColor(QPalette.ColorRole.ToolTipBase, BG_CARD)
    pal.setColor(QPalette.ColorRole.ToolTipText, TEXT_PRIMARY)
    pal.setColor(QPalette.ColorRole.Text, TEXT_PRIMARY)
    pal.setColor(QPalette.ColorRole.Button, BG_CARD)
    pal.setColor(QPalette.ColorRole.ButtonText, TEXT_PRIMARY)
    pal.setColor(QPalette.ColorRole.BrightText, ACCENT_AMBER)
    pal.setColor(QPalette.ColorRole.Highlight, ACCENT_CYAN)
    pal.setColor(QPalette.ColorRole.HighlightedText, BG_DEEP)
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
