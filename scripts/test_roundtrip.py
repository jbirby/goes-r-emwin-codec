#!/usr/bin/env python3
"""Round-trip test: encode files into HRIT and raw streams, decode, verify."""
import os
import sys
import tempfile
import hashlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from hrit_emwin_encode import encode
from hrit_emwin_decode import decode


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_case(name: str, payload: bytes, fmt: str, compress: bool = True) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, name)
        container_path = os.path.join(tmp, f"out.{fmt}")
        out_dir = os.path.join(tmp, "decoded")
        Path(in_path).write_bytes(payload)
        encode(in_path, container_path, fmt=fmt, compress=compress, quiet=True)
        decode([container_path], out_dir=out_dir, quiet=True)
        recovered = Path(os.path.join(out_dir, name)).read_bytes()
        assert recovered == payload, (
            f"[{name} fmt={fmt} compress={compress}] mismatch: "
            f"orig sha={_hash(payload)[:12]}, got sha={_hash(recovered)[:12]}, "
            f"orig len={len(payload)}, got len={len(recovered)}"
        )
        cs = os.path.getsize(container_path)
        print(
            f"  PASS  {name:<24} fmt={fmt:<4} compress={compress!s:<5}  "
            f"in={len(payload):>6}B  container={cs:>9,}B  sha={_hash(payload)[:12]}"
        )


def run_multi_input_case() -> None:
    """Two HRIT files together, each carrying part of a larger fictional file."""
    # Easier path: encode one file into a raw stream, split the bytes
    # in half, write each half as a standalone raw stream, decode by passing
    # both. Since EMWIN packets are independently framed and headered, this
    # works.
    from hrit_emwin_common import (
        encode_file_to_raw_stream,
        PACKET_TOTAL_BYTES,
    )
    with tempfile.TemporaryDirectory() as tmp:
        # Use incompressible random data so we actually get multiple packets
        # after zlib compression.
        import random
        rng = random.Random(0xBEEF)
        payload = bytes(rng.randrange(256) for _ in range(20_000))
        in_path = os.path.join(tmp, "split.bin")
        Path(in_path).write_bytes(payload)
        full_stream = encode_file_to_raw_stream(in_path, compress=True)
        # Split at a packet boundary.
        n_packets = len(full_stream) // PACKET_TOTAL_BYTES
        split = (n_packets // 2) * PACKET_TOTAL_BYTES
        a = os.path.join(tmp, "a.raw")
        b = os.path.join(tmp, "b.raw")
        Path(a).write_bytes(full_stream[:split])
        Path(b).write_bytes(full_stream[split:])
        out_dir = os.path.join(tmp, "decoded")
        decode([a, b], out_dir=out_dir, quiet=True)
        recovered = Path(os.path.join(out_dir, "split.bin")).read_bytes()
        assert recovered == payload, "multi-input reassembly mismatch"
        print(
            f"  PASS  split-across-2-files     fmt=raw  compress=True   "
            f"in={len(payload):>6}B  (reassembled from 2 input files)"
        )


def main() -> int:
    print("Round-trip tests")
    cases = [
        ("hello.txt", b"Hello, GOES-R EMWIN! This is a test bulletin.\n"),
        ("empty.bin", b""),
        ("medium.bin", bytes((i * 31 + 7) & 0xFF for i in range(5_000))),
        ("large.bin", bytes((i ^ (i >> 5)) & 0xFF for i in range(50_000))),
    ]
    for name, payload in cases:
        run_case(name, payload, fmt="hrit", compress=True)
        run_case(name, payload, fmt="raw", compress=True)
    # Uncompressed path.
    run_case("text.txt", b"WFUS54 KFWD 141822\nTORFWD\nTornado Warning\n" * 20,
             fmt="hrit", compress=False)
    # Real-feeling NWS bulletin.
    bulletin = (
        b"WFUS54 KFWD 141822\nTORFWD\n"
        b"BULLETIN - IMMEDIATE BROADCAST REQUESTED\n"
        b"Tornado Warning\nNational Weather Service Fort Worth TX\n"
        b"122 PM CDT TUE APR 14 2026\n"
    ) * 30
    run_case("WFUS54KFWD.TXT", bulletin, fmt="hrit", compress=True)
    # Multi-input reassembly.
    run_multi_input_case()
    print("All round-trip tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
