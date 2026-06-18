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
        self._controls_synced = False

    def update_cooling(self, enabled: bool, target_c: float, sensor_temp_c: float, status: str) -> None:
        """Update the live readouts (sensor temp + status) on every event.

        The Enable checkbox always starts OFF and is a pure user-intent input
        (applied via APPLY) — it is never auto-checked from the camera, so the
        operator must consciously turn cooling on. The actual cooling state is
        shown by the Status readout. The Target is synced to the camera's setpoint
        once at first update, then left to the user."""
        if not self._controls_synced:
            self.target_spin.blockSignals(True); self.target_spin.setValue(target_c); self.target_spin.blockSignals(False)
            self._controls_synced = True
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
    """Contrast = black/white display levels in absolute pixel values (0..65535),
    the single source of truth shared with the image's histogram. Typing a value
    or dragging the histogram updates the other; 'Auto' computes a best-fit +
    Solis-like window."""
    requestSetLevels = QtCore.pyqtSignal(float, float)   # black, white pixel values
    requestAuto = QtCore.pyqtSignal()

    _MAX = 65535

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("CONTRAST"); t.setObjectName("cardTitle")
        lay.addWidget(t)

        self.black_spin = self._make_row(lay, "Black", 0)
        self.white_spin = self._make_row(lay, "White", self._MAX)
        self.black_spin.valueChanged.connect(self._on_change)
        self.white_spin.valueChanged.connect(self._on_change)

        auto_btn = QtWidgets.QPushButton("Auto")
        auto_btn.setToolTip("Best-fit + Solis-like window from the current frame")
        auto_btn.clicked.connect(self.requestAuto.emit)
        lay.addWidget(auto_btn)

    def _make_row(self, parent_lay, name: str, default: int):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(f"{name}:"))
        spin = QtWidgets.QSpinBox()
        spin.setRange(0, self._MAX)
        spin.setSingleStep(50)
        spin.setValue(default)
        row.addWidget(spin, stretch=1)
        parent_lay.addLayout(row)
        return spin

    def _on_change(self, _v: int) -> None:
        self.requestSetLevels.emit(float(self.black_spin.value()), float(self.white_spin.value()))

    def set_values(self, lo: float, hi: float) -> None:
        """Mirror the current levels into the boxes (from auto or histogram drag),
        without re-emitting."""
        for spin, v in ((self.black_spin, lo), (self.white_spin, hi)):
            spin.blockSignals(True)
            spin.setValue(int(round(max(0, min(self._MAX, v)))))
            spin.blockSignals(False)


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
