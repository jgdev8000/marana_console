"""KINETIC tab — burst parameters, progress, scrubber, save buttons."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


RAM_GUARD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    nf = float(n)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        nf /= 1024.0
        if nf < 1024:
            return f"{nf:.2f} {unit}"
    return f"{nf:.2f} TiB"


class KineticPanel(QtWidgets.QWidget):
    requestStartKinetic = QtCore.pyqtSignal(int, float, float)
    requestConfirmKinetic = QtCore.pyqtSignal()
    requestCancelKinetic = QtCore.pyqtSignal()
    requestSaveFrame = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Burst params card
        params = self._card("BURST PARAMETERS")
        grid = QtWidgets.QGridLayout()
        params.layout().addLayout(grid)
        grid.addWidget(QtWidgets.QLabel("Frames:"), 0, 0)
        self.frames_spin = QtWidgets.QSpinBox()
        self.frames_spin.setRange(1, 10000)
        self.frames_spin.setValue(200)
        grid.addWidget(self.frames_spin, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Exposure:"), 1, 0)
        self.exposure_spin = QtWidgets.QDoubleSpinBox()
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setRange(0.0001, 60.0)
        self.exposure_spin.setValue(0.005)
        self.exposure_spin.setSuffix(" s")
        grid.addWidget(self.exposure_spin, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Target FPS:"), 2, 0)
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.1, 200.0)
        self.fps_spin.setValue(100.0)
        grid.addWidget(self.fps_spin, 2, 1)
        self.aoi_label = QtWidgets.QLabel("AOI: --")
        self.aoi_label.setStyleSheet("color: #22d3ee;")
        params.layout().addWidget(self.aoi_label)
        self.ram_label = QtWidgets.QLabel("Memory: --")
        self.ram_label.setStyleSheet("color: #facc15;")
        params.layout().addWidget(self.ram_label)
        outer.addWidget(params)

        # Buttons
        btns = self._card("ACTIONS")
        self.start_btn = QtWidgets.QPushButton("START")
        self.start_btn.clicked.connect(self._on_start)
        btns.layout().addWidget(self.start_btn)
        self.stop_btn = QtWidgets.QPushButton("STOP")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.clicked.connect(self.requestCancelKinetic.emit)
        self.stop_btn.setEnabled(False)
        btns.layout().addWidget(self.stop_btn)
        outer.addWidget(btns)

        # Progress
        prog = self._card("PROGRESS")
        self.progress_bar = QtWidgets.QProgressBar()
        prog.layout().addWidget(self.progress_bar)
        self.progress_label = QtWidgets.QLabel("Idle")
        prog.layout().addWidget(self.progress_label)
        outer.addWidget(prog)

        # Save (the stack auto-saves on completion; this shows where)
        save = self._card("SAVE")
        self.saved_label = QtWidgets.QLabel("—")
        self.saved_label.setWordWrap(True)
        self.saved_label.setStyleSheet("color: #94a3b8;")
        save.layout().addWidget(self.saved_label)
        self.save_frame_btn = QtWidgets.QPushButton("SAVE FRAME…")
        self.save_frame_btn.setEnabled(False)
        self.save_frame_btn.clicked.connect(lambda: self.requestSaveFrame.emit(self.scrubber.value()))
        save.layout().addWidget(self.save_frame_btn)
        outer.addWidget(save)

        outer.addStretch(1)

        # The scrubber lives here for ownership; MainWindow re-parents it into the strip.
        self.scrubber = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.scrubber.setMinimum(0)
        self.scrubber.setMaximum(0)
        self.scrubber_label = QtWidgets.QLabel("Frame 0 / 0")
        self.scrubber.valueChanged.connect(self._update_scrubber_label)

        self._aoi_for_estimate: tuple[int, int, int, int] | None = None
        self.frames_spin.valueChanged.connect(self._refresh_estimate)
        self.fps_spin.valueChanged.connect(self._refresh_estimate)
        self.exposure_spin.valueChanged.connect(self._refresh_estimate)

    def _card(self, title: str) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        f.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(f)
        lay.setContentsMargins(0, 0, 0, 8); lay.setSpacing(4)
        t = QtWidgets.QLabel(title); t.setObjectName("cardTitle")
        lay.addWidget(t)
        return f

    def set_aoi_for_estimate(self, x0: int, x1: int, y0: int, y1: int) -> None:
        self._aoi_for_estimate = (x0, x1, y0, y1)
        self.aoi_label.setText(f"AOI: {x1 - x0 + 1}×{y1 - y0 + 1} @ ({x0},{y0})")
        self._refresh_estimate()

    def _refresh_estimate(self) -> None:
        if self._aoi_for_estimate is None:
            self.ram_label.setText("Memory: --")
            return
        x0, x1, y0, y1 = self._aoi_for_estimate
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        n = self.frames_spin.value()
        budget = n * w * h * 2
        flag = " ⚠" if budget > RAM_GUARD_BYTES else ""
        self.ram_label.setText(f"Memory: {_human_bytes(budget)}{flag}")

    def _on_start(self) -> None:
        self.requestStartKinetic.emit(
            self.frames_spin.value(),
            self.exposure_spin.value(),
            self.fps_spin.value(),
        )

    def on_kinetic_budget_reply(self, ram_estimate: int, ram_free: int) -> None:
        if ram_estimate > RAM_GUARD_BYTES or (ram_free and ram_estimate > 0.8 * ram_free):
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setText("Large kinetic burst")
            box.setInformativeText(
                f"This run will allocate {_human_bytes(ram_estimate)} on the server.\n\n"
                f"Proceed?"
            )
            box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
            if box.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.requestConfirmKinetic.emit()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setRange(0, self.frames_spin.value())
        self.progress_bar.setValue(0)
        self.progress_label.setText("Acquiring…")

    def on_progress(self, done: int, total: int, fps: float) -> None:
        self.progress_bar.setValue(done)
        self.progress_label.setText(f"Frame {done} / {total}  —  {fps:.1f} fps")

    def on_complete(self, done: int, total: int, partial: bool) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.save_frame_btn.setEnabled(done > 0)
        msg = "Cancelled" if partial else "Complete"
        self.progress_label.setText(f"{msg}: {done} frames captured")
        if done > 0:
            self.scrubber.setMaximum(done - 1)
            self.scrubber.setValue(0)
            self._update_scrubber_label(0)

    def show_saved(self, path: str) -> None:
        """Display where the stack was auto-saved (called after the server save)."""
        self.saved_label.setText(f"Stack saved to {path}")

    def _update_scrubber_label(self, v: int) -> None:
        self.scrubber_label.setText(f"Frame {v} / {self.scrubber.maximum()}")
