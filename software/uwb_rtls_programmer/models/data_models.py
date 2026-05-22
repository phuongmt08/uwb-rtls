from dataclasses import dataclass
from typing import Optional

@dataclass
class DeviceInfo:
    vid: int
    pid: int
    bus: Optional[int]
    address: Optional[int]
    interface_number: int

@dataclass
class HexImage:
    start_address: int
    data: bytes

class DfuError(RuntimeError):
    pass
