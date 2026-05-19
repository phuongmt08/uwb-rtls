import os
from models.data_models import HexImage, DfuError

class HexService:
    @staticmethod
    def parse_hex_line(line: str) -> tuple[int, int, int, bytes]:
        if not line.startswith(":"):
            raise DfuError("Invalid HEX line: missing ':'")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5:
            raise DfuError("Invalid HEX line: too short")

        byte_count = raw[0]
        if len(raw) != (5 + byte_count):
            raise DfuError("Invalid HEX line length")

        checksum = sum(raw) & 0xFF
        if checksum != 0:
            raise DfuError("HEX checksum mismatch")

        address = (raw[1] << 8) | raw[2]
        rectype = raw[3]
        payload = raw[4 : 4 + byte_count]
        return byte_count, address, rectype, payload

    @staticmethod
    def load_hex_image(path: str) -> HexImage:
        if not path:
            raise DfuError("No HEX file selected")
        if not os.path.exists(path):
            raise DfuError("Firmware file does not exist")
        memory = {}
        upper_linear = 0
        upper_segment = 0

        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                row = line.strip()
                if not row:
                    continue
                try:
                    count, addr16, rectype, payload = HexService.parse_hex_line(row)
                except Exception as exc:
                    raise DfuError(f"HEX parse error at line {line_no}: {exc}") from exc

                if rectype == 0x00:
                    base = upper_linear + upper_segment + addr16
                    for offset, value in enumerate(payload):
                        memory[base + offset] = value
                elif rectype == 0x01:
                    break
                elif rectype == 0x02:
                    if count != 2:
                        raise DfuError(f"Invalid type 02 record at line {line_no}")
                    upper_segment = int.from_bytes(payload, "big") << 4
                    upper_linear = 0
                elif rectype == 0x04:
                    if count != 2:
                        raise DfuError(f"Invalid type 04 record at line {line_no}")
                    upper_linear = int.from_bytes(payload, "big") << 16
                    upper_segment = 0
                elif rectype == 0x05:
                    continue
                else:
                    raise DfuError(f"Unsupported HEX record type 0x{rectype:02X} at line {line_no}")

        if not memory:
            raise DfuError("HEX has no data records")

        start = min(memory.keys())
        end = max(memory.keys())
        length = end - start + 1
        data = bytearray([0xFF] * length)
        for address, value in memory.items():
            data[address - start] = value

        return HexImage(start_address=start, data=bytes(data))
