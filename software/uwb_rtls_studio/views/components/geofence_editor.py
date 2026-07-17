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
    QDoubleSpinBox,
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

        # Apply compact styling to mode selector buttons in other tabs to prevent horizontal overflow
        compact_style = (
            "QPushButton { padding: 4px 4px; font-size: 11px; font-weight: normal; }"
            "QPushButton:checked { font-weight: bold; }"
        )
        self.btn_mode_room.setStyleSheet(compact_style + " QPushButton:checked { background-color: #F8FAFC; color: #0F172A; border-color: #CBD5E1; }")
        self.btn_mode_wall.setStyleSheet(compact_style + " QPushButton:checked { background-color: #64748B; color: white; border-color: #94A3B8; }")
        self.btn_mode_anchor.setStyleSheet(compact_style + " QPushButton:checked { background-color: #0891B2; color: white; border-color: #22D3EE; }")
        self.btn_mode_edit_map.setStyleSheet(compact_style + " QPushButton:checked { background-color: #2563EB; color: white; border-color: #3B82F6; }")

        self.btn_mode_draw.setStyleSheet(compact_style + " QPushButton:checked { background-color: #2563EB; color: white; border-color: #3B82F6; }")
        self.btn_mode_edit.setStyleSheet(compact_style + " QPushButton:checked { background-color: #2563EB; color: white; border-color: #3B82F6; }")

    def _setup_ground_truth_tab(self):
        self.tab_ground_truth = QWidget(self.editor_tabs)
        layout = QVBoxLayout(self.tab_ground_truth)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        compact_btn_style = (
            "QPushButton { padding: 4px 4px; font-size: 11px; font-weight: normal; }"
            "QPushButton:checked { font-weight: bold; background-color: #2563EB; color: white; border-color: #3B82F6; }"
        )
        self.btn_mode_ground_truth = QPushButton("Draw", self.tab_ground_truth)
        self.btn_mode_ground_truth.setCheckable(True)
        self.btn_mode_ground_truth.setToolTip("Click snapped points to draw a ground-truth path.")
        self.btn_mode_ground_truth.setStyleSheet(compact_btn_style)

        self.btn_edit_ground_truth = QPushButton("Edit", self.tab_ground_truth)
        self.btn_edit_ground_truth.setCheckable(True)
        self.btn_edit_ground_truth.setToolTip(
            "Finish the current path, then select two connected edges to apply Fillet or Chamfer."
        )
        self.btn_edit_ground_truth.setStyleSheet(compact_btn_style)
        self.btn_import_ground_truth = QPushButton("Import", self.tab_ground_truth)
        self.btn_import_ground_truth.setToolTip("Import ground-truth paths from JSON or GraphML XML.")
        self.btn_import_ground_truth.setStyleSheet(compact_btn_style)

        self.btn_export_ground_truth = QPushButton("Export", self.tab_ground_truth)
        self.btn_export_ground_truth.setToolTip("Export saved ground-truth paths to a JSON file.")
        self.btn_export_ground_truth.setStyleSheet(compact_btn_style)

        toolbar.addWidget(self.btn_mode_ground_truth)
        toolbar.addWidget(self.btn_edit_ground_truth)
        toolbar.addWidget(self.btn_import_ground_truth)
        toolbar.addWidget(self.btn_export_ground_truth)
        layout.addLayout(toolbar)

        corner_toolbar = QHBoxLayout()
        corner_toolbar.setSpacing(6)
        self.gt_corner_amount = QDoubleSpinBox(self.tab_ground_truth)
        self.gt_corner_amount.setRange(0.01, 20.0)
        self.gt_corner_amount.setDecimals(2)
        self.gt_corner_amount.setSingleStep(0.05)
        self.gt_corner_amount.setValue(0.20)
        self.gt_corner_amount.setSuffix(" m")
        self.gt_corner_amount.setToolTip("Fillet radius or chamfer distance for the selected corner.")
        self.btn_fillet_ground_truth = QPushButton("Fillet", self.tab_ground_truth)
        self.btn_fillet_ground_truth.setToolTip("Round the corner shared by the two selected edges.")
        self.btn_fillet_ground_truth.setStyleSheet(compact_btn_style)
        self.btn_fillet_ground_truth.setEnabled(False)

        self.btn_chamfer_ground_truth = QPushButton("Chamfer", self.tab_ground_truth)
        self.btn_chamfer_ground_truth.setToolTip("Cut the corner shared by the two selected edges.")
        self.btn_chamfer_ground_truth.setStyleSheet(compact_btn_style)
        self.btn_chamfer_ground_truth.setEnabled(False)

        self.btn_extend_ground_truth = QPushButton("Extend", self.tab_ground_truth)
        self.btn_extend_ground_truth.setToolTip("Extend two selected edges until their endpoints meet at one intersection.")
        self.btn_extend_ground_truth.setStyleSheet(compact_btn_style)
        self.btn_extend_ground_truth.setEnabled(False)
        self.btn_fillet_ground_truth.setMinimumWidth(0)
        self.btn_chamfer_ground_truth.setMinimumWidth(0)
        self.btn_extend_ground_truth.setMinimumWidth(0)
        self.btn_fillet_ground_truth.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_chamfer_ground_truth.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_extend_ground_truth.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        corner_toolbar.addWidget(QLabel("Corner:", self.tab_ground_truth))
        corner_toolbar.addWidget(self.gt_corner_amount)
        corner_toolbar.addStretch(1)
        layout.addLayout(corner_toolbar)

        corner_action_toolbar = QHBoxLayout()
        corner_action_toolbar.setSpacing(6)
        corner_action_toolbar.addWidget(self.btn_fillet_ground_truth)
        corner_action_toolbar.addWidget(self.btn_chamfer_ground_truth)
        corner_action_toolbar.addWidget(self.btn_extend_ground_truth)
        layout.addLayout(corner_action_toolbar)
        form = QFormLayout()
        form.setSpacing(8)
        self.txt_ground_truth_name = QLineEdit(self.tab_ground_truth)
        self.txt_ground_truth_name.setPlaceholderText("Ground Truth name")
        self.cmb_ground_truth = QComboBox(self.tab_ground_truth)
        self.cmb_ground_truth.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cmb_ground_truth.hide()
        self.btn_ground_truth_color = QPushButton("Choose color...", self.tab_ground_truth)
        self.btn_ground_truth_color.setToolTip("Choose the color used for new or selected ground-truth paths.")
        self.btn_ground_truth_color.setStyleSheet("QPushButton { background: #FB7185; color: #0F172A; font-weight: bold; }")
        form.addRow("Name:", self.txt_ground_truth_name)
        form.addRow("Color:", self.btn_ground_truth_color)
        # form.addRow("Saved paths:", self.cmb_ground_truth)
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
