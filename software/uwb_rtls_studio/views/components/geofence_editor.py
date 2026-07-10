"""
==============================================================================
  UWB RTLS Studio - Geofence Editor Widget
==============================================================================
"""
import os
from PyQt6 import uic
from PyQt6.QtWidgets import QWidget

UI_FILE = os.path.join(os.path.dirname(__file__), "..", "ui", "geofence_editor.ui")


class GeofenceEditorWidget(QWidget):
    """Standalone widget for the Geofencing Editor panel.
    Loaded from geofence_editor.ui via uic.loadUi.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(UI_FILE, self)
