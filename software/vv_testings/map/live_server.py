#!/usr/bin/env python3
"""Phat vi tri UKF cua Tag len trang ban do, xem qua browser.

Orin khong co GUI nen server nay chay tren Orin, may khac mo
    http://<ip-orin>:8080
Trang web nhan vi tri qua SSE (/stream) va ve len map sinh tu graph.hml.

Doc Tag bang chinh logic cua test_position.py (import lai, khong sua file do).

Vi du
-----
    python3 live_server.py                          # tu dong tim cong Tag
    python3 live_server.py --port /dev/ttyACM0
    python3 live_server.py --replay run1.csv --loop  # phat lai CSV, khong can phan cung
    python3 live_server.py --http 8080 --csv live.csv  # -> live_<ngay>_<gio>.csv
    python3 live_server.py --record                  # ghi runs/run_<ngay>_<gio>.csv
    python3 live_server.py --record duong_thang.csv  # tu dat ten file
    python3 live_server.py --lpf-hz 1.0              # loc tril truoc khi ve len trang
"""
from __future__ import annotations

import argparse
import csv
import errno
import json
import queue
import signal
import socket
import statistics
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_STAMP = time.strftime("%Y%m%d_%H%M%S")      # tem cho lan chay nay
TP_DIR = HERE.parent / "vehicle_testings"
SOFTWARE_DIR = HERE.parent.parent               # .../software, cho goi common/
sys.path.insert(0, str(SOFTWARE_DIR))

from common.filters import PositionSmoother     # noqa: E402  (sau khi co sys.path)

state = {"status": "khoi dong", "last": None, "count": 0, "started": time.time(),
         "lpf_hz": 0.0, "lpf_median": 0}
# Moi phan tu: [ukf_x, ukf_y, tril_x, tril_y, lpf_x, lpf_y] - ba duong di cung nhau.
# Khong bat --lpf-hz thi hai so cuoi luon la 0 va trang web khong ve duong do.
trail: deque = deque(maxlen=4000)
lpf = None                               # PositionSmoother, chi co khi --lpf-hz > 0
# Nhip ra nghiem tril moi. Goi ve ~26 Hz nhung nghiem moi thi cham hon nhieu,
# ma do tre cua chuoi loc lai tinh theo SO MAU nen no phu thuoc hoan toan vao
# nhip nay. Do va gui kem moi mau de trang web noi ra duoc dang tre bao nhieu.
fix_times: deque = deque(maxlen=21)      # gio MCU cua cac nghiem tril gan day
last_tril = None
pkt_times: deque = deque(maxlen=41)      # gio MCU cua cac goi gan day

# 5 gia tri ghi lai moi mau, dung thu tu cot trong file CSV.
RUN_COLUMNS = ["ukf_x_m", "ukf_y_m", "tril_x_m", "tril_y_m", "ukf_yaw_deg"]
# Cot cho --csv: nhat ky pose. Co ca tril tho lan tril da loc de con do lai
# nhieu va chon tan so cat tu chinh du lieu chay that. Khong bat --lpf-hz thi
# hai cot lpf de trong (khac 0, de khoi lan voi nghiem that bang 0).
LOG_COLUMNS = ["host_time", "timestamp_ms", "ukf_x_m", "ukf_y_m", "ukf_yaw_deg",
               "tril_x_m", "tril_y_m", "tril_x_lpf_m", "tril_y_lpf_m",
               "zone_id", "n_anchors"]
session = None                           # K2Session, chi co khi dung --k2-plan
# K2Session la may trang thai mot luong. O day co hai luong dung no: luong doc
# Tag goi feed(), luong HTTP goi start()/stop()/goto()/state(). Khong khoa thi
# stop() dong file ngay giua luc feed() dang ghi -> luong doc Tag chet, trang
# web treo o mau cuoi ma van bao "da ket noi". Moi cho cham vao session deu
# phai di qua khoa nay.
session_lock = threading.Lock()
sinks: list["CsvSink"] = []              # cac file CSV dang duoc ghi
clients: list[queue.Queue] = []
clients_lock = threading.Lock()
stop_flag = threading.Event()


