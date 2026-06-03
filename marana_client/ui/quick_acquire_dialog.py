"""Modal for the ACQUIRE & SAVE… quick-acquisition button.

Collects exposure, gain mode and a frame count, then the caller runs a one-shot
kinetic burst with those settings and saves the result (frames=1 -> a single-
frame stack). The caller restores the prior exposure/gain afterward.
"""
from __future__ import annotations

from PyQt6 import QtWidgets


class QuickAcquireDialog(QtWidgets.QDialog):
    def __init__(self, exposure_s: float, gain_options: list[str] | None = None,
                 current_gain: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acquire & Save")
        self.setModal(True)

        form = QtWidgets.QFormLayout(self)

        self.exposure_spin = QtWidgets.QDoubleSpinBox()
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setRange(0.0001, 60.0)
        self.exposure_spin.setSingleStep(0.001)
        self.exposure_spin.setSuffix(" s")
        self.exposure_spin.setValue(float(exposure_s))
        form.addRow("Exposure:", self.exposure_spin)

        # Gain row is shown only on cameras that expose GainMode (hidden on SimCam).
        self.gain_combo = QtWidgets.QComboBox()
        for opt in (gain_options or []):
            self.gain_combo.addItem(opt)
        if current_gain is not None:
            idx = self.gain_combo.findText(str(current_gain))
            if idx >= 0:
                self.gain_combo.setCurrentIndex(idx)
        self._gain_row_label = QtWidgets.QLabel("Gain:")
        form.addRow(self._gain_row_label, self.gain_combo)
        if self.gain_combo.count() == 0:
            self._gain_row_label.setVisible(False)
            self.gain_combo.setVisible(False)

        self.frames_spin = QtWidgets.QSpinBox()
        self.frames_spin.setRange(1, 10000)
        self.frames_spin.setValue(1)
        form.addRow("Frames:", self.frames_spin)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        """Return {'exposure_s', 'gain' (str|None), 'frame_count'}."""
        gain = self.gain_combo.currentText() if self.gain_combo.count() > 0 else None
        return {
            "exposure_s": float(self.exposure_spin.value()),
            "gain": gain,
            "frame_count": int(self.frames_spin.value()),
        }
