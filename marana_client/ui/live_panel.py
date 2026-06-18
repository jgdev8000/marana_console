"""LIVE tab — exposure, speed, encoding, shutter, AOI, capture buttons."""
from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtWidgets


class LivePanel(QtWidgets.QWidget):
    requestSetFeature = QtCore.pyqtSignal(str, object)
    requestStartLive = QtCore.pyqtSignal()
    requestStop = QtCore.pyqtSignal()
    requestSnapDisplay = QtCore.pyqtSignal()   # acquire one frame, display only (no save)
    requestSnapNow = QtCore.pyqtSignal()
    requestSaveDisplayed = QtCore.pyqtSignal()  # save the currently displayed frame
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

        # Modes card — Gain is the only user choice; Speed + Encoding follow it
        # automatically (12-bit gain -> 200 MHz / Mono12, 16-bit gain -> 100 MHz /
        # Mono16). Shutter is omitted (Rolling is the only option on the Marana).
        modes_card = self._make_card("MODES")
        modes_grid = QtWidgets.QGridLayout()
        modes_card.layout().addLayout(modes_grid)

        # GainMode — the 12-bit-fast vs 16-bit-HDR control. Hidden on cameras
        # (e.g. SimCam) that don't expose it.
        self.gain_label = QtWidgets.QLabel("Gain:")
        modes_grid.addWidget(self.gain_label, 0, 0)
        self.gain_combo = QtWidgets.QComboBox()
        self.gain_combo.currentTextChanged.connect(
            lambda v: self.requestSetFeature.emit("GainMode", v))
        modes_grid.addWidget(self.gain_combo, 0, 1)

        # Read-only indicators (speed / encoding / bit depth / max FPS) — all
        # derived from the current gain mode.
        self.indicators_label = QtWidgets.QLabel("")
        self.indicators_label.setStyleSheet("color: #94a3b8;")
        self.indicators_label.setWordWrap(True)
        modes_card.layout().addWidget(self.indicators_label)
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
        self.snap_display_button = QtWidgets.QPushButton("SNAP")
        self.snap_display_button.setObjectName("snapButton")   # highlights only while pressed
        self.snap_display_button.setToolTip("Acquire one frame and display it (no save)")
        self.snap_display_button.clicked.connect(self.requestSnapDisplay.emit)
        cap_card.layout().addWidget(self.snap_display_button)
        self.save_button = QtWidgets.QPushButton("SAVE")
        self.save_button.setToolTip("Save the currently displayed frame (auto-named, with metadata)")
        self.save_button.clicked.connect(self.requestSaveDisplayed.emit)
        cap_card.layout().addWidget(self.save_button)
        self.snap_button = QtWidgets.QPushButton("SNAP && SAVE")
        self.snap_button.clicked.connect(self.requestSnapNow.emit)
        cap_card.layout().addWidget(self.snap_button)
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

    def set_exposure_value(self, exposure_s: float) -> None:
        self.exposure_spin.blockSignals(True)
        self.exposure_spin.setValue(float(exposure_s))
        self.exposure_spin.blockSignals(False)

    def populate_acq_settings(self, snapshot: dict) -> None:
        """Authoritative (re)fill of the Gain combo from a get_acq_settings
        snapshot, plus the read-only indicators (Speed / Encoding / BitDepth /
        max FPS) which all follow the current gain mode. Signals blocked so a
        repopulate never re-emits requestSetFeature."""
        options = snapshot.get("options", {})
        values = snapshot.get("values", {})
        readonly = snapshot.get("readonly", {})

        self.gain_combo.blockSignals(True)
        self.gain_combo.clear()
        for opt in options.get("GainMode", []) or []:
            self.gain_combo.addItem(opt)
        cur = values.get("GainMode")
        if cur is not None:
            idx = self.gain_combo.findText(str(cur))
            if idx >= 0:
                self.gain_combo.setCurrentIndex(idx)
        self.gain_combo.blockSignals(False)

        # GainMode hides itself when the camera doesn't expose it (e.g. SimCam)
        has_gain = bool(options.get("GainMode"))
        self.gain_label.setVisible(has_gain)
        self.gain_combo.setVisible(has_gain)

        # Read-only indicators — Speed + Encoding are derived from gain; show the
        # currently-selected value of each.
        def cur_or_first(key):
            v = values.get(key)
            if v is not None:
                return v
            opts = options.get(key) or []
            return opts[0] if opts else None
        parts = []
        speed = cur_or_first("PixelReadoutRate")
        if speed is not None:
            parts.append(f"Speed: {speed}")
        enc = cur_or_first("PixelEncoding")
        if enc is not None:
            parts.append(f"Encoding: {enc}")
        if readonly.get("bit_depth") is not None:
            parts.append(f"BitDepth: {readonly['bit_depth']}")
        if readonly.get("max_frame_rate_hz") is not None:
            parts.append(f"max FPS: {readonly['max_frame_rate_hz']:.1f}")
        self.indicators_label.setText("   ".join(parts))
        self.indicators_label.setVisible(bool(parts))

    def set_aoi_values(self, x0: int, x1: int, y0: int, y1: int) -> None:
        self.aoi_spins["L"].setValue(x0)
        self.aoi_spins["T"].setValue(y0)
        self.aoi_spins["W"].setValue(x1 - x0 + 1)
        self.aoi_spins["H"].setValue(y1 - y0 + 1)

    def set_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")

    def set_live_active(self, on: bool) -> None:
        """Reflect the actual live state on the LIVE button (lit when running).
        Signals blocked so syncing state doesn't re-trigger start/stop."""
        self.live_button.blockSignals(True)
        self.live_button.setChecked(on)
        self.live_button.blockSignals(False)

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
