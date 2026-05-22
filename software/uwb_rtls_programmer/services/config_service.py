from PySide6.QtCore import QSettings

class ConfigService:
    def __init__(self):
        self.settings = QSettings("uwb-rtls", "uwb_rtls_programmer")

    def get_last_vid_pid(self):
        vid = self.settings.value("last_vid", "0483")
        pid = self.settings.value("last_pid", "DF11")
        return str(vid), str(pid)

    def set_last_vid_pid(self, vid: str, pid: str):
        self.settings.setValue("last_vid", vid)
        self.settings.setValue("last_pid", pid)
        self.settings.sync()

    def get_last_hex_dir(self):
        return str(self.settings.value("last_hex_dir", ""))

    def set_last_hex_dir(self, directory: str):
        self.settings.setValue("last_hex_dir", directory)
        self.settings.sync()
        
    def get_recent_hex_paths(self):
        recent = self.settings.value("recent_hex_paths", [])
        if isinstance(recent, str):
            return [recent] if recent else []
        return [str(p) for p in recent if p]

    def set_recent_hex_paths(self, paths):
        self.settings.setValue("recent_hex_paths", paths)
        self.settings.sync()