def publish(kind: str, payload: dict) -> None:
    msg = json.dumps(payload, separators=(",", ":"))
    with clients_lock:
        for q in clients:
            try:
                q.put_nowait((kind, msg))
            except queue.Full:
                # Client cham (mang yeu, tab bi treo): bo mau cu nhat roi nhet
                # mau moi vao. Truoc day day hang doi la mat luon ket noi.
                try:
                    q.get_nowait()
                    q.put_nowait((kind, msg))
                except (queue.Empty, queue.Full):
                    pass


def set_status(text: str) -> None:
    state["status"] = text
    print(f"[status] {text}", flush=True)
    publish("status", {"status": text})


def pkt_hz() -> float:
    """Nhip lay mau that [Hz], do theo dong ho MCU chu khong theo gio Orin.

    Gio Orin con dinh do tre cua USB va cua vong doc, con timestamp_ms do
    chinh MCU dong dau luc dung goi, nen day moi la nhip lay mau that.
    """
    if len(pkt_times) < 3:
        return 0.0
    ts = list(pkt_times)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if 0.0 < b - a < 5.0]
    return round(1.0 / statistics.median(gaps), 1) if gaps else 0.0


def fix_hz(s: dict) -> float:
    """Nhip ra nghiem tril moi [Hz], lay trung vi vai chuc nghiem gan nhat."""
    global last_tril
    tril = (s.get("tril_x_m"), s.get("tril_y_m"))
    if tril != last_tril and (tril[0] or tril[1]):
        last_tril = tril
        fix_times.append((s.get("timestamp_ms") or 0.0) / 1000.0)
    if len(fix_times) < 3:
        return 0.0
    ts = list(fix_times)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if 0.0 < b - a < 5.0]
    return round(1.0 / statistics.median(gaps), 2) if gaps else 0.0


def apply_lpf(s: dict) -> None:
    """Them tril_x_lpf_m/tril_y_lpf_m vao mau; tat loc thi bo han hai khoa do.

    Loc o server chu khong o trang web: moi tab deu thay cung mot duong, va
    file CSV ghi ra khop voi cai dang nhin thay. Mau khong co nghiem tril
    ((0,0)) thi khong dua vao bo loc, neu khong duong loc bi keo ve goc.
    """
    if lpf is None or not (s.get("tril_x_m") or s.get("tril_y_m")):
        s.pop("tril_x_lpf_m", None)
        s.pop("tril_y_lpf_m", None)
        return
    s["tril_x_lpf_m"], s["tril_y_lpf_m"] = lpf.update_if_new(
        s["tril_x_m"], s["tril_y_m"], (s.get("timestamp_ms") or 0.0) / 1000.0)


def on_sample(s: dict) -> None:
    s["host_time"] = time.time()
    pkt_times.append((s.get("timestamp_ms") or 0.0) / 1000.0)
    s["pkt_hz"] = pkt_hz()
    s["fix_hz"] = fix_hz(s)
    apply_lpf(s)
    state["last"] = s
    state["count"] += 1
    trail.append([s["ukf_x_m"], s["ukf_y_m"],
                  s.get("tril_x_m", 0.0), s.get("tril_y_m", 0.0),
                  s.get("tril_x_lpf_m", 0.0), s.get("tril_y_lpf_m", 0.0)])
    for sink in sinks:
        sink.feed(s)
    publish("pose", s)
    if session is not None:
        with session_lock:
            evs = session.feed(s)
            st = session.state() if evs else None
        for ev in evs:
            print(f"[k2] {ev['muc']}: {ev['text']}", flush=True)
            publish("k2ev", ev)
        if st is not None:
            publish("k2", st)                # co su kien thi bao ngay...


def k2_ticker() -> None:
    """Bat truong hop im hoan toan - luc do on_sample khong con duoc goi."""
    while not stop_flag.wait(1.0):
        if session is None:
            continue
        with session_lock:
            evs = session.tick()
            st = session.state()
        for ev in evs:
            print(f"[k2] {ev['muc']}: {ev['text']}", flush=True)
            publish("k2ev", ev)
        publish("k2", st)                    # ...con lai nhip 2 s nay lo


