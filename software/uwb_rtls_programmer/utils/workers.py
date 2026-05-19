import threading
from PySide6.QtCore import QObject, Signal

class WorkerSignals(QObject):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str)
    connected = Signal(str)

def run_task(signals, fn):
    import traceback
    def target():
        try:
            fn()
            signals.done.emit(True, "OK")
        except Exception as exc:
            err_msg = f"{exc}\n{traceback.format_exc()}"
            signals.log.emit(f"ERROR: {err_msg}")
            signals.done.emit(False, str(exc))
    threading.Thread(target=target, daemon=True).start()
