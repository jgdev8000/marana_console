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
        """Update the live readouts (sensor temp + status) on every event. The
        Enable checkbox and Target are USER INPUTS applied via APPLY, so they are
        only synced to the camera ONCE (at first update) — otherwise periodic
        temperature events would clear the user's selection before they can APPLY."""
        if not self._controls_synced:
            self.enable_cb.blockSignals(True); self.enable_cb.setChecked(enabled); self.enable_cb.blockSignals(False)
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
    """Contrast = an auto baseline (set on live-start and each snap) plus two
    fine offset sliders centered at the middle (0 = pure auto). Sliding or
    arrowing pushes the black/white point relative to the auto result, live.
    'Auto' re-stretches and re-centers the sliders."""
    requestOffsets = QtCore.pyqtSignal(int, int)   # black %, white %  (-100..100)
    requestAuto = QtCore.pyqtSignal()

    _RANGE = 100   # offset range: ±100% of the auto span

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel("CONTRAST"); t.setObjectName("cardTitle")
        lay.addWidget(t)

        self.black_slider, self.black_label = self._make_row(lay, "Black")
        self.white_slider, self.white_label = self._make_row(lay, "White")
        self.black_slider.valueChanged.connect(self._on_change)
        self.white_slider.valueChanged.connect(self._on_change)

        auto_btn = QtWidgets.QPushButton("Auto")
        auto_btn.setToolTip("Re-stretch to 1–99.5% of the current frame and re-center")
        auto_btn.clicked.connect(self.requestAuto.emit)
        lay.addWidget(auto_btn)

    def _make_row(self, parent_lay, name: str):
        label = QtWidgets.QLabel(f"{name}: 0")
        parent_lay.addWidget(label)
        row = QtWidgets.QHBoxLayout()
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(-self._RANGE, self._RANGE)
        slider.setValue(0)                 # centered = pure auto
        left = QtWidgets.QPushButton("◄"); left.setFixedWidth(28)
        right = QtWidgets.QPushButton("►"); right.setFixedWidth(28)
        left.setToolTip("nudge −1%"); right.setToolTip("nudge +1%")
        left.clicked.connect(lambda: slider.setValue(slider.value() - 1))   # arrow = 1% fine step
        right.clicked.connect(lambda: slider.setValue(slider.value() + 1))
        row.addWidget(left); row.addWidget(slider, stretch=1); row.addWidget(right)
        parent_lay.addLayout(row)
        return slider, label

    def _on_change(self, _v: int) -> None:
        black = self.black_slider.value()
        white = self.white_slider.value()
        self.black_label.setText(f"Black: {black:+d}%")
        self.white_label.setText(f"White: {white:+d}%")
        self.requestOffsets.emit(black, white)

    def center(self) -> None:
        """Reset both offset sliders to the middle (pure auto), no re-emit."""
        for s in (self.black_slider, self.white_slider):
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        self.black_label.setText("Black: +0%")
        self.white_label.setText("White: +0%")


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