# ----------------------------------------------------------------- nguon du lieu
def run_source(fn, args) -> None:
    """Chay nguon du lieu, khong de no chet am tham.

    Truoc day mot loi ngoai du tinh trong luong nay chi in traceback ra console
    roi thoi: trang web van giu ket noi SSE, van bao "da ket noi", nhung dung
    im o mau cuoi cung. Bay gio doi trang thai sang bao loi de con nhin thay.
    """
    try:
        fn(args)
    except Exception:
        traceback.print_exc()
        set_status("nguon du lieu da dung vi loi - xem log tren Orin")


def serial_loop(args) -> None:
    sys.path.insert(0, str(TP_DIR))
    import test_position as tp                      # dung lai protocol + decode

    proto = tp.VvProtocol()
    factory = tp.CommandFactory()
    src, dst = int(tp.VvAddress.VEHICLE), int(tp.VvAddress.MCU)

    while not stop_flag.is_set():
        port = args.port or tp.find_port()
        if port is None:
            set_status("khong thay cong Tag (0483:5740), thu lai sau 2 s")
            stop_flag.wait(2.0)
            continue
        try:
            with tp.serial.Serial(port, args.baud, timeout=0.2) as link:
                set_status(f"da mo {port} @ {args.baud}")
                if not args.no_start:
                    link.write(proto.wrap_packet(factory.ranging_start(
                        src, dst, proto.next_seq(),
                        yaw_deg=args.yaw, is_ukf_reinit=args.reinit)))
                    link.flush()
                    set_status(f"da gui ranging_start (yaw={args.yaw}, reinit={args.reinit})")

                while not stop_flag.is_set():
                    # read(4096) cho cho du 4096 byte hoac het timeout 0.2 s.
                    # O 115200 baud, 4096 byte mat ~356 ms nen khong lan nao du
                    # -> lan nao cung cho het 0.2 s. Moi mau bi om lai trung
                    # binh ~100 ms va vi tri nhay theo tung cum 200 ms mot.
                    # Lay dung so byte dang cho san; chua co byte nao thi
                    # read(1) nam doi byte dau tien roi lay tiep phan con lai.
                    # Bo giai ma HDLC la may trang thai chay tung byte, giu
                    # trang thai qua cac lan goi, nen chia goi nho vo tu.
                    n = link.in_waiting
                    chunk = link.read(n if n else 1)
                    if not chunk:
                        continue
                    for packet in proto.decode_from_frames(chunk):
                        if packet.WhichOneof("params") != "sensor_fusion_result":
                            continue
                        on_sample(tp.decode(packet.sensor_fusion_result))

                if not args.no_start and not args.no_stop:
                    link.write(proto.wrap_packet(
                        factory.ranging_stop(src, dst, proto.next_seq())))
                    link.flush()
        except tp.serial.SerialException as exc:
            set_status(f"loi cong: {exc} - thu lai sau 2 s")
            stop_flag.wait(2.0)


def replay_loop(args) -> None:
    """Phat lai file CSV do test_position.py --csv ghi ra."""
    rows = list(csv.DictReader(args.replay.open()))
    if not rows:
        set_status(f"{args.replay} rong")
        return
    num = ("ukf_x_m", "ukf_y_m", "ukf_yaw_deg", "tril_x_m", "tril_y_m", "yaw_deg",
           "cov_xx_m2", "cov_xy_m2", "cov_yy_m2")
    while not stop_flag.is_set():
        set_status(f"phat lai {args.replay.name} ({len(rows)} mau)")
        trail.clear()          # moi vong phat lai la mot lan chay moi, khong noi vet
        prev = None
        for r in rows:
            if stop_flag.is_set():
                return
            s = {k: (float(r[k]) if r.get(k) not in (None, "") else 0.0) for k in num}
            s["timestamp_ms"] = float(r.get("timestamp_ms") or 0)
            s["zone_id"] = int(float(r.get("zone_id") or 0))
            s["n_anchors"] = int(float(r.get("n_anchors") or 0))
            s["cov_valid"] = str(r.get("cov_valid", "")).lower() in ("true", "1")
            s["anchors"] = [
                {"id": int(p.split(":")[0]), "distance_mm": float(p.split(":")[1])}
                for p in (r.get("anchors") or "").split(";") if p.count(":") >= 1
            ]
            t = float(r["host_time"]) if r.get("host_time") else None
            if args.rate:
                stop_flag.wait(1.0 / args.rate)
            elif prev is not None and t is not None:
                stop_flag.wait(min(max(t - prev, 0.0), 1.0))
            prev = t
            on_sample(s)
        if not args.loop:
            set_status("het file phat lai")
            return


