"""Bo loc dung chung cho chuoi vi tri doc tu Tag.

Tach rieng khoi script doc Tag va khoi live_server de hai ben goi cung mot
bo loc, sua mot cho la ca hai doi theo.

    from common.filters import PositionSmoother

    loc = PositionSmoother(cutoff_hz=1.0, median=5)
    x, y = loc.update_if_new(tril_x_m, tril_y_m, timestamp_ms / 1000.0)

PositionSmoother la thu duy nhat noi goi can dung; LowPass2D va MedianFilter2D
o duoi la hai chang ben trong no, de rieng cho de doc va de do tung chang.
"""
from __future__ import annotations

import math
from collections import deque
from statistics import median


class LowPass2D:
    """Loc thap mot cuc cho mot diem (x, y).

    alpha = dt / (RC + dt) voi RC = 1 / (2*pi*fc), nen do muot bam theo khoang
    cach thuc giua hai mau chu khong gia dinh tan so co dinh - mat goi khong
    lam sai do muot.

    Bo loc khoi dong lai (nhan thang gia tri moi thay vi bo dan toi) khi:
      - day la mau dau tien,
      - cach mau truoc qua max_gap_s giay,
      - hoac diem moi cach diem dang giu qua jump_m met (jump_m > 0).

    Doi so:
        cutoff_hz  tan so cat fc [Hz]; phai > 0.
        jump_m     nguong nhay de khoi dong lai [m]; 0 = khong bao gio.
        max_gap_s  khoang lang toi da con coi la lien mach [s].
    """

    def __init__(self, cutoff_hz: float, jump_m: float = 0.0,
                 max_gap_s: float = 1.0) -> None:
        if cutoff_hz <= 0.0:
            raise ValueError("cutoff_hz phai > 0")
        self.rc = 1.0 / (2.0 * math.pi * cutoff_hz)
        self.jump_m = jump_m
        self.max_gap_s = max_gap_s
        self.x: float | None = None
        self.y: float | None = None
        self.t: float | None = None
        self.last_in: tuple[float, float] | None = None

    def reset(self) -> None:
        """Quen diem dang giu; mau ke tiep duoc nhan nguyen."""
        self.x = self.y = self.t = None
        self.last_in = None

    def update_if_new(self, x: float, y: float, t_s: float) -> tuple[float, float]:
        """Nhu update() nhung bo qua mau trung y het mau vua nap.

        Tag phat goi nhanh hon nhip ra nghiem moi, nen mot nghiem duoc lap lai
        nhieu lan. Nap ca ban sao la coi mot nghiem nhu nhieu lan do doc lap:
        bo loc bi keo ve gia tri dang lap, muot kem han va diem van xa gan nhu
        khong bi lam nhe. Do o day theo gia tri vi giao thuc khong danh dau dau
        la nghiem moi. Hai nghiem lien tiep tinh co bang nhau thi bi bo, doi
        lai chi mat mot lan cap nhat.
        """
        if self.last_in == (x, y) and self.x is not None:
            return self.x, self.y
        return self.update(x, y, t_s)

    def update(self, x: float, y: float, t_s: float) -> tuple[float, float]:
        """Nap mot mau tai thoi diem t_s [s], tra ve diem da loc."""
        self.last_in = (x, y)
        dt = None if self.t is None else t_s - self.t
        self.t = t_s

        restart = (
            self.x is None
            or dt is None
            or dt <= 0.0
            or dt > self.max_gap_s
            or (self.jump_m > 0.0 and math.hypot(x - self.x, y - self.y) > self.jump_m)
        )
        if restart:
            self.x, self.y = x, y
            return x, y

        alpha = dt / (self.rc + dt)
        self.x += alpha * (x - self.x)
        self.y += alpha * (y - self.y)
        return self.x, self.y


class MedianFilter2D:
    """Trung vi truot tren N mau gan nhat, tinh rieng tung truc.

    Lam mot viec ma loc thap lam rat te: nem di diem van. Loc thap gap mot
    diem sai 1 m van keo dau ra di mot doan; trung vi thi chi can da so mau
    trong cua so con dung la diem do bi bo qua han.

    Chi nhin ve qua khu nen chay truc tiep duoc, doi lai tre khoang
    (N-1)/2 mau. N chan cung chay duoc nhung N le thi tre it hon.
    """

    def __init__(self, window: int) -> None:
        if window < 1:
            raise ValueError("window phai >= 1")
        self.window = window
        self.xs: deque[float] = deque(maxlen=window)
        self.ys: deque[float] = deque(maxlen=window)

    def reset(self) -> None:
        self.xs.clear()
        self.ys.clear()

    def update(self, x: float, y: float) -> tuple[float, float]:
        self.xs.append(x)
        self.ys.append(y)
        return median(self.xs), median(self.ys)


class PositionSmoother:
    """Trung vi chan diem van, roi loc thap lam muot. Bo mau trung lap.

    Do tren ban ghi xe dung im ngay 22/09/2026 (712 nghiem that, 9.2 Hz,
    moc that da biet), so voi tril tho:

        cau hinh          sigma      buoc nhay 95%   buoc nhay max
        tho               0.070 m    0.220 m         0.955 m
        LPF 1.0 Hz        0.042 m    0.103 m         0.310 m
        med5 + LPF 1.0    0.032 m    0.040 m         0.089 m

    Ha rieng tan so cat xuong 0.3 Hz cung ra duoc buoc nhay 95% tam 0.054 m,
    nhung tre 0.53 s; med3 + LPF 1.0 cho dung con so do voi tre ~0.27 s. Nen
    chan diem van bang trung vi truoc, dung ha fc.

    Doi so:
        cutoff_hz  tan so cat cua chang loc thap [Hz].
        median     so mau cua so trung vi; 1 = tat chang nay.
        jump_m     nguong nhay de khoi dong lai chang loc thap [m]; 0 = tat.
        max_gap_s  khoang lang toi da con coi la lien mach [s].
    """

    def __init__(self, cutoff_hz: float, median: int = 5, jump_m: float = 0.0,
                 max_gap_s: float = 1.0) -> None:
        self.med = MedianFilter2D(median) if median > 1 else None
        self.lpf = LowPass2D(cutoff_hz, jump_m, max_gap_s)
        self.last_in: tuple[float, float] | None = None
        self.out: tuple[float, float] | None = None

    def reset(self) -> None:
        if self.med is not None:
            self.med.reset()
        self.lpf.reset()
        self.last_in = None
        self.out = None

    def update_if_new(self, x: float, y: float, t_s: float) -> tuple[float, float]:
        """Nap mot nghiem; mau trung y het mau vua nap thi tra lai ket qua cu.

        Tag phat goi nhanh hon nhip ra nghiem moi nen mot nghiem duoc lap lai
        nhieu lan - ban ghi 22/09 co 81% dong la ban sao. Nap ca ban sao la
        coi mot phep do nhu nhieu lan do doc lap: cua so trung vi bi mot
        nghiem chiem het, con loc thap thi bi keo ve gia tri dang lap. Do o
        day theo gia tri vi giao thuc khong danh dau dau la nghiem moi. Hai
        nghiem lien tiep tinh co bang nhau thi bi bo, chi mat mot lan cap nhat.
        """
        if self.last_in == (x, y) and self.out is not None:
            return self.out
        self.last_in = (x, y)
        if self.med is not None:
            x, y = self.med.update(x, y)
        self.out = self.lpf.update(x, y, t_s)
        return self.out
