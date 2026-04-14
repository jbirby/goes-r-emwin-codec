"""
Common library for the modern GOES-R EMWIN file extractor / synthesizer.

GOES-R EMWIN is delivered inside the HRIT (High Rate Information
Transmission) downlink at ~927 kbps OQPSK on 1694.1 MHz. The full RF
demodulation stack (Reed-Solomon, Viterbi, CCSDS packet stack) is the job
of dedicated tools like `goestools`. By the time we get involved, the
downlink has already been turned into:

  - HRIT files (one per transmitted file) with structured header records
    followed by a payload, OR
  - A raw stream of EMWIN packets (1116 bytes each) extracted from the
    HRIT/VCID stream.

This library handles both formats:

  - `EmwinPacket` is the on-the-wire 1116-byte block: 80-byte ASCII header,
    1024-byte payload (zlib-compressed for v2 EMWIN), and a 12-byte
    trailer carrying a CRC-32 plus reserved bytes.

  - `HritFile` is a minimal CCSDS-style HRIT container: a primary header
    record followed by an EMWIN data field (a stream of packets).

The synthetic encoder produces files that round-trip cleanly through this
library but do not claim wire-format compatibility with operational HRIT
ingest stacks. The decoder is structured so that swapping in a more
faithful HRIT parser later is a matter of editing one function.
"""
from __future__ import annotations

import math
import os
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKET_HEADER_BYTES = 80
PACKET_PAYLOAD_BYTES = 1024
PACKET_TRAILER_BYTES = 12       # 4-byte CRC-32 + 8 bytes reserved/padding
PACKET_TOTAL_BYTES = (
    PACKET_HEADER_BYTES + PACKET_PAYLOAD_BYTES + PACKET_TRAILER_BYTES
)
assert PACKET_TOTAL_BYTES == 1116, "EMWIN packet must be 1116 bytes"

# HRIT primary header: type=0, length=16, file_type, total_header_len,
# data_field_length_bits. We keep only the fields we need to round-trip.
HRIT_PRIMARY_HEADER_TYPE = 0
HRIT_PRIMARY_HEADER_LEN = 16
HRIT_FILE_TYPE_EMWIN = 0x2A     # arbitrary code we use to flag "EMWIN payload"

# Magic bytes we use to tell HRIT files apart from raw EMWIN streams during
# auto-detect. Real HRIT files always start with type=0 (one byte) followed
# by a 2-byte big-endian record length of 16. That's a useful enough
# fingerprint for our purposes.
HRIT_MAGIC = bytes([HRIT_PRIMARY_HEADER_TYPE, 0x00, HRIT_PRIMARY_HEADER_LEN])


# ---------------------------------------------------------------------------
# CRC-32 (same as zlib / IEEE 802.3)
# ---------------------------------------------------------------------------

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# EMWIN packet
# ---------------------------------------------------------------------------

@dataclass
class EmwinPacket:
    """A single 1116-byte EMWIN over-the-wire packet.

    `compressed_payload` is the on-the-wire payload (zlib-compressed bytes,
    zero-padded out to PACKET_PAYLOAD_BYTES). `payload_len` records the
    actual compressed length so the trailing zero-padding can be stripped
    on decode.
    """
    filename: str
    part: int                      # 1-based
    total: int
    timestamp: datetime
    checksum: int                  # CRC-32 of the original (uncompressed) full file
    payload_len: int               # length in bytes of compressed_payload before padding
    compressed_payload: bytes      # zlib-compressed payload chunk

    HEADER_TIME_FMT = "%d-%b-%y %H:%M:%S"   # e.g. "14-APR-26 18:42:00"

    def to_bytes(self) -> bytes:
        # ASCII header layout, fixed-width (fits inside 80 bytes with padding):
        #   /PF<filename:16>/PN<part:04>/PT<total:04>/CS<crc32:08x>/LN<plen:04>/FD<datetime:18>/
        # 16 char filename comfortably holds the 8.3 names that real EMWIN files use.
        ts = self.timestamp.strftime(self.HEADER_TIME_FMT).upper()
        header = (
            f"/PF{self.filename:<16.16}"
            f"/PN{self.part:04d}"
            f"/PT{self.total:04d}"
            f"/CS{self.checksum:08x}"
            f"/LN{self.payload_len:04d}"
            f"/FD{ts:<18.18}/"
        ).encode("ascii")
        if len(header) > PACKET_HEADER_BYTES:
            raise ValueError(
                f"header would be {len(header)} bytes, max {PACKET_HEADER_BYTES}"
            )
        header = header.ljust(PACKET_HEADER_BYTES, b" ")
        payload = self.compressed_payload.ljust(PACKET_PAYLOAD_BYTES, b"\x00")
        body = header + payload
        crc = crc32(body)
        trailer = struct.pack(">I", crc) + b"\x00" * 8
        return body + trailer

    @classmethod
    def from_bytes(cls, raw: bytes) -> "EmwinPacket":
        if len(raw) != PACKET_TOTAL_BYTES:
            raise ValueError(
                f"packet length {len(raw)} != {PACKET_TOTAL_BYTES}"
            )
        body = raw[: PACKET_HEADER_BYTES + PACKET_PAYLOAD_BYTES]
        trailer = raw[PACKET_HEADER_BYTES + PACKET_PAYLOAD_BYTES :]
        expected_crc = struct.unpack(">I", trailer[:4])[0]
        actual_crc = crc32(body)
        if expected_crc != actual_crc:
            raise ValueError(
                f"CRC mismatch: header says {expected_crc:08x}, computed {actual_crc:08x}"
            )
        header_text = body[:PACKET_HEADER_BYTES].decode("ascii", errors="replace")
        payload = body[PACKET_HEADER_BYTES:]

        fields = {}
        for chunk in header_text.strip().split("/"):
            if len(chunk) < 2:
                continue
            fields[chunk[:2]] = chunk[2:].strip()
        try:
            filename = fields.get("PF", "").strip()
            part = int(fields["PN"])
            total = int(fields["PT"])
            checksum = int(fields["CS"], 16)
            payload_len = int(fields["LN"])
            ts_text = fields["FD"].strip()
        except KeyError as exc:
            raise ValueError(f"missing required header field {exc}") from None
        if not (0 <= payload_len <= PACKET_PAYLOAD_BYTES):
            raise ValueError(f"payload_len out of range: {payload_len}")
        try:
            timestamp = datetime.strptime(ts_text, cls.HEADER_TIME_FMT)
        except ValueError:
            timestamp = datetime(1970, 1, 1)
        return cls(
            filename=filename,
            part=part,
            total=total,
            timestamp=timestamp,
            checksum=checksum,
            payload_len=payload_len,
            compressed_payload=payload[:payload_len],
        )


