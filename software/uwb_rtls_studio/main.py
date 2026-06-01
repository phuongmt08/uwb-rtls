"""
===============================================================================
  UWB RTLS Studio — Application Entry Point (Frontend Demo)
===============================================================================
  File        : main.py
  Author      : Trung Quan
  Description : Entry point — khởi chạy toàn bộ UI Frontend.
                Flow: DonglePopup → ScanPopup → MainWindow.
                Chỉ có Frontend, chưa có Backend logic.
===============================================================================
"""
import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from utils.theme import DARK_STYLESHEET
from views.popups.dongle_popup import DonglePopup
from views.popups.scan_popup import ScanPopup
from views.windows.main_window import MainWindow


def main():
    # High DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)

    # Apply global font
    font = QFont("Segoe UI", 13)
    app.setFont(font)

    # Apply dark theme
    app.setStyleSheet(DARK_STYLESHEET)

    # ═══ FLOW 1: Dongle Detection ═══
    dongle_popup = DonglePopup()
    result = dongle_popup.exec()
    if result != DonglePopup.DialogCode.Accepted:
        print("Dongle detection cancelled. Exiting.")
        sys.exit(0)

    # ═══ FLOW 2: BLE Scan & Connect ═══
    scan_popup = ScanPopup()
    result = scan_popup.exec()
    if result != ScanPopup.DialogCode.Accepted:
        print("BLE scan cancelled. Exiting.")
        sys.exit(0)

    # ═══ FLOW 3: Main Window ═══
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
