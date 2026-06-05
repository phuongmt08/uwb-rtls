import sys
import signal
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from module.module_live_plot.live_plot_core import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    signal.signal(signal.SIGINT, lambda *_: (window.close(), app.quit()))
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(100)
    window.show()
    sys.exit(app.exec_())
