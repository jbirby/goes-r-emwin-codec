#!/usr/bin/env python3
"""Encode a file into a synthetic GOES-R EMWIN container (HRIT or raw
packet stream).

Usage:
    python3 hrit_emwin_encode.py INPUT_FILE OUTPUT [options]

Options:
    --format {hrit,raw}   Output container format (default: hrit)
    --no-compress         Skip zlib compression of the payload
    -q, --quiet           Suppress progress messages

The HRIT container is a minimal CCSDS-style wrapper around an EMWIN
packet stream. The raw format is just the concatenated 1116-byte EMWIN
packets, with no outer wrapper. Either round-trips byte-for-byte through
hrit_emwin_decode.py.

These outputs are useful for demos, tests, and round-trip verification.
They are NOT bit-exact to operational HRIT downlink files; for that you
need a real receiver chain (goestools etc.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hrit_emwin_common import (
    encode_file_to_hrit,
    encode_file_to_raw_stream,
)


def encode(
    input_path: str,
    output_path: str,
    fmt: str = "hrit",
    compress: bool = True,
    quiet: bool = False,
) -> None:
    if fmt == "hrit":
        blob = encode_file_to_hrit(input_path, compress=compress)
    elif fmt == "raw":
        blob = encode_file_to_raw_stream(input_path, compress=compress)
    else:
        raise SystemExit(f"--format must be 'hrit' or 'raw', got {fmt!r}")

    Path(output_path).write_bytes(blob)
    if not quiet:
        in_size = Path(input_path).stat().st_size
        print(
            f"Encoded {in_size:,} bytes from {Path(input_path).name!r} "
            f"into {len(blob):,} bytes of {fmt.upper()} "
            f"({'compressed' if compress else 'uncompressed'}) at {output_path}."
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Encode a file into a synthetic GOES-R EMWIN container."
    )
    p.add_argument("input", help="path to the file to encode")
    p.add_argument("output", help="path for the output container")
    p.add_argument(
        "--format",
        choices=("hrit", "raw"),
        default="hrit",
        help="container format (default: hrit)",
    )
    p.add_argument(
        "--no-compress",
        action="store_true",
        help="skip zlib compression of the payload (default: compress)",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)
    encode(
        args.input,
        args.output,
        fmt=args.format,
        compress=not args.no_compress,
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
