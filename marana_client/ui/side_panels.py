"""Right-column panels: cooling, display, contrast, status log."""
from __future__ import annotations

from datetime import datetime

from PyQt6 import QtCore, QtWidgets


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
        # Flip H / V as checkable buttons that stay lit when active.
        flip_row = QtWidgets.QHBoxLayout()
        self.flip_h = QtWidgets.QPushButton("Flip H")
        self.flip_v = QtWidgets.QPushButton("Flip V")
        for b in (self.flip_h, self.flip_v):
            b.setObjectName("toggleButton")
            b.setCheckable(True)
            b.toggled.connect(lambda _: self._emit_flip())
            flip_row.addWidget(b)
        lay.addLayout(flip_row)

    def _emit_flip(self) -> None:
        self.requestFlip.emit(self.flip_h.isChecked(), self.flip_v.isChecked())


class ContrastPanel(QtWidgets.QFrame):
    """Live black/white-point sliders. Dragging either updates the display
    immediately (manual contrast). 'Auto' stretches to a percentile of the
    current frame and moves the sliders there."""
    requestLevels = QtCore.pyqtSignal(int, int)   # black, white
    requestAuto = QtCore.pyqtSignal()

    _MAXVAL = 65535

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("CONTRAST"); t.setObjectName("cardTitle")
        lay.addWidget(t)

        self.black_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.black_slider.setRange(0, self._MAXVAL)
        self.black_slider.setValue(0)
        self.white_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.white_slider.setRange(0, self._MAXVAL)
        self.white_slider.setValue(self._MAXVAL)

        self.black_label = QtWidgets.QLabel("Black: 0")
        self.white_label = QtWidgets.QLabel(f"White: {self._MAXVAL}")
        for w in (self.black_label, self.black_slider, self.white_label, self.white_slider):
            lay.addWidget(w)

        self.black_slider.valueChanged.connect(self._on_slider)
        self.white_slider.valueChanged.connect(self._on_slider)

        auto_btn = QtWidgets.QPushButton("Auto")
        auto_btn.setToolTip("Stretch to 1–99.5% of the current frame")
        auto_btn.clicked.connect(self.requestAuto.emit)
        lay.addWidget(auto_btn)

    def _on_slider(self, _v: int) -> None:
        black = self.black_slider.value()
        white = self.white_slider.value()
        # Keep black strictly below white without fighting the user mid-drag.
        if black >= white:
            if self.sender() is self.black_slider:
                white = min(self._MAXVAL, black + 1)
                self.white_slider.blockSignals(True); self.white_slider.setValue(white); self.white_slider.blockSignals(False)
            else:
                black = max(0, white - 1)
                self.black_slider.blockSignals(True); self.black_slider.setValue(black); self.black_slider.blockSignals(False)
        self.black_label.setText(f"Black: {black}")
        self.white_label.setText(f"White: {white}")
        self.requestLevels.emit(black, white)

    def set_levels(self, black: int, white: int) -> None:
        """Sync slider positions (e.g. after Auto) without re-emitting."""
        black = int(black); white = int(white)
        for s, v in ((self.black_slider, black), (self.white_slider, white)):
            s.blockSignals(True)
            s.setValue(max(0, min(self._MAXVAL, v)))
            s.blockSignals(False)
        self.black_label.setText(f"Black: {black}")
        self.white_label.setText(f"White: {white}")


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
