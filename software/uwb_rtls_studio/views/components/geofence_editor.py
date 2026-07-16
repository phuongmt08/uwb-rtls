"""
==============================================================================
  UWB RTLS Studio - Geofence Editor Widget
==============================================================================
"""
import os
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

UI_FILE = os.path.join(os.path.dirname(__file__), "..", "ui", "geofence_editor.ui")


class GeofenceEditorWidget(QWidget):
    """Standalone widget for the Geofencing Editor panel.
    Loaded from geofence_editor.ui via uic.loadUi.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(UI_FILE, self)
        self._setup_ground_truth_tab()

    def _setup_ground_truth_tab(self):
        self.tab_ground_truth = QWidget(self.editor_tabs)
        layout = QVBoxLayout(self.tab_ground_truth)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.btn_mode_ground_truth = QPushButton("Draw Path", self.tab_ground_truth)
        self.btn_mode_ground_truth.setCheckable(True)
        self.btn_mode_ground_truth.setToolTip("Draw a ground-truth polyline on the map.")
        self.btn_finish_ground_truth = QPushButton("Finish", self.tab_ground_truth)
        self.btn_finish_ground_truth.setToolTip("Finish and save the current path.")
        self.btn_import_ground_truth = QPushButton("Import", self.tab_ground_truth)
        self.btn_import_ground_truth.setToolTip("Import ground-truth paths from JSON or GraphML XML.")
        self.btn_export_ground_truth = QPushButton("Export", self.tab_ground_truth)
        self.btn_export_ground_truth.setToolTip("Export saved ground-truth paths to a JSON file.")
        toolbar.addWidget(self.btn_mode_ground_truth)
        toolbar.addWidget(self.btn_finish_ground_truth)
        toolbar.addWidget(self.btn_import_ground_truth)
        toolbar.addWidget(self.btn_export_ground_truth)
        layout.addLayout(toolbar)

        form = QFormLayout()
        form.setSpacing(8)
        self.txt_ground_truth_name = QLineEdit(self.tab_ground_truth)
        self.txt_ground_truth_name.setPlaceholderText("Ground Truth name")
        self.cmb_ground_truth = QComboBox(self.tab_ground_truth)
        self.cmb_ground_truth.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow("Name:", self.txt_ground_truth_name)
        form.addRow("Saved paths:", self.cmb_ground_truth)
        layout.addLayout(form)

        self.chk_show_all_ground_truths = QCheckBox("Show all paths", self.tab_ground_truth)
        self.chk_show_all_ground_truths.setChecked(True)
        layout.addWidget(self.chk_show_all_ground_truths)

        self.lbl_ground_truth_status = QLabel("0 saved paths", self.tab_ground_truth)
        self.lbl_ground_truth_status.setStyleSheet("color: #94A3B8;")
        layout.addWidget(self.lbl_ground_truth_status)
        layout.addStretch(1)

        self.btn_delete_ground_truth = QPushButton("Delete Selected Path", self.tab_ground_truth)
        self.btn_delete_ground_truth.setStyleSheet(
            "QPushButton { color: #FCA5A5; border-color: #7F1D1D; }"
            "QPushButton:hover { background: #7F1D1D; color: #FFFFFF; }"
        )
        layout.addWidget(self.btn_delete_ground_truth)
        self.editor_tabs.addTab(self.tab_ground_truth, "Ground Truth")