class CsvSink:
    """Ghi tung mau ra mot file CSV, tu luc chay den khi dung chuong trinh.

    Mau di qua hang doi nen vong doc serial khong phai cho o dia, va khong
    bo mau nao nhu kieu doc theo chu ky. Luong ghi duoc join luc thoat nen
    dong cuoi cung luon nam tren dia truoc khi tien trinh ket thuc.
    """

    def __init__(self, path: Path, cols: list[str]) -> None:
        self.path, self.cols = path, cols
        self.q: queue.Queue = queue.Queue(maxsize=20000)
        self.rows = 0
        self.dropped = 0
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def feed(self, s: dict) -> None:
        # Thieu khoa thi de o trong: 0.0 se bi doc nham thanh mot nghiem that.
        row = [round(v, 3) if isinstance(v, float) else v
               for v in (s.get(k, "") for k in self.cols)]
        try:
            self.q.put_nowait(row)
        except queue.Full:
            self.dropped += 1

    def _loop(self) -> None:
        try:
            with self.path.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(self.cols)
                f.flush()
                while True:
                    try:
                        row = self.q.get(timeout=0.2)
                    except queue.Empty:
                        f.flush()                # ranh tay thi day het ra dia
                        if stop_flag.is_set():
                            return               # het mau ton dong moi dong file
                        continue
                    w.writerow(row)
                    self.rows += 1
                    if self.rows % 20 == 0:
                        f.flush()
        except OSError as exc:                   # het dia, mat quyen ghi...
            print(f"[csv] loi ghi {self.path}: {exc}", flush=True)

    def close(self) -> None:
        """Cho luong ghi don het hang doi roi dong file, sau do bao ket qua."""
        self.thread.join(timeout=10.0)
        note = f" (bo {self.dropped} mau vi hang doi day)" if self.dropped else ""
        if self.thread.is_alive():
            note += " (con mau chua kip ghi)"
        print(f"Da ghi {self.rows} dong vao {self.path}{note}")


def run_file(name: str | None) -> Path:
    """Duong dan CSV rieng cho lan chay nay - khong bao gio de len file cu.

    Khong dua ten:  runs/run_<ngay>_<gio>.csv
    Co dua ten:     chen <ngay>_<gio> vao ten, live.csv -> live_20260918_174500.csv
    """
    if name:
        p = Path(name).expanduser()
        path = p.with_name(f"{p.stem}_{RUN_STAMP}{p.suffix or '.csv'}")
    else:
        folder = HERE / "runs"
        folder.mkdir(exist_ok=True)
        path = folder / f"run_{RUN_STAMP}.csv"

    # Hai lan chay trong cung mot giay thi them so thu tu.
    stem, n = path.stem, 2
    while path.exists():
        path = path.with_name(f"{stem}_{n}{path.suffix}")
        n += 1
    return path


def stop_on_signal(signum, frame) -> None:
    """Bi kill/SIGTERM thi xu ly y het Ctrl-C de con kip dong file CSV."""
    raise KeyboardInterrupt


