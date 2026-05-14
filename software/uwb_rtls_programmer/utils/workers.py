import threading
from PySide6.QtCore import QObject, Signal

class WorkerSignals(QObject):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str)
    connected = Signal(str)

def run_task(signals, fn):
    def target():
        try:
            fn()
            signals.done.emit(True, "OK")
        except Exception as exc:
            signals.done.emit(False, str(exc))
    threading.Thread(target=target, daemon=True).start()
