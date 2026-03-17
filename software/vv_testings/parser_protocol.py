from __future__ import annotations

from vv_commands import CommandFactory
from vv_transport import HdlcChunk, HdlcCodec, HostTransport, VvAddress, VvProtocol as _VvProtocol


class VvProtocol(_VvProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._commands = CommandFactory()

    def build_none(self, src: int, dst: int, seq: int):
        return self._commands.none(src, dst, seq)

    def build_transport_set(self, src: int, dst: int, seq: int, transport: HostTransport = HostTransport.USB):
        pkt = self._commands.host_transport_set(src, dst, seq)
        pkt.host_transport_set.transport = int(transport)
        return pkt

    def build_device_info_get(self, src: int, dst: int, seq: int):
        return self._commands.device_information_get(src, dst, seq)