# ----------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True      # goi SSE nho, cho di ngay khong doi gom

    def log_message(self, *a):                      # im lang, tranh spam console
        pass

    def _send(self, code, body: bytes, ctype: str, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("map_live.html", "text/html; charset=utf-8")
        if path == "/map.json":
            return self._file("map.json", "application/json")
        if path == "/state":
            body = json.dumps({
                "status": state["status"], "count": state["count"],
                "lpf_hz": state["lpf_hz"], "lpf_median": state["lpf_median"],
                "last": state["last"], "trail": list(trail)[-500:],
            }, separators=(",", ":")).encode()
            return self._send(200, body, "application/json")
        if path == "/stream":
            return self._stream()
        if path == "/k2":
            return self._file("k2_panel.html", "text/html; charset=utf-8")
        if path == "/k2/state":
            if session is None:
                return self._send(404, b'{"loi":"chua bat --k2-plan"}', "application/json")
            with session_lock:
                body = json.dumps(session.state()).encode()
            return self._send(200, body, "application/json")
        self._send(404, b"khong co trang nay", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/k2/"):
            return self._send(404, b"khong co trang nay", "text/plain; charset=utf-8")
        if session is None:
            return self._send(400, b'{"loi":"chua bat --k2-plan"}', "application/json")

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}

        action = path[len("/k2/"):]
        try:
            with session_lock:
                if action == "start":
                    session.start(body.get("giay"))
                    out = {"ok": True}
                elif action == "stop":
                    out = {"ok": True, "tom_tat": session.stop(body.get("k3", ""),
                                                               body.get("ghi_chu", ""))}
                elif action == "goto":
                    session.goto(int(body.get("dong", 1)) - 1)
                    out = {"ok": True}
                else:
                    return self._send(404, b'{"loi":"khong ro lenh"}', "application/json")
                st = session.state()
        except (RuntimeError, ValueError) as exc:
            return self._send(409, json.dumps({"loi": str(exc)}).encode(),
                              "application/json")

        publish("k2", st)
        self._send(200, json.dumps(out).encode(), "application/json")

    def _file(self, name, ctype):
        p = HERE / name
        if not p.is_file():
            return self._send(404, f"thieu file {name}".encode(), "text/plain; charset=utf-8")
        self._send(200, p.read_bytes(), ctype)

    def _stream(self):
        q: queue.Queue = queue.Queue(maxsize=200)
        with clients_lock:
            clients.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._emit("cfg", json.dumps({"lpf_hz": state["lpf_hz"],
                                          "lpf_median": state["lpf_median"]}))
            self._emit("status", json.dumps({"status": state["status"]}))
            if state["last"]:
                self._emit("pose", json.dumps(state["last"], separators=(",", ":")))
            while not stop_flag.is_set():
                try:
                    kind, msg = q.get(timeout=10.0)
                    self._emit(kind, msg)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")     # giu ket noi song
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with clients_lock:
                if q in clients:
                    clients.remove(q)

    def _emit(self, kind: str, data: str):
        self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())
        self.wfile.flush()


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def open_http(args) -> ThreadingHTTPServer | None:
    """Mo cong HTTP. Cong mac dinh ban thi nhay sang cong ke tiep;
    con neu nguoi dung tu chi dinh --http thi bao loi chu khong doi."""
    first = args.http or 8080
    for port in range(first, first + (1 if args.http else 21)):
        try:
            return ThreadingHTTPServer(("0.0.0.0", port), Handler)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            print(f"[http] cong {port} dang ban", flush=True)
    print(f"\nKhong mo duoc cong HTTP tu {first}.\n"
          f"  Xem ai dang giu:  ss -ltnp | grep {first}\n"
          f"  Roi tat:          kill <PID>")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", help="cong serial cua Tag (mac dinh: tu tim 0483:5740)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--http", type=int,
                    help="cong HTTP (mac dinh 8080, tu nhay cong khac neu ban)")
    ap.add_argument("--replay", type=Path, help="phat lai CSV thay vi doc Tag")
    ap.add_argument("--rate", type=float, help="ep tan so phat lai [Hz]")
    ap.add_argument("--loop", action="store_true", help="phat lai lap vo han")
    ap.add_argument("--csv", type=Path,
                    help="ghi pose ra CSV suot lan chay (moi lan chay mot file moi)")
    ap.add_argument("--record", nargs="?", const="", metavar="FILE",
                    help="ghi 5 gia tri moi mau ra CSV rieng cho lan chay nay; "
                         "khong dua ten thi tu dat runs/run_<ngay>_<gio>.csv")
    ap.add_argument("--lpf-hz", type=float, default=1.0,
                    help="tan so cat cua bo loc thap cho tril_x/tril_y [Hz]; "
                         "0 = tat. Mac dinh 1.0 do tu ban ghi xe dung im ngay "
                         "22/09/2026: sigma 7.0 -> 4.2 cm, diem van xa nhat "
                         "1.01 -> 0.53 m. Thap hon nua loi khong dang bao nhieu "
                         "ma tre khi xe chay tang nhanh.")
    ap.add_argument("--lpf-median", type=int, default=5,
                    help="so mau cua so trung vi chan diem van, chay truoc loc "
                         "thap; 1 = tat. Mac dinh 5: buoc nhay 95%% tu 0.103 "
                         "xuong 0.040 m so voi chi dung loc thap.")
    ap.add_argument("--lpf-jump-m", type=float, default=0.0,
                    help="nghiem tril nhay xa hon bay nhieu met trong mot mau thi "
                         "khoi dong lai bo loc (0 = khong bao gio)")
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--reinit", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--no-stop", action="store_true")
    ap.add_argument("--k2-plan", type=Path,
                    help="SO_PHIEN_K2_PHIEN.csv - bat che do dieu phoi 28 lan ghi")
    ap.add_argument("--k2-out", type=Path,
                    help="thu muc ghi ket qua K2 (mac dinh: canh file ke hoach)")
    ap.add_argument("--k2-seconds", type=float, default=90.0,
                    help="do dai mot lan ghi [giay], mac dinh 90 (§3.1)")
    ap.add_argument("--k2-no-auto-stop", action="store_true",
                    help="het gio khong tu dung, phai bam Stop")
    args = ap.parse_args()

    if not (HERE / "map.json").is_file():
        print("Thieu map.json. Tao bang:\n"
              "  python3 draw_map.py graph.hml map.png --json map.json")
        return 1

    if args.lpf_hz > 0.0:
        global lpf
        lpf = PositionSmoother(args.lpf_hz, args.lpf_median, args.lpf_jump_m)
        state["lpf_hz"] = args.lpf_hz
        state["lpf_median"] = args.lpf_median
        print(f"Loc tril:  trung vi {args.lpf_median} mau -> fc={args.lpf_hz} Hz, "
              f"nhay lai={args.lpf_jump_m or 'tat'} m")

    if args.k2_plan:
        global session
        from k2_session import K2Session
        out_dir = args.k2_out or (args.k2_plan.parent / f"K2_{RUN_STAMP}")
        session = K2Session(args.k2_plan, out_dir, seconds=args.k2_seconds,
                            auto_stop=not args.k2_no_auto_stop)
        print(f"K2:       {len(session.rows)} dong ke hoach tu {args.k2_plan}")
        print(f"          ket qua -> {out_dir}")
        print(f"          bang dieu khien: /k2   (moi lan ghi {args.k2_seconds:.0f}s"
              f"{', tu dung' if not args.k2_no_auto_stop else ', phai bam Stop'})")
        threading.Thread(target=k2_ticker, daemon=True).start()

    if args.record is not None:
        sinks.append(CsvSink(run_file(args.record), RUN_COLUMNS))
    if args.csv:
        sinks.append(CsvSink(run_file(str(args.csv)), LOG_COLUMNS))
    for sink in sinks:
        sink.start()
        print(f"Ghi lai:  {sink.path}   ({', '.join(sink.cols)})")

    src = threading.Thread(target=run_source,
                           args=(replay_loop if args.replay else serial_loop, args),
                           daemon=True)
    src.start()

    srv = open_http(args)
    if srv is None:
        stop_flag.set()
        for sink in sinks:
            sink.close()
        return 1
    srv.daemon_threads = True
    signal.signal(signal.SIGTERM, stop_on_signal)
    print(f"Ban do live:  http://{lan_ip()}:{srv.server_address[1]}    (Ctrl-C de dung)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\ndang dung...")
    finally:
        stop_flag.set()
        srv.shutdown()
        elapsed = time.time() - state["started"]
        print(f"{state['count']} mau trong {elapsed:.1f}s "
              f"({state['count'] / elapsed if elapsed else 0:.1f} Hz)")
        for sink in sinks:
            sink.close()                     # ghi not hang doi roi dong file
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
