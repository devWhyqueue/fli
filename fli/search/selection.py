"""Helpers for parsing Google Flights selection tokens."""

from base64 import urlsafe_b64decode


def parse_selection_token(data: list) -> str | None:
    """Extract the opaque selection token used for stepwise native flows."""
    try:
        encoded_token = data[1][1]
        if not encoded_token:
            return None
        decoded = urlsafe_b64decode(encoded_token + "=" * ((4 - len(encoded_token) % 4) % 4))
        _, offset = _read_varint(decoded, 0)
        length, offset = _read_varint(decoded, offset)
        return decoded[offset : offset + length].decode()
    except (IndexError, TypeError, ValueError, UnicodeDecodeError):
        return None


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf-style varint from raw bytes."""
    result = 0
    shift = 0
    while True:
        value = buf[pos]
        pos += 1
        result |= (value & 0x7F) << shift
        if not (value & 0x80):
            return result, pos
        shift += 7
