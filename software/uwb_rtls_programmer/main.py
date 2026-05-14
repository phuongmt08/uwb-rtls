import sys

def main():
    # Lazy imports to speed up startup
    from PySide6.QtWidgets import QApplication
    from views.main_window import MainWindow
    from controllers.main_controller import MainController

    app = QApplication(sys.argv)
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

