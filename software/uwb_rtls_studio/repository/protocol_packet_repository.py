"""
Top-level protobuf packet repository.

ProtocolService owns transport decoding. This repository receives decoded
protobuf packets, stores recent raw payloads for debug, then delegates domain
parsing to smaller repositories.
"""
from __future__ import annotations

import logging

from data.raw_packet import RawPacket
from data.raw_packet_store import shared_raw_packet_store
from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class ProtocolPacketRepository:
    def __init__(self, *repositories):
        self._repositories = list(repositories)

    def add_repository(self, repository) -> None:
        self._repositories.append(repository)

    def handle_packet(self, param_name: str, pkt) -> None:
        shared_raw_packet_store.append(RawPacket.from_proto(param_name, pkt))
        if shared_app_state.manual_test_mode_enabled:
            return
        for repository in self._repositories:
            handler = getattr(repository, "handle_packet", None)
            if not handler:
                continue
            try:
                handler(param_name, pkt)
            except Exception as exc:
                log.exception("Repository %s failed to handle %s: %s", type(repository).__name__, param_name, exc)
