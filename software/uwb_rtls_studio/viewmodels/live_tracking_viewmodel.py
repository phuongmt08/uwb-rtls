"""
===============================================================================
  UWB RTLS Studio — Live Tracking ViewModel
===============================================================================
  File        : viewmodels/live_tracking_viewmodel.py
  Description : ViewModel cho tab "Live Tracking" (Tab 2).
                Quản lý realtime position data, vẽ trajectory,
                và hiển thị anchor-tag distances.

  MVVM Role   : VIEWMODEL

  Tab này hiển thị (User-Facing):
    ┌─────────────────────────────────────────────────────────┐
    │  LIVE TRACKING TAB                                      │
    ├─────────────────────────────────────────────────────────┤
    │  ┌─ Position Map (2D Canvas) ─────────────────────────┐ │
    │  │                                                     │ │
    │  │    [A1]────────────────────────[A2]                  │ │
    │  │      │                          │                   │ │
    │  │      │       ● (Tag pos)        │                   │ │
    │  │      │      ╱  ╲                │                   │ │
    │  │      │     ╱    ╲               │                   │ │
    │  │    [A3]────────────────────────[A4]                  │ │
    │  │                                                     │ │
    │  │  ← Pan/Zoom enabled, trajectory trail shown →       │ │
    │  └─────────────────────────────────────────────────────┘ │
    │  ┌─ Position Data ────────────────────────────────────┐ │
    │  │  X: x.xx (m)  |  Y: x.xx (m)  |  Z: x.xx (m)         │ │
    │  │  RMS Error: x.xx (m)  |  Update Rate: x.xx Hz        │ │
    │  └────────────────────────────────────────────────────┘ │
    │  ┌─ Anchor Distances ─────────────────────────────────┐ │
    │  │  Anchor 1: x.xx (m) (FP: 500) | Anchor 2: x.xx (m)     │ │
    │  │  Anchor 3: x.xx (m) (FP: 480) | Anchor 4: x.xx (m)     │ │
    │  └────────────────────────────────────────────────────┘ │
    │  [▶ Start Ranging]  [■ Stop Ranging]                    │
    └─────────────────────────────────────────────────────────┘

  Signals:
    - position_updated(x, y, z, rms)
    - anchor_distances_updated(anchors: list)
    - ranging_started()
    - ranging_stopped()
    - stats_updated(stats: dict)

  Protocol Messages:
    - ranging_start_t (16), ranging_stop_t (17)
    - ranging_result_t (18)
    - ranging_status_get_t / _resp_t (19, 20)
    - anchor_layout_get_t / _resp_t (43, 45)
===============================================================================
"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal
from models.ranging_model import RangingModel
from services.protocol_service import ProtocolService

log = logging.getLogger(__name__)

class LiveTrackingViewModel(QObject):
    ranging_started = pyqtSignal()
    ranging_stopped = pyqtSignal()
    position_updated = pyqtSignal(float, float, float, float)
    anchor_distances_updated = pyqtSignal(list)
    stats_updated = pyqtSignal(dict)

    def __init__(self, model: RangingModel, protocol_service: ProtocolService, parent=None):
        super().__init__(parent)
        self.model = model
        self.protocol = protocol_service
        
        self.model.position_updated.connect(self.position_updated.emit)
        self.model.anchor_distances_updated.connect(self.anchor_distances_updated.emit)
        self.model.stats_updated.connect(self.stats_updated.emit)

    def start_ranging(self) -> None:
        # Gọi command tới BE từ ViewModel
        self.protocol.send_command("ranging_start")
        self.model.is_ranging = True
        self.ranging_started.emit()

    def stop_ranging(self) -> None:
        self.protocol.send_command("ranging_stop")
        self.model.is_ranging = False
        self.ranging_stopped.emit()
