"""Dialog for picking a save path on the server's captures directory."""
from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtWidgets


class KineticSaveDialog(QtWidgets.QDialog):
    """list_dir_callable(subdir) -> {"entries": [...], "abs_path": str}"""

    def __init__(self, list_dir_callable: Callable[[str], dict],
                 default_subdir: str = "", default_name: str = "marana_kinetic.tif",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save kinetic stack on server")
        self.resize(620, 480)
        self._list = list_dir_callable
        self._current_subdir = default_subdir

        outer = QtWidgets.QVBoxLayout(self)

        nav_row = QtWidgets.QHBoxLayout()
        self.up_btn = QtWidgets.QPushButton("◄ Up")
        self.up_btn.clicked.connect(self._go_up)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        self.path_label = QtWidgets.QLabel("")
        self.path_label.setStyleSheet("color: #22d3ee;")
        nav_row.addWidget(self.up_btn)
        nav_row.addWidget(self.refresh_btn)
        nav_row.addWidget(self.path_label, stretch=1)
        outer.addLayout(nav_row)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        outer.addWidget(self.list_widget, stretch=1)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Filename:"))
        self.name_edit = QtWidgets.QLineEdit(default_name)
        name_row.addWidget(self.name_edit, stretch=1)
        outer.addLayout(name_row)

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)

        self._refresh()

    def chosen_relative_path(self) -> str:
        name = self.name_edit.text().strip() or "marana_kinetic.tif"
        if self._current_subdir:
            return f"{self._current_subdir.rstrip('/')}/{name}"
        return name

    def _refresh(self) -> None:
        try:
            payload = self._list(self._current_subdir)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Listing failed", str(e))
            return
        self.path_label.setText(f"server: {payload.get('abs_path', '')}")
        self.list_widget.clear()
        for entry in payload.get("entries", []):
            label = f"📁 {entry['name']}" if entry["is_dir"] else f"   {entry['name']}  ({entry['size']} B)"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, entry)
            self.list_widget.addItem(item)

    def _on_double_click(self, item: QtWidgets.QListWidgetItem) -> None:
        entry = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if entry["is_dir"]:
            sep = "/" if self._current_subdir and not self._current_subdir.endswith("/") else ""
            self._current_subdir = f"{self._current_subdir}{sep}{entry['name']}".lstrip("/")
            self._refresh()
        else:
            self.name_edit.setText(entry["name"])

    def _go_up(self) -> None:
        if not self._current_subdir:
            return
        if "/" in self._current_subdir.rstrip("/"):
            self._current_subdir = self._current_subdir.rstrip("/").rsplit("/", 1)[0]
        else:
            self._current_subdir = ""
        self._refresh()
