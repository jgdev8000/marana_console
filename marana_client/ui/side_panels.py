"""Right-column panels: cooling, display, contrast, status log."""
from __future__ import annotations

from datetime import datetime

from PyQt6 import QtCore, QtWidgets

from marana_client.ui.image_view import ContrastMode


class CoolingPanel(QtWidgets.QFrame):
    requestSetCooling = QtCore.pyqtSignal(bool, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("COOLING"); t.setObjectName("cardTitle")
        lay.addWidget(t)
        self.enable_cb = QtWidgets.QCheckBox("Enable cooling")
        lay.addWidget(self.enable_cb)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Target:"))
        self.target_spin = QtWidgets.QDoubleSpinBox()
        self.target_spin.setRange(-90.0, 30.0)
        self.target_spin.setValue(-45.0)
        self.target_spin.setSuffix(" °C")
        row.addWidget(self.target_spin)
        lay.addLayout(row)
        self.temp_label = QtWidgets.QLabel("Sensor: -- °C")
        self.temp_label.setStyleSheet("color: #facc15; font-size: 12pt;")
        lay.addWidget(self.temp_label)
        self.status_label = QtWidgets.QLabel("Status: --")
        lay.addWidget(self.status_label)
        apply_btn = QtWidgets.QPushButton("APPLY")
        apply_btn.clicked.connect(
            lambda: self.requestSetCooling.emit(self.enable_cb.isChecked(), self.target_spin.value()))
        lay.addWidget(apply_btn)

    def update_cooling(self, enabled: bool, target_c: float, sensor_temp_c: float, status: str) -> None:
        self.enable_cb.blockSignals(True)
        self.enable_cb.setChecked(enabled)
        self.enable_cb.blockSignals(False)
        self.target_spin.blockSignals(True)
        self.target_spin.setValue(target_c)
        self.target_spin.blockSignals(False)
        self.temp_label.setText(f"Sensor: {sensor_temp_c:+.2f} °C")
        self.status_label.setText(f"Status: {status}")


class DisplayPanel(QtWidgets.QFrame):
    requestRotation = QtCore.pyqtSignal(int)
    requestFlip = QtCore.pyqtSignal(bool, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("DISPLAY"); t.setObjectName("cardTitle")
        lay.addWidget(t)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Rot:"))
        self.rot_combo = QtWidgets.QComboBox()
        for v in (0, 90, 180, 270):
            self.rot_combo.addItem(f"{v}°", userData=v)
        self.rot_combo.currentIndexChanged.connect(
            lambda _i: self.requestRotation.emit(int(self.rot_combo.currentData())))
        row.addWidget(self.rot_combo)
        lay.addLayout(row)
        self.flip_h = QtWidgets.QCheckBox("Flip H")
        self.flip_v = QtWidgets.QCheckBox("Flip V")
        self.flip_h.toggled.connect(lambda _: self._emit_flip())
        self.flip_v.toggled.connect(lambda _: self._emit_flip())
        lay.addWidget(self.flip_h)
        lay.addWidget(self.flip_v)

    def _emit_flip(self) -> None:
        self.requestFlip.emit(self.flip_h.isChecked(), self.flip_v.isChecked())


class ContrastPanel(QtWidgets.QFrame):
    requestContrast = QtCore.pyqtSignal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("CONTRAST"); t.setObjectName("cardTitle")
        lay.addWidget(t)
        self.mode_combo = QtWidgets.QComboBox()
        for m in ContrastMode:
            self.mode_combo.addItem(m.value, userData=m.value)
        self.mode_combo.setCurrentText("percentile")
        lay.addWidget(self.mode_combo)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Min:"))
        self.min_spin = QtWidgets.QSpinBox(); self.min_spin.setRange(0, 65535); self.min_spin.setValue(0)
        row.addWidget(self.min_spin)
        row.addWidget(QtWidgets.QLabel("Max:"))
        self.max_spin = QtWidgets.QSpinBox(); self.max_spin.setRange(0, 65535); self.max_spin.setValue(65535)
        row.addWidget(self.max_spin)
        lay.addLayout(row)
        apply_btn = QtWidgets.QPushButton("APPLY")
        apply_btn.clicked.connect(self._emit)
        lay.addWidget(apply_btn)

    def _emit(self) -> None:
        self.requestContrast.emit(self.mode_combo.currentText(), self.min_spin.value(), self.max_spin.value())


class StatusLog(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        t = QtWidgets.QLabel("STATUS LOG"); t.setObjectName("cardTitle")
        lay.addWidget(t)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        lay.addWidget(self.text)

    def append(self, message: str, severity: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "·", "warn": "!", "error": "✕"}.get(severity, "·")
        self.text.appendPlainText(f"{ts} {prefix} {message}")
