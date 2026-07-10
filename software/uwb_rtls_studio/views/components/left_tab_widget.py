from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QStyle, QStyleOptionTab, QTabBar, QTabWidget, QWidget


class HorizontalLeftTabBar(QTabBar):
    """Left navigation bar whose tab labels remain horizontal."""

    def tabSizeHint(self, index):
        return QSize(220, 50)

    def paintEvent(self, event):
        painter = QPainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            self.style().drawControl(
                QStyle.ControlElement.CE_TabBarTabShape,
                option,
                painter,
                self,
            )

            label_option = QStyleOptionTab(option)
            label_option.shape = QTabBar.Shape.RoundedNorth
            self.style().drawControl(
                QStyle.ControlElement.CE_TabBarTabLabel,
                label_option,
                painter,
                self,
            )


class LeftTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sidebar_background = QWidget(self)
        self._sidebar_background.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._sidebar_background.setStyleSheet("background-color: #231E3D;")
        self.setTabBar(HorizontalLeftTabBar(self))
        self.setTabPosition(QTabWidget.TabPosition.West)
        self.tabBar().setExpanding(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        sidebar_width = max(self.tabBar().width(), self.tabBar().tabSizeHint(0).width())
        self._sidebar_background.setGeometry(0, 0, sidebar_width, self.height())
        self._sidebar_background.raise_()
        self.tabBar().raise_()
