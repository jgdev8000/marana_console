"""Main application window — three-column layout, tabbed left, status bar with connection card."""
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from marana_client.ui.connection_card import ConnectionCard


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, host: str):
        super().__init__()
        self.setWindowTitle("Marana Console")
        self.resize(1400, 850)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Left column: tab widget (Live / Kinetic)
        self.left_tabs = QtWidgets.QTabWidget()
        self.left_tabs.setObjectName("leftTabs")
        self.live_tab = QtWidgets.QWidget()
        self.kinetic_tab = QtWidgets.QWidget()
        self.left_tabs.addTab(self.live_tab, "LIVE")
        self.left_tabs.addTab(self.kinetic_tab, "KINETIC")
        self.left_tabs.setFixedWidth(320)
        root.addWidget(self.left_tabs)

        # Center column: ImageView placeholder + kinetic scrubber strip
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.image_view_container = QtWidgets.QWidget()
        center_layout.addWidget(self.image_view_container, stretch=1)
        self.scrubber_strip = QtWidgets.QWidget()
        self.scrubber_strip.setFixedHeight(40)
        self.scrubber_strip.hide()
        center_layout.addWidget(self.scrubber_strip)
        root.addWidget(center, stretch=1)

        # Right column: side panels stacked vertically
        self.right_column = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(self.right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self.right_column.setFixedWidth(280)
        root.addWidget(self.right_column)

        # Status bar
        self.statusBar().setSizeGripEnabled(False)
        self.live_indicator = QtWidgets.QLabel("[STOPPED]")
        self.live_indicator.setStyleSheet("color: #94a3b8; padding: 0 12px;")
        self.cam_label = QtWidgets.QLabel("cam: --")
        self.cam_label.setStyleSheet("color: #94a3b8; padding: 0 12px;")
        self.temp_label = QtWidgets.QLabel("T: -- °C")
        self.temp_label.setStyleSheet("color: #94a3b8; padding: 0 12px;")
        self.connection_card = ConnectionCard()
        self.connection_card.set_host(host)
        self.statusBar().addWidget(self.live_indicator)
        self.statusBar().addWidget(self.cam_label)
        self.statusBar().addWidget(self.temp_label)
        self.statusBar().addPermanentWidget(self.connection_card)

    def set_camera_info(self, model: str, serial: str) -> None:
        self.cam_label.setText(f"cam: {model} {serial}")

    def set_temperature(self, t_c: float, status: str) -> None:
        self.temp_label.setText(f"T: {t_c:+.1f} °C ({status})")

    def set_live_indicator(self, on: bool) -> None:
        if on:
            self.live_indicator.setText("● LIVE")
            self.live_indicator.setStyleSheet("color: #22d3ee; padding: 0 12px; font-weight: bold;")
        else:
            self.live_indicator.setText("[STOPPED]")
            self.live_indicator.setStyleSheet("color: #94a3b8; padding: 0 12px;")

    def install_image_view(self, image_view: QtWidgets.QWidget) -> None:
        lay = QtWidgets.QVBoxLayout(self.image_view_container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(image_view)
        self._image_view = image_view

    def install_left_panels(self, live_panel: QtWidgets.QWidget, kinetic_panel: QtWidgets.QWidget) -> None:
        lay_l = QtWidgets.QVBoxLayout(self.live_tab)
        lay_l.setContentsMargins(0, 0, 0, 0); lay_l.addWidget(live_panel)
        lay_k = QtWidgets.QVBoxLayout(self.kinetic_tab)
        lay_k.setContentsMargins(0, 0, 0, 0); lay_k.addWidget(kinetic_panel)
        self._live_panel = live_panel
        self._kinetic_panel = kinetic_panel
        # Re-parent the scrubber into the strip
        strip_lay = QtWidgets.QHBoxLayout(self.scrubber_strip)
        strip_lay.setContentsMargins(8, 4, 8, 4)
        strip_lay.addWidget(kinetic_panel.scrubber_label)
        strip_lay.addWidget(kinetic_panel.scrubber, stretch=1)

    def install_right_panels(self, cooling, display_p, contrast, status_log) -> None:
        for w in (cooling, display_p, contrast, status_log):
            self.right_column.layout().addWidget(w)
        self.right_column.layout().addStretch(1)
        self._cooling = cooling
        self._display = display_p
        self._contrast = contrast
        self._log = status_log

    def show_scrubber(self, show: bool) -> None:
        self.scrubber_strip.setVisible(show)
