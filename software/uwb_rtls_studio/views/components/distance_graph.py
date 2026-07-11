import math
import time
from collections import defaultdict

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class DistanceGraph(QWidget):
    COLORS = [
        "#22D3EE", "#F97316", "#22C55E", "#A78BFA",
        "#F43F5E", "#EAB308", "#60A5FA", "#F472B6",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._started_at = None
        self._times = []
        self._distances = defaultdict(list)
        self._curves = {}
        self._ground_truth_lines = []
        self._ground_truth_values = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        controls = QHBoxLayout()

        controls.addWidget(QLabel("Window:"))
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItems(["Sliding", "Static"])
        self.mode_combo.currentTextChanged.connect(self._refresh_curves)
        controls.addWidget(self.mode_combo)

        controls.addWidget(QLabel("Ground truth count:"))
        self.ground_truth_count = QSpinBox(self)
        self.ground_truth_count.setRange(0, 8)
        self.ground_truth_count.setValue(0)
        self.ground_truth_count.valueChanged.connect(self._on_ground_truth_count_changed)
        controls.addWidget(self.ground_truth_count)

        controls.addWidget(QLabel("Ground truth:"))
        self.ground_truth_combo = QComboBox(self)
        self.ground_truth_combo.setEnabled(False)
        self.ground_truth_combo.currentIndexChanged.connect(self._on_ground_truth_selected)
        controls.addWidget(self.ground_truth_combo)

        controls.addWidget(QLabel("Value:"))
        self.ground_truth_value = QDoubleSpinBox(self)
        self.ground_truth_value.setRange(0.0, 1000.0)
        self.ground_truth_value.setDecimals(3)
        self.ground_truth_value.setSuffix(" m")
        self.ground_truth_value.setEnabled(False)
        self.ground_truth_value.valueChanged.connect(self._on_ground_truth_value_changed)
        controls.addWidget(self.ground_truth_value)

        controls.addStretch(1)

        clear_button = QPushButton("Clear", self)
        clear_button.clicked.connect(self.clear)
        controls.addWidget(clear_button)
        root.addLayout(controls)

        self.plot = pg.PlotWidget(self)
        self.plot.setBackground("#0F172A")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Elapsed time", units="s")
        self.plot.setLabel("left", "Distance", units="m")
        self.plot.addLegend(offset=(10, 10))
        root.addWidget(self.plot, 1)

    def start_session(self):
        self.clear()
        self._started_at = time.monotonic()

    def stop_session(self):
        self._started_at = None

    def clear(self):
        self._times.clear()
        self._distances.clear()
        for curve in self._curves.values():
            curve.setData([], [])
        self.plot.setXRange(0.0, 1.0, padding=0.0)

    def append_sample(self, sample):
        if self._started_at is None:
            return
        elapsed_s = time.monotonic() - self._started_at
        distances = list(sample.get("distance", []) or [])
        self._times.append(elapsed_s)

        series_count = max(len(distances[:8]), len(self._curves))
        for index in range(series_count):
            anchor_id = index + 1
            value = float(distances[index]) if index < len(distances) else math.nan
            if anchor_id not in self._curves:
                self._curves[anchor_id] = self.plot.plot(
                    name=f"A{anchor_id}",
                    pen=pg.mkPen(self.COLORS[index], width=2),
                )
            self._distances[anchor_id].append(value if math.isfinite(value) and value > 0.0 else math.nan)
        self._refresh_curves()

    def _refresh_curves(self):
        if not self._times:
            return
        start_index = max(0, len(self._times) - 100) if self.mode_combo.currentText() == "Sliding" else 0
        visible_times = self._times[start_index:]
        for anchor_id, curve in self._curves.items():
            curve.setData(visible_times, self._distances[anchor_id][start_index:])
        self.plot.setXRange(visible_times[0], max(visible_times[0] + 1.0, visible_times[-1]), padding=0.01)

    def _update_ground_truth(self):
        for line in self._ground_truth_lines:
            self.plot.removeItem(line)
        self._ground_truth_lines.clear()

        for index, value in enumerate(self._ground_truth_values):
            line = pg.InfiniteLine(
                pos=value,
                angle=0,
                pen=pg.mkPen(self.COLORS[index], width=1, style=Qt.PenStyle.DashLine),
                label=f"GT A{index + 1}: {value:.3f} m",
                labelOpts={"color": self.COLORS[index], "position": 0.92},
            )
            self.plot.addItem(line)
            self._ground_truth_lines.append(line)

    def _on_ground_truth_count_changed(self, count):
        count = int(count)
        if count > len(self._ground_truth_values):
            self._ground_truth_values.extend([0.0] * (count - len(self._ground_truth_values)))
        else:
            self._ground_truth_values = self._ground_truth_values[:count]

        self.ground_truth_combo.blockSignals(True)
        self.ground_truth_combo.clear()
        self.ground_truth_combo.addItems([f"GT {index}" for index in range(1, count + 1)])
        self.ground_truth_combo.blockSignals(False)
        enabled = count > 0
        self.ground_truth_combo.setEnabled(enabled)
        self.ground_truth_value.setEnabled(enabled)
        if enabled:
            self.ground_truth_combo.setCurrentIndex(0)
            self._on_ground_truth_selected(0)
        self._update_ground_truth()

    def _on_ground_truth_selected(self, index):
        if not 0 <= index < len(self._ground_truth_values):
            return
        self.ground_truth_value.blockSignals(True)
        self.ground_truth_value.setValue(self._ground_truth_values[index])
        self.ground_truth_value.blockSignals(False)

    def _on_ground_truth_value_changed(self, value):
        index = self.ground_truth_combo.currentIndex()
        if not 0 <= index < len(self._ground_truth_values):
            return
        self._ground_truth_values[index] = float(value)
        self._update_ground_truth()
