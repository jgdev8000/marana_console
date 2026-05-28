"""LIVE tab — exposure, speed, encoding, shutter, AOI, capture buttons."""
from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtWidgets


class LivePanel(QtWidgets.QWidget):
    requestSetFeature = QtCore.pyqtSignal(str, object)
    requestStartLive = QtCore.pyqtSignal()
    requestStop = QtCore.pyqtSignal()
    requestSnapNow = QtCore.pyqtSignal()
    requestAcquireAndSave = QtCore.pyqtSignal()
    requestSetAoi = QtCore.pyqtSignal(int, int, int, int)
    requestSetAoiFull = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Exposure card
        exp_card = self._make_card("EXPOSURE")
        self.exposure_spin = QtWidgets.QDoubleSpinBox()
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setRange(0.0001, 60.0)
        self.exposure_spin.setSingleStep(0.001)
        self.exposure_spin.setValue(0.05)
        self.exposure_spin.setSuffix(" s")
        self.exposure_spin.editingFinished.connect(
            lambda: self.requestSetFeature.emit("ExposureTime", self.exposure_spin.value()))
        exp_card.layout().addWidget(self.exposure_spin)
        self.fps_label = QtWidgets.QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #facc15;")
        exp_card.layout().addWidget(self.fps_label)
        outer.addWidget(exp_card)

        # Modes card
        modes_card = self._make_card("MODES")
        modes_grid = QtWidgets.QGridLayout()
        modes_card.layout().addLayout(modes_grid)

        modes_grid.addWidget(QtWidgets.QLabel("Speed:"), 0, 0)
        self.speed_combo = QtWidgets.QComboBox()
        self.speed_combo.currentTextChanged.connect(
            lambda v: self.requestSetFeature.emit("PixelReadoutRate", v))
        modes_grid.addWidget(self.speed_combo, 0, 1)

        modes_grid.addWidget(QtWidgets.QLabel("Encoding:"), 1, 0)
        self.encoding_combo = QtWidgets.QComboBox()
        self.encoding_combo.currentTextChanged.connect(
            lambda v: self.requestSetFeature.emit("PixelEncoding", v))
        modes_grid.addWidget(self.encoding_combo, 1, 1)

        modes_grid.addWidget(QtWidgets.QLabel("Shutter:"), 2, 0)
        self.shutter_combo = QtWidgets.QComboBox()
        self.shutter_combo.currentTextChanged.connect(
            lambda v: self.requestSetFeature.emit("ElectronicShutteringMode", v))
        modes_grid.addWidget(self.shutter_combo, 2, 1)
        outer.addWidget(modes_card)

        # AOI card
        aoi_card = self._make_card("AOI / ROI")
        aoi_grid = QtWidgets.QGridLayout()
        aoi_card.layout().addLayout(aoi_grid)
        labels = ("L", "T", "W", "H")
        self.aoi_spins: dict[str, QtWidgets.QSpinBox] = {}
        for i, lab in enumerate(labels):
            aoi_grid.addWidget(QtWidgets.QLabel(lab), 0, i * 2)
            spin = QtWidgets.QSpinBox()
            spin.setRange(0, 65535)
            aoi_grid.addWidget(spin, 0, i * 2 + 1)
            self.aoi_spins[lab] = spin
        btn_row = QtWidgets.QHBoxLayout()
        full_btn = QtWidgets.QPushButton("FULL")
        full_btn.clicked.connect(self.requestSetAoiFull.emit)
        set_btn = QtWidgets.QPushButton("SET")
        set_btn.clicked.connect(self._emit_aoi)
        btn_row.addWidget(full_btn); btn_row.addWidget(set_btn)
        aoi_card.layout().addLayout(btn_row)
        outer.addWidget(aoi_card)

        # Capture card
        cap_card = self._make_card("CAPTURE")
        self.live_button = QtWidgets.QPushButton("LIVE")
        self.live_button.setObjectName("liveButton")
        self.live_button.setCheckable(True)
        self.live_button.toggled.connect(self._on_live_toggled)
        cap_card.layout().addWidget(self.live_button)
        self.stop_button = QtWidgets.QPushButton("STOP")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.requestStop.emit)
        cap_card.layout().addWidget(self.stop_button)
        cap_card.layout().addSpacing(8)
        self.snap_button = QtWidgets.QPushButton("SNAP NOW")
        self.snap_button.clicked.connect(self.requestSnapNow.emit)
        cap_card.layout().addWidget(self.snap_button)
        self.acq_save_button = QtWidgets.QPushButton("ACQUIRE && SAVE…")
        self.acq_save_button.clicked.connect(self.requestAcquireAndSave.emit)
        cap_card.layout().addWidget(self.acq_save_button)
        outer.addWidget(cap_card)

        outer.addStretch(1)

    def _make_card(self, title: str) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        f.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(f)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(4)
        title_lab = QtWidgets.QLabel(title)
        title_lab.setObjectName("cardTitle")
        lay.addWidget(title_lab)
        return f

    def populate_feature_options(self, options: dict[str, list[str]]) -> None:
        for combo, key in ((self.speed_combo, "PixelReadoutRate"),
                           (self.encoding_combo, "PixelEncoding"),
                           (self.shutter_combo, "ElectronicShutteringMode")):
            combo.blockSignals(True)
            combo.clear()
            for opt in options.get(key, []):
                combo.addItem(opt)
            combo.blockSignals(False)

    def set_current_values(self, values: dict) -> None:
        if "ExposureTime" in values:
            self.exposure_spin.blockSignals(True)
            self.exposure_spin.setValue(float(values["ExposureTime"]))
            self.exposure_spin.blockSignals(False)
        for key, combo in (("PixelReadoutRate", self.speed_combo),
                           ("PixelEncoding", self.encoding_combo),
                           ("ElectronicShutteringMode", self.shutter_combo)):
            if key in values:
                combo.blockSignals(True)
                idx = combo.findText(str(values[key]))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.blockSignals(False)

    def set_aoi_values(self, x0: int, x1: int, y0: int, y1: int) -> None:
        self.aoi_spins["L"].setValue(x0)
        self.aoi_spins["T"].setValue(y0)
        self.aoi_spins["W"].setValue(x1 - x0 + 1)
        self.aoi_spins["H"].setValue(y1 - y0 + 1)

    def set_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")

    def _emit_aoi(self) -> None:
        x0 = self.aoi_spins["L"].value()
        y0 = self.aoi_spins["T"].value()
        w = self.aoi_spins["W"].value()
        h = self.aoi_spins["H"].value()
        self.requestSetAoi.emit(x0, x0 + w - 1, y0, y0 + h - 1)

    def _on_live_toggled(self, on: bool) -> None:
        if on:
            self.requestStartLive.emit()
        else:
            self.requestStop.emit()
