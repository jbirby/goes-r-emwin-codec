# goes-r-emwin-codec

Pure-Python extractor and synthetic encoder for **GOES-R EMWIN** files —
the modern weather data downlink carried inside the HRIT (High Rate
Information Transmission) signal on GOES-16 and GOES-18 at 1694.1 MHz.
Packaged as a Claude Skill.

This is the file-layer companion to `goes-emwin-codec` (which handles the
legacy GOES-N/O/P 1692.7 MHz BPSK audio format). GOES-R EMWIN doesn't
ride on a tractable audio waveform — it's wrapped inside HRIT/CCSDS
packets transmitted at ~927 kbps OQPSK, so the realistic input here is
file-level: HRIT files coming out of a tool like `goestools`, or a raw
EMWIN packet stream extracted from the HRIT VCID.

## What it does

- **Decode** HRIT files or raw EMWIN packet streams → original NWS files
  (text bulletins, GIF/JPG imagery, anything they were carrying)
- Auto-detects which format it's given
- Reassembles multi-part files, including parts split across multiple
  input files
- Validates per-packet CRC and end-to-end file CRC
- **Encode** any file into a synthetic HRIT or raw container for testing
  and round-trip verification (not bit-exact to operational HRIT files)

## Install (standalone)

No third-party dependencies.

```bash
python3 scripts/hrit_emwin_encode.py somefile.png signal.lrit
python3 scripts/hrit_emwin_decode.py signal.lrit --out-dir ./extracted
```

## Install (as a Claude Skill)

Drop the `goes-r-emwin.skill` zip into your Claude Skills directory or
use the "Save skill" install button when Claude offers it. Then ask:

> Extract the EMWIN files from these HRIT files.
> Package this PNG as a synthetic HRIT EMWIN container.

## Round-trip self-test

```bash
python3 scripts/test_roundtrip.py
```

Verifies byte-exact recovery for HRIT and raw containers across empty,
small, medium, large, compressed, and uncompressed payloads — plus a
multi-input reassembly case where one file is split across two input
streams.

## License

MIT.