# ---------------------------------------------------------------------------
# Packet stream <-> file
# ---------------------------------------------------------------------------

def packetize_file(
    filename: str,
    data: bytes,
    timestamp: Optional[datetime] = None,
    compress: bool = True,
) -> List[EmwinPacket]:
    """Split a file's bytes into a list of EmwinPacket objects.

    The raw file is optionally zlib-compressed first (matching the v2
    EMWIN-over-HRIT format), then chunked into 1024-byte payload pieces.
    A CRC-32 over the original file is recorded in every packet header so
    the decoder can verify end-to-end integrity after reassembly.
    """
    if timestamp is None:
        timestamp = datetime.utcnow().replace(microsecond=0)
    file_crc = crc32(data)
    if compress:
        body = zlib.compress(data, level=9)
    else:
        body = data
    if not body:
        # Empty file still gets one anchor packet so the decoder has
        # something to work with.
        return [
            EmwinPacket(
                filename=filename,
                part=1,
                total=1,
                timestamp=timestamp,
                checksum=file_crc,
                payload_len=0,
                compressed_payload=b"",
            )
        ]
    total = math.ceil(len(body) / PACKET_PAYLOAD_BYTES)
    out = []
    for i in range(total):
        chunk = body[i * PACKET_PAYLOAD_BYTES : (i + 1) * PACKET_PAYLOAD_BYTES]
        out.append(
            EmwinPacket(
                filename=filename,
                part=i + 1,
                total=total,
                timestamp=timestamp,
                checksum=file_crc,
                payload_len=len(chunk),
                compressed_payload=chunk,
            )
        )
    return out


def reassemble_packets(packets: Iterable[EmwinPacket]) -> List[Tuple[str, bytes]]:
    """Group packets by filename and reassemble each into its original bytes.

    Returns a list of (filename, payload_bytes). Raises if any file is
    missing parts or fails its CRC check.
    """
    by_file: dict[str, list[EmwinPacket]] = {}
    for p in packets:
        by_file.setdefault(p.filename, []).append(p)

    results: List[Tuple[str, bytes]] = []
    for filename, ps in by_file.items():
        ps.sort(key=lambda x: x.part)
        total = ps[0].total
        if any(p.total != total for p in ps):
            raise ValueError(f"{filename}: inconsistent total-part count")
        seen = {p.part for p in ps}
        missing = [i for i in range(1, total + 1) if i not in seen]
        if missing:
            raise ValueError(f"{filename}: missing packet parts {missing}")
        body = b"".join(p.compressed_payload for p in ps)
        # Try zlib-decompress; if it fails, treat as uncompressed.
        try:
            data = zlib.decompress(body) if body else b""
        except zlib.error:
            data = body
        expected_crc = ps[0].checksum
        actual_crc = crc32(data)
        if expected_crc != actual_crc:
            raise ValueError(
                f"{filename}: end-to-end CRC mismatch "
                f"(header says {expected_crc:08x}, computed {actual_crc:08x})"
            )
        results.append((filename, data))
    return results


