import sys

def main():
    # Lazy imports to speed up startup
    from PySide6.QtWidgets import QApplication
    from views.main_window import MainWindow
    from controllers.main_controller import MainController

    app = QApplication(sys.argv)
    
    # Force Dark Mode using Fusion style and custom CSS
    app.setStyle("Fusion")
    dark_stylesheet = """
    /* --- Deep Slate Theme --- */
    QMainWindow, QWidget {
        background-color: #0F172A;
        color: #F8FAFC;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 10pt;
    }

    /* --- Tabs --- */
    QTabWidget::pane {
        border: 1px solid #334155;
        background-color: #0F172A;
        border-radius: 6px;
    }
    QTabBar::tab {
        background-color: #1E293B;
        color: #94A3B8;
        padding: 10px 24px;
        border: 1px solid transparent;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 4px;
    }
    QTabBar::tab:hover {
        background-color: #334155;
        color: #F8FAFC;
    }
    QTabBar::tab:selected {
        background-color: #0F172A;
        color: #38BDF8;
        border: 1px solid #334155;
        border-bottom-color: #0F172A;
        border-top: 3px solid #38BDF8;
        font-weight: 600;
    }

    /* --- Group Boxes --- */
    QGroupBox {
        border: 1px solid #334155;
        margin-top: 10px;
        padding-top: 16px;
        border-radius: 8px;
        background-color: #1E293B;
    }
    QGroupBox::title {
        subcontrol-origin: border;
        subcontrol-position: top left;
        left: 20px;
        padding: 4px 12px;
        color: #0F172A;
        font-weight: bold;
        background-color: #38BDF8;
        border: 1px solid #0284C7;
        border-radius: 6px;
    }

    /* --- Buttons --- */
    QPushButton {
        background-color: #2563EB;
        border: 1px solid #1D4ED8;
        padding: 8px 16px;
        color: #FFFFFF;
        border-radius: 6px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #3B82F6;
        border: 1px solid #2563EB;
        color: #FFFFFF;
    }
    QPushButton:pressed {
        background-color: #1E40AF;
        border: 1px solid #1E3A8A;
    }
    QPushButton:disabled {
        background-color: #334155;
        color: #94A3B8;
        border: 1px solid #475569;
    }

    /* --- Inputs --- */
    QComboBox, QSpinBox, QLineEdit {
        background-color: #0B1120;
        border: 1px solid #334155;
        color: #F8FAFC;
        padding: 6px 12px;
        border-radius: 6px;
    }
    QComboBox:hover, QSpinBox:hover, QLineEdit:hover {
        border: 1px solid #38BDF8;
    }
    QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
        border: 1px solid #38BDF8;
        background-color: #0F172A;
    }
    QComboBox QAbstractItemView {
        background-color: #0B1120;
        border: 1px solid #334155;
        selection-background-color: #38BDF8;
        selection-color: #0F172A;
        border-radius: 6px;
    }

    /* --- Text Edit (Terminal/Logs) --- */
    QTextEdit {
        background-color: #020617;
        color: #10B981;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 10px;
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: 10pt;
        selection-background-color: #38BDF8;
        selection-color: #020617;
    }

    /* --- Tables --- */
    QTableWidget {
        background-color: #0F172A;
        alternate-background-color: #1E293B;
        gridline-color: #334155;
        border: 1px solid #334155;
        border-radius: 8px;
        color: #F8FAFC;
        selection-background-color: #38BDF8;
        selection-color: #0F172A;
    }
    QTableWidget::item {
        padding: 6px 10px;
        border-bottom: 1px solid #1E293B;
    }
    QHeaderView::section {
        background-color: #1E293B;
        color: #94A3B8;
        padding: 8px 10px;
        border: none;
        border-right: 1px solid #334155;
        border-bottom: 2px solid #38BDF8;
        font-weight: bold;
    }

    /* --- Scrollbars --- */
    QScrollBar:vertical {
        border: none;
        background: transparent;
        width: 14px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background: #475569;
        min-height: 24px;
        border-radius: 7px;
        margin: 2px;
    }
    QScrollBar::handle:vertical:hover {
        background: #64748B;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    
    QScrollBar:horizontal {
        border: none;
        background: transparent;
        height: 14px;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background: #475569;
        min-width: 24px;
        border-radius: 7px;
        margin: 2px;
    }
    QScrollBar::handle:horizontal:hover {
        background: #64748B;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
    }

    /* --- Progress Bar --- */
    QProgressBar {
        border: 1px solid #334155;
        border-radius: 8px;
        text-align: center;
        background-color: #0F172A;
        color: #F8FAFC;
        font-weight: bold;
    }
    QProgressBar::chunk {
        background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #3B82F6, stop: 1 #38BDF8);
        border-radius: 7px;
    }
    """
    app.setStyleSheet(dark_stylesheet)

    view = MainWindow()
    controller = MainController(view)
    
    # Override close event
    old_close = view.closeEvent
    def closeEvent(event):
        controller.shutdown()
        old_close(event)
    view.closeEvent = closeEvent
    
    view.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

