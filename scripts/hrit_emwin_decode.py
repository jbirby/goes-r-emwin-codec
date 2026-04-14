#!/usr/bin/env python3
"""Extract EMWIN files from a GOES-R HRIT file or a raw EMWIN packet stream.

Usage:
    python3 hrit_emwin_decode.py INPUT [INPUT ...] [options]

Auto-detects whether each input is a HRIT-wrapped file or a raw EMWIN
packet stream. Multi-part files spread across multiple inputs are
reassembled automatically — pass them all on the same command line.

Options:
    --out-dir DIR   Directory to write recovered files into (default: .)
    --list          Just list the files that would be extracted, don't write them
    -q, --quiet     Suppress progress messages
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

from hrit_emwin_common import (
    EmwinPacket,
    parse_input,
    reassemble_packets,
)


def _safe_filename(name: str, fallback: str = "decoded.bin") -> str:
    name = os.path.basename(name).strip()
    return name or fallback


def decode(
    input_paths: List[str],
    out_dir: str = ".",
    list_only: bool = False,
    quiet: bool = False,
) -> List[str]:
    all_packets: List[EmwinPacket] = []
    for path in input_paths:
        blob = Path(path).read_bytes()
        try:
            packets = parse_input(blob)
        except ValueError as exc:
            raise SystemExit(f"{path}: {exc}")
        if not quiet:
            kind = "HRIT" if blob.startswith(b"\x00\x00\x10") else "raw EMWIN stream"
            print(f"{path}: parsed as {kind}, {len(packets)} packet(s)")
        all_packets.extend(packets)

    if not all_packets:
        raise SystemExit("no EMWIN packets found in input")

    files = reassemble_packets(all_packets)

    if list_only:
        for filename, data in files:
            print(f"  {filename}  ({len(data):,} bytes)")
        return [filename for filename, _ in files]

    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for filename, data in files:
        safe = _safe_filename(filename)
        out_path = os.path.join(out_dir, safe)
        Path(out_path).write_bytes(data)
        written.append(out_path)
        if not quiet:
            print(f"  wrote {out_path} ({len(data):,} bytes)")
    return written


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Extract EMWIN files from HRIT or raw EMWIN packet stream input."
    )
    p.add_argument("inputs", nargs="+", help="one or more HRIT files or raw EMWIN streams")
    p.add_argument("--out-dir", default=".", help="output directory (default: .)")
    p.add_argument(
        "--list",
        action="store_true",
        help="list what would be extracted without writing",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)
    decode(args.inputs, out_dir=args.out_dir, list_only=args.list, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