# ---------------------------------------------------------------------------
# Raw EMWIN packet stream serialization
# ---------------------------------------------------------------------------

def emwin_stream_to_bytes(packets: Iterable[EmwinPacket]) -> bytes:
    return b"".join(p.to_bytes() for p in packets)


def emwin_stream_from_bytes(blob: bytes) -> List[EmwinPacket]:
    if len(blob) % PACKET_TOTAL_BYTES != 0:
        raise ValueError(
            f"raw EMWIN stream length {len(blob)} is not a multiple of "
            f"{PACKET_TOTAL_BYTES}"
        )
    out = []
    for i in range(0, len(blob), PACKET_TOTAL_BYTES):
        out.append(EmwinPacket.from_bytes(blob[i : i + PACKET_TOTAL_BYTES]))
    return out


# ---------------------------------------------------------------------------
# Minimal HRIT container
# ---------------------------------------------------------------------------

@dataclass
class HritFile:
    """A minimal HRIT-style wrapper around an EMWIN packet stream."""
    file_type: int = HRIT_FILE_TYPE_EMWIN
    data_field: bytes = b""

    def to_bytes(self) -> bytes:
        # 16-byte primary header:
        #   1 byte  : header type (0)
        #   2 bytes : header record length (16)
        #   1 byte  : file type code
        #   4 bytes : total header length (16)
        #   8 bytes : data field length, in bits (big-endian)
        data_bits = len(self.data_field) * 8
        header = struct.pack(
            ">BHBIQ",
            HRIT_PRIMARY_HEADER_TYPE,
            HRIT_PRIMARY_HEADER_LEN,
            self.file_type & 0xFF,
            HRIT_PRIMARY_HEADER_LEN,
            data_bits,
        )
        return header + self.data_field

    @classmethod
    def from_bytes(cls, blob: bytes) -> "HritFile":
        if len(blob) < HRIT_PRIMARY_HEADER_LEN:
            raise ValueError("HRIT file shorter than primary header")
        if not blob.startswith(HRIT_MAGIC):
            raise ValueError("HRIT primary header magic mismatch")
        (
            _hdr_type,
            _record_len,
            file_type,
            total_hdr_len,
            data_bits,
        ) = struct.unpack(">BHBIQ", blob[:HRIT_PRIMARY_HEADER_LEN])
        if total_hdr_len != HRIT_PRIMARY_HEADER_LEN:
            # Real HRIT files have many additional header records here. We
            # skip over them — we don't need their contents to recover the
            # EMWIN payload.
            pass
        data_bytes = data_bits // 8
        data_field = blob[total_hdr_len : total_hdr_len + data_bytes]
        return cls(file_type=file_type, data_field=data_field)


# ---------------------------------------------------------------------------
# Auto-detect on decode
# ---------------------------------------------------------------------------

def looks_like_hrit(blob: bytes) -> bool:
    return blob.startswith(HRIT_MAGIC)


def looks_like_raw_emwin_stream(blob: bytes) -> bool:
    if not blob:
        return False
    if len(blob) % PACKET_TOTAL_BYTES != 0:
        return False
    # Try to parse the first packet.
    try:
        EmwinPacket.from_bytes(blob[:PACKET_TOTAL_BYTES])
    except Exception:
        return False
    return True


def parse_input(blob: bytes) -> List[EmwinPacket]:
    """Auto-detect the input format and return the EMWIN packet stream.

    Accepts either a HRIT file (detected by the primary-header magic) or a
    raw concatenated EMWIN packet stream. Raises ValueError if neither.
    """
    if looks_like_hrit(blob):
        hrit = HritFile.from_bytes(blob)
        return emwin_stream_from_bytes(hrit.data_field)
    if looks_like_raw_emwin_stream(blob):
        return emwin_stream_from_bytes(blob)
    raise ValueError(
        "input is neither a recognized HRIT file (no primary-header magic) "
        f"nor a raw EMWIN packet stream (length not a multiple of {PACKET_TOTAL_BYTES} "
        "or first packet failed to parse)"
    )


# ---------------------------------------------------------------------------
# Convenience helpers used by the CLI scripts
# ---------------------------------------------------------------------------

def encode_file_to_hrit(
    input_path: str,
    timestamp: Optional[datetime] = None,
    compress: bool = True,
) -> bytes:
    with open(input_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(input_path)
    packets = packetize_file(filename, data, timestamp=timestamp, compress=compress)
    stream = emwin_stream_to_bytes(packets)
    return HritFile(data_field=stream).to_bytes()


def encode_file_to_raw_stream(
    input_path: str,
    timestamp: Optional[datetime] = None,
    compress: bool = True,
) -> bytes:
    with open(input_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(input_path)
    packets = packetize_file(filename, data, timestamp=timestamp, compress=compress)
    return emwin_stream_to_bytes(packets)
