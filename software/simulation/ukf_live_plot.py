import sys
from PyQt5.QtWidgets import QApplication
from module.module_live_plot.live_plot_core import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
