"""Status-bar connection indicator + popup."""
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets


class ConnectionCard(QtWidgets.QFrame):
    STATE_DISCONNECTED = 0
    STATE_DEGRADED = 1
    STATE_HEALTHY = 2

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("connectionCard")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        self._dot = QtWidgets.QLabel("●")
        self._dot.setStyleSheet("color: #ef4444; font-size: 14pt;")
        self._label = QtWidgets.QLabel("DISCONNECTED")
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        self._state = self.STATE_DISCONNECTED
        self._host = ""

    def set_host(self, host: str) -> None:
        self._host = host
        self._refresh_label()

    @property
    def current_state(self) -> int:
        return self._state

    def mark_healthy(self) -> None:
        """Promote to HEALTHY on any sign of life (a successful round-trip or an
        inbound frame/status event). No-op if already healthy so the hot path
        (every live frame) doesn't churn the stylesheet."""
        if self._state != self.STATE_HEALTHY:
            self.set_state(self.STATE_HEALTHY)

    def set_state(self, state: int) -> None:
        self._state = state
        if state == self.STATE_HEALTHY:
            self._dot.setStyleSheet("color: #10b981; font-size: 14pt;")
        elif state == self.STATE_DEGRADED:
            self._dot.setStyleSheet("color: #f59e0b; font-size: 14pt;")
        else:
            self._dot.setStyleSheet("color: #ef4444; font-size: 14pt;")
        self._refresh_label()

    def _refresh_label(self) -> None:
        names = {0: "DISCONNECTED", 1: "DEGRADED", 2: "CONNECTED"}
        name = names.get(self._state, "?")
        host = f" — {self._host}" if self._host else ""
        self._label.setText(f"{name}{host}")
