from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

# Allow running as a plain script from any working directory.
THIS_FILE = Path(__file__).resolve()
sys.path.append(str(THIS_FILE.parent.parent))
sys.path.append(str(THIS_FILE.parent.parent.parent))

from common.commands import CommandFactory
from common import protocol_pb2 as pb
from vv_test_session import VvTestSession


BCAST_MAX_PACKET_SIZE = 250
DEFAULT_SRC = pb.PACKET_ADDR_DEBUG
DEFAULT_CENTRAL_DST = pb.PACKET_ADDR_CENTRAL

DEVICE_TYPE_OPTIONS = {
    "Any": None,
    "TAG": pb.DEVICE_TYPE_TAG,
    "ANCHOR": pb.DEVICE_TYPE_ANCHOR,
    "GATEWAY": pb.DEVICE_TYPE_GATEWAY,
    "DEBUG_TOOL": pb.DEVICE_TYPE_DEBUG_TOOL,
}

BCAST_KIND_OPTIONS = ("device_information_get", "time_sync_get", "none")


@dataclass
class GuiSettings:
    port: str | None
    baud: int
    timeout_s: float
    scan_interval_ms: int
    scan_window_ms: int
    device_type: int | None
    device_id: int | None
    min_battery: int | None
    bcast_kind: str
    bcast_repeat: int
    bcast_interval_s: float
    verbose: bool


def enum_name(enum_desc, value: int) -> str:
    item = enum_desc.values_by_number.get(value)
    return item.name if item is not None else f"UNKNOWN({value})"


def device_type_name(value: int) -> str:
    return enum_name(pb.device_type_t.DESCRIPTOR, value)


def addr_name(value: int) -> str:
    return enum_name(pb.device_addr_t.DESCRIPTOR, value)


def packet_summary(pkt: pb.packet_t) -> str:
    ptype = pkt.WhichOneof("params") or "<none>"
    if pkt.HasField("hdr") and pkt.hdr.HasField("addr"):
        src = addr_name(pkt.hdr.addr.src)
        dst = addr_name(pkt.hdr.addr.dst)
        return f"{ptype} src={src} dst={dst} seq={pkt.hdr.seq}"
    return ptype


def auto_port() -> tuple[str | None, int]:
    try:
        probe = VvTestSession.auto_probe(debug=False)
        if probe is not None:
            return probe.port, probe.baud
    except Exception:
        pass
    return None, 115200


class CentralTestGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BLE Central System Test")
        self.geometry("920x620")
        self.minsize(820, 540)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.port_var = tk.StringVar(value="auto")
        self.baud_var = tk.StringVar(value="115200")
        self.timeout_var = tk.StringVar(value="20")
        self.scan_interval_var = tk.StringVar(value="100")
        self.scan_window_var = tk.StringVar(value="80")
        self.device_type_var = tk.StringVar(value="Any")
        self.device_id_var = tk.StringVar(value="")
        self.min_battery_var = tk.StringVar(value="")
        self.bcast_kind_var = tk.StringVar(value=BCAST_KIND_OPTIONS[0])
        self.bcast_repeat_var = tk.StringVar(value="1")
        self.bcast_interval_var = tk.StringVar(value="0.25")
        self.verbose_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Idle")

        self._build_ui()
        self.after(80, self._drain_log_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(root)
        controls.pack(side=tk.TOP, fill=tk.X)

        central = ttk.LabelFrame(controls, text="Central")
        central.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._entry(central, "Port", self.port_var, 0, 0, 14)
        self._entry(central, "Baud", self.baud_var, 0, 2, 10)
        self._entry(central, "Timeout s", self.timeout_var, 0, 4, 8)
        self._entry(central, "Scan int ms", self.scan_interval_var, 1, 0, 8)
        self._entry(central, "Scan win ms", self.scan_window_var, 1, 2, 8)
        ttk.Checkbutton(central, text="Verbose RX", variable=self.verbose_var).grid(
            row=1, column=4, sticky="w", padx=6
        )

        adv = ttk.LabelFrame(controls, text="ADV status filter")
        adv.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(adv, text="Device").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Combobox(
            adv,
            textvariable=self.device_type_var,
            values=tuple(DEVICE_TYPE_OPTIONS.keys()),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=5)
        self._entry(adv, "ID", self.device_id_var, 0, 2, 7)
        self._entry(adv, "Min bat", self.min_battery_var, 1, 0, 7)

        bcast = ttk.LabelFrame(controls, text="BCAST TX")
        bcast.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(bcast, text="Kind").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Combobox(
            bcast,
            textvariable=self.bcast_kind_var,
            values=BCAST_KIND_OPTIONS,
            state="readonly",
            width=22,
        ).grid(row=0, column=1, columnspan=3, sticky="w", padx=6, pady=5)
        self._entry(bcast, "Repeat", self.bcast_repeat_var, 1, 0, 7)
        self._entry(bcast, "Interval", self.bcast_interval_var, 1, 2, 7)

        actions = ttk.Frame(root)
        actions.pack(side=tk.TOP, fill=tk.X, pady=(10, 8))
        ttk.Button(actions, text="Test ADV Status", command=self.start_adv_status).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Listen BCAST RX", command=self.start_bcast_rx).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Send BCAST", command=self.start_send_bcast).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Stop", command=self.stop_current).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Clear Log", command=self.clear_log).pack(side=tk.RIGHT)

        ttk.Label(root, textvariable=self.status_var).pack(side=tk.TOP, anchor="w")

        log_frame = ttk.Frame(root)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=22, undo=False)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _entry(
        self,
        parent: ttk.Frame,
        label: str,
        var: tk.StringVar,
        row: int,
        col: int,
        width: int,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=6, pady=5)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=col + 1, sticky="w", padx=6, pady=5)

    def log(self, text: str) -> None:
        self.log_queue.put(text)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
        self.after(80, self._drain_log_queue)

    def clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def settings(self) -> GuiSettings:
        return GuiSettings(
            port=self._optional_text(self.port_var.get(), auto=True),
            baud=int(self.baud_var.get(), 0),
            timeout_s=float(self.timeout_var.get()),
            scan_interval_ms=int(self.scan_interval_var.get(), 0),
            scan_window_ms=int(self.scan_window_var.get(), 0),
            device_type=DEVICE_TYPE_OPTIONS[self.device_type_var.get()],
            device_id=self._optional_int(self.device_id_var.get()),
            min_battery=self._optional_int(self.min_battery_var.get()),
            bcast_kind=self.bcast_kind_var.get(),
            bcast_repeat=max(1, int(self.bcast_repeat_var.get(), 0)),
            bcast_interval_s=float(self.bcast_interval_var.get()),
            verbose=self.verbose_var.get(),
        )

    @staticmethod
    def _optional_text(value: str, auto: bool = False) -> str | None:
        value = value.strip()
        if value == "" or (auto and value.lower() == "auto"):
            return None
        return value

    @staticmethod
    def _optional_int(value: str) -> int | None:
        value = value.strip()
        if value == "":
            return None
        return int(value, 0)

    def run_worker(self, title: str, fn) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.log("ERROR: another test is still running")
            return

        self.stop_event.clear()
        self.status_var.set(f"Running: {title}")
        self.log("")
        self.log(f"=== {title} ===")

        def wrapper() -> None:
            try:
                fn()
            except Exception as exc:
                self.log(f"ERROR: {exc}")
            finally:
                self.status_var.set("Idle")

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def stop_current(self) -> None:
        self.stop_event.set()
        self.log("Stop requested")

    def open_session(self, cfg: GuiSettings) -> VvTestSession:
        port = cfg.port
        baud = cfg.baud
        if port is None:
            port, baud = auto_port()
        if port is None:
            raise RuntimeError("no central serial port found")

        self.log(f"Opening central serial {port} @ {baud}")
        return VvTestSession(port, baud, debug=False)

    def send_scan_start(self, session: VvTestSession, cfg: GuiSettings) -> None:
        factory = CommandFactory()
        pkt = factory.ble_scan_start(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq())
        pkt.ble_scan_start.duration_ms = 0
        pkt.ble_scan_start.interval_ms = cfg.scan_interval_ms
        pkt.ble_scan_start.window_ms = cfg.scan_window_ms
        pkt.ble_scan_start.active_scanning = True
        session.send_packet(pkt)
        self.log("Central scan_start sent")

    def start_adv_status(self) -> None:
        cfg = self.settings()
        self.run_worker("ADV status system test", lambda: self.worker_adv_status(cfg))

    def worker_adv_status(self, cfg: GuiSettings) -> None:
        observed = 0
        matched = False
        with self.open_session(cfg) as session:
            self.send_scan_start(session, cfg)
            deadline = time.time() + cfg.timeout_s
            while time.time() < deadline and not self.stop_event.is_set():
                for pkt in session.recv_packets(timeout_s=0.2):
                    ptype = pkt.WhichOneof("params") or "<none>"
                    if ptype == "ble_adv_status":
                        observed += 1
                        s = pkt.ble_adv_status
                        self.log(
                            "ADV_STATUS "
                            f"device={device_type_name(s.device)} id={s.device_id} "
                            f"bat={s.bat_soc_percent}% flags=0x{s.status_flags:08X} "
                            f"warn={s.warning_count} err={s.error_count} ts={s.local_timestamp_s}"
                        )
                        if self.adv_status_matches(s, cfg):
                            matched = True
                            self.log("PASS: expected peripheral ADV status observed")
                            return
                    elif cfg.verbose:
                        self.log(f"RX {packet_summary(pkt)}")

        if observed == 0:
            self.log("FAIL: central did not report any peripheral ADV status")
        elif not matched:
            self.log("FAIL: ADV status observed, but filter did not match")

    def adv_status_matches(self, status: pb.ble_adv_status_t, cfg: GuiSettings) -> bool:
        if cfg.device_type is not None and status.device != cfg.device_type:
            return False
        if cfg.device_id is not None and status.device_id != cfg.device_id:
            return False
        if cfg.min_battery is not None and status.bat_soc_percent < cfg.min_battery:
            return False
        return True

    def start_bcast_rx(self) -> None:
        cfg = self.settings()
        self.run_worker("BCAST RX system test", lambda: self.worker_bcast_rx(cfg))

    def worker_bcast_rx(self, cfg: GuiSettings) -> None:
        with self.open_session(cfg) as session:
            self.send_scan_start(session, cfg)
            deadline = time.time() + cfg.timeout_s
            while time.time() < deadline and not self.stop_event.is_set():
                for pkt in session.recv_packets(timeout_s=0.2):
                    self.log(f"RX {packet_summary(pkt)}")
                    if pkt.HasField("hdr") and pkt.hdr.HasField("addr") and pkt.hdr.addr.dst == pb.PACKET_ADDR_BCAST:
                        self.log("PASS: central received and forwarded a BCAST packet")
                        return

        self.log("FAIL: no BCAST packet observed through central")

    def start_send_bcast(self) -> None:
        cfg = self.settings()
        self.run_worker("BCAST TX", lambda: self.worker_send_bcast(cfg))

    def worker_send_bcast(self, cfg: GuiSettings) -> None:
        with self.open_session(cfg) as session:
            for i in range(cfg.bcast_repeat):
                if self.stop_event.is_set():
                    return
                pkt = pb.packet_t()
                pkt.hdr.addr.src = pb.PACKET_ADDR_DEBUG
                pkt.hdr.addr.dst = pb.PACKET_ADDR_BCAST
                pkt.hdr.seq = session.proto.next_seq()
                if cfg.bcast_kind == "device_information_get":
                    pkt.device_information_get.dummy = 0
                elif cfg.bcast_kind == "time_sync_get":
                    pkt.time_sync_get.dummy = 0
                else:
                    pkt.none.dummy = i

                raw = pkt.SerializeToString()
                if len(raw) > BCAST_MAX_PACKET_SIZE:
                    self.log(f"ERROR: encoded protobuf too large: {len(raw)} > {BCAST_MAX_PACKET_SIZE}")
                    return

                self.log(f"TX BCAST {packet_summary(pkt)} encoded_len={len(raw)}")
                session.send_packet(pkt)
                time.sleep(cfg.bcast_interval_s)
        self.log("PASS: BCAST TX command sent")


def main() -> int:
    app = CentralTestGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
