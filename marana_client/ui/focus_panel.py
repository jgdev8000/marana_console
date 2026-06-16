# -*- coding: utf-8 -*-
"""FOCUS tab — through-focus series controls."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

PV_BASE_REAL = "MCS2:zoneplate_z"
PV_BASE_SIM = "MCS2SIM:zoneplate_z"


class FocusPanel(QtWidgets.QWidget):
    requestStartFocus = QtCore.pyqtSignal(dict)
    requestConfirmFocus = QtCore.pyqtSignal()
    requestCancelFocus = QtCore.pyqtSignal()
    requestSaveFocusStack = QtCore.pyqtSignal()
    requestRefreshStartZ = QtCore.pyqtSignal(str)        # mover_pv_base
    requestSetMoverSource = QtCore.pyqtSignal(str)       # "sim" | "real"

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Motor source card
        src = self._card("MOTOR SOURCE")
        self.src_real = QtWidgets.QRadioButton(f"Real  ({PV_BASE_REAL})")
        self.src_sim = QtWidgets.QRadioButton(f"Sim   ({PV_BASE_SIM})")
        self.src_sim.setChecked(True)
        src.layout().addWidget(self.src_real)
        src.layout().addWidget(self.src_sim)
        z_row = QtWidgets.QHBoxLayout()
        z_row.addWidget(QtWidgets.QLabel("Start Z:"))
        self.start_z_label = QtWidgets.QLabel("--")
        self.start_z_label.setStyleSheet("color: #facc15;")
        z_row.addWidget(self.start_z_label, stretch=1)
        self.refresh_btn = QtWidgets.QPushButton("⟲")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.clicked.connect(lambda: self.requestRefreshStartZ.emit(self.mover_pv_base()))
        z_row.addWidget(self.refresh_btn)
        src.layout().addLayout(z_row)
        outer.addWidget(src)

        self.src_real.toggled.connect(self._on_source_toggle)
        self.src_sim.toggled.connect(self._on_source_toggle)

        # Range card (direction is now fixed: negative‑first)
        rng = self._card("RANGE")
        grid = QtWidgets.QGridLayout()
        rng.layout().addLayout(grid)
        grid.addWidget(QtWidgets.QLabel("Range (µm):"), 0, 0)
        self.range_spin = QtWidgets.QDoubleSpinBox()
        self.range_spin.setRange(0.001, 20000.0)
        self.range_spin.setDecimals(3)
        self.range_spin.setValue(100.0)
        grid.addWidget(self.range_spin, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Step (µm):"), 1, 0)
        self.step_spin = QtWidgets.QDoubleSpinBox()
        self.step_spin.setRange(0.001, 20000.0)
        self.step_spin.setDecimals(3)
        self.step_spin.setValue(5.0)
        grid.addWidget(self.step_spin, 1, 1)
        self.derived_label = QtWidgets.QLabel("Stops: --    End Z: --")
        self.derived_label.setStyleSheet("color: #94a3b8;")
        rng.layout().addWidget(self.derived_label)
        self.aoi_label = QtWidgets.QLabel("AOI: --")
        self.aoi_label.setStyleSheet("color: #22d3ee;")
        rng.layout().addWidget(self.aoi_label)
        outer.addWidget(rng)

        for w in (self.range_spin, self.step_spin):
            w.valueChanged.connect(self._refresh_derived)

        # Acquisition card
        acq = self._card("ACQUISITION")
        grid2 = QtWidgets.QGridLayout()
        acq.layout().addLayout(grid2)
        grid2.addWidget(QtWidgets.QLabel("Exposure (s):"), 0, 0)
        self.exposure_spin = QtWidgets.QDoubleSpinBox()
        self.exposure_spin.setDecimals(4); self.exposure_spin.setRange(0.0001, 60.0); self.exposure_spin.setValue(0.05)
        grid2.addWidget(self.exposure_spin, 0, 1)
        grid2.addWidget(QtWidgets.QLabel("Settle (ms):"), 1, 0)
        self.settle_spin = QtWidgets.QSpinBox()
        self.settle_spin.setRange(0, 10000); self.settle_spin.setValue(100)
        grid2.addWidget(self.settle_spin, 1, 1)
        self.return_cb = QtWidgets.QCheckBox("Return to start")
        self.return_cb.setChecked(True)
        acq.layout().addWidget(self.return_cb)
        self.est_label = QtWidgets.QLabel("Est. time: --")
        self.est_label.setStyleSheet("color: #facc15;")
        acq.layout().addWidget(self.est_label)
        outer.addWidget(acq)

        for w in (self.exposure_spin, self.settle_spin):
            w.valueChanged.connect(self._refresh_derived)

        # Actions
        btns = self._card("ACTIONS")
        self.start_btn = QtWidgets.QPushButton("START")
        self.start_btn.clicked.connect(self._on_start)
        btns.layout().addWidget(self.start_btn)
        self.stop_btn = QtWidgets.QPushButton("STOP")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.requestCancelFocus.emit)
        btns.layout().addWidget(self.stop_btn)
        outer.addWidget(btns)

        # Progress
        prog = self._card("PROGRESS")
        self.progress_bar = QtWidgets.QProgressBar()
        prog.layout().addWidget(self.progress_bar)
        self.progress_label = QtWidgets.QLabel("Idle")
        prog.layout().addWidget(self.progress_label)
        outer.addWidget(prog)

        # Save
        save = self._card("SAVE")
        self.save_btn = QtWidgets.QPushButton("SAVE STACK…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.requestSaveFocusStack.emit)
        save.layout().addWidget(self.save_btn)
        outer.addWidget(save)

        outer.addStretch(1)

        self._start_z_um: float | None = None
        self._dllm_um: float | None = None
        self._dhlm_um: float | None = None
        self._refresh_derived()

    def _card(self, title: str) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        f.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(f)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel(title); t.setObjectName("cardTitle")
        lay.addWidget(t)
        return f

    def mover_pv_base(self) -> str:
        return PV_BASE_REAL if self.src_real.isChecked() else PV_BASE_SIM

    def direction(self) -> int:
        """Direction is fixed to -1 (negative‑first) for the new sweep."""
        return -1

    def _on_source_toggle(self, checked: bool) -> None:
        if not checked:
            return
        which = "real" if self.src_real.isChecked() else "sim"
        self.requestSetMoverSource.emit(which)
        self.requestRefreshStartZ.emit(self.mover_pv_base())

    def set_start_z_um(self, z_um: float, dllm_um: float | None = None, dhlm_um: float | None = None) -> None:
        self._start_z_um = z_um
        if dllm_um is not None:
            self._dllm_um = dllm_um
        if dhlm_um is not None:
            self._dhlm_um = dhlm_um
        self.start_z_label.setText(f"{z_um:+.3f} µm")
        self._refresh_derived()

    def set_aoi(self, x0: int, x1: int, y0: int, y1: int) -> None:
        """Display the current camera AOI (each Z snapshot is taken at this AOI)."""
        self.aoi_label.setText(f"AOI: {x1 - x0 + 1}×{y1 - y0 + 1} @ ({x0},{y0})")

    def apply_persisted_state(self, cfg: dict) -> None:
        if cfg.get("mover_source") == "real":
            self.src_real.setChecked(True)
        else:
            self.src_sim.setChecked(True)
        self.range_spin.setValue(float(cfg.get("focus_range_um", 100.0)))
        self.step_spin.setValue(float(cfg.get("focus_step_um", 5.0)))
        self.exposure_spin.setValue(float(cfg.get("focus_exposure_s", 0.05)))
        self.settle_spin.setValue(int(cfg.get("focus_settle_ms", 100)))
        self.return_cb.setChecked(bool(cfg.get("focus_return_to_start", True)))

    def current_params(self) -> dict:
        return {
            "mover_pv_base": self.mover_pv_base(),
            "direction": -1,
            "range_um": float(self.range_spin.value()),
            "step_um": float(self.step_spin.value()),
            "exposure_s": float(self.exposure_spin.value()),
            "settle_ms": int(self.settle_spin.value()),
            "return_to_start": self.return_cb.isChecked(),
        }

    def _refresh_derived(self) -> None:
        rng = self.range_spin.value()
        step = self.step_spin.value()
        if step <= 0:
            self.derived_label.setText("Stops: --    End Z: --")
            return
        half_steps = int((rng / 2) // step)
        stops = 1 + 2 * half_steps
        if self._start_z_um is None:
            self.derived_label.setText(f"Stops: {stops}    End Z: --")
        else:
            end_z = self._start_z_um + half_steps * step
            self.derived_label.setText(f"Stops: {stops}    End Z: {end_z:+.3f} µm")
        per_step = max(0.02, step * 1e-3) + self.settle_spin.value() / 1000.0 + self.exposure_spin.value() + 0.05
        self.est_label.setText(f"Est. time: {stops * per_step:.1f} s")

    def _on_start(self) -> None:
        self.requestStartFocus.emit(self.current_params())

    def on_plan_reply(self, plan: dict) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setWindowTitle("Confirm through-focus series")
        box.setText("Run through-focus series?")
        box.setInformativeText(
            f"Start Z: {plan['z_start_um']:+.3f} µm\n"
            f"End Z:   {plan['z_end_um']:+.3f} µm\n"
            f"Stops:   {plan['stop_count']}\n"
            f"Estimated time: {plan['est_time_s']:.1f} s\n\n"
            f"Limits: {plan['dllm_mm']*1e3:+.0f} .. {plan['dhlm_mm']*1e3:+.0f} µm"
        )
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if box.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.requestConfirmFocus.emit()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self.progress_bar.setRange(0, plan["stop_count"])
        self.progress_bar.setValue(0)
        self.progress_label.setText("Acquiring…")

    def on_focus_progress(self, idx: int, total: int, z_um: float) -> None:
        self.progress_bar.setValue(idx + 1)
        self.progress_label.setText(f"{idx+1} / {total}   z = {z_um:+.3f} µm")

    def on_focus_complete(self, done: int, total: int, partial: bool) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.save_btn.setEnabled(done > 0)
        msg = "Cancelled" if partial else "Complete"
        self.progress_label.setText(f"{msg}: {done} frames captured")