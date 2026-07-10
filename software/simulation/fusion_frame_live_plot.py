import signal
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from module.module_live_plot.fusion_frame_plot_core import FusionFrameWindow


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FusionFrameWindow()
    signal.signal(signal.SIGINT, lambda *_: (window.close(), app.quit()))
    sigint_timer = QTimer()
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start(100)
    window.show()
    sys.exit(app.exec_())
