#!/usr/bin/env python3
"""Check the two PaperDIY release images before they are published.

- The full image (flashed at 0x0) must carry a bootloader, a partition table at
  0x8000 and, at 0x10000, the same app as the app-only image.
- The app-only image must be an ESP32-S3 app (starts with 0xE9).

Adapted from dcarnicer/paperdiy-reader.

Usage:
    python3 scripts/paperdiy/check_release_assets.py trmnl-paperdiy-latest.bin trmnl-paperdiy-1.8.20-1-app.bin
"""
from __future__ import annotations

import argparse
from pathlib import Path

ESP_IMAGE_MAGIC = 0xE9
ESP32S3_CHIP_ID = 0x0009
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_MAGIC = b"\xaa\x50"
APP_OFFSET = 0x10000
# M5Launcher 3.0.0 gave this app a 1472 KB slot; warn before a release stops fitting
LAUNCHER_SLOT_BYTES = 1472 * 1024


def check_app_header(data: bytes, what: str) -> None:
    if len(data) < 24 or data[0] != ESP_IMAGE_MAGIC:
        raise SystemExit(f"{what}: not an ESP app image (no 0xE9 magic)")
    chip_id = int.from_bytes(data[12:14], "little")
    if chip_id != ESP32S3_CHIP_ID:
        raise SystemExit(f"{what}: chip id 0x{chip_id:04X}, expected ESP32-S3 (0x{ESP32S3_CHIP_ID:04X})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("full", type=Path, help="full image flashed at 0x0")
    parser.add_argument("app", type=Path, help="app-only image")
    args = parser.parse_args()

    full = args.full.read_bytes()
    app = args.app.read_bytes()

    check_app_header(app, str(args.app))
    if full[:1] != bytes([ESP_IMAGE_MAGIC]):
        raise SystemExit(f"{args.full}: no bootloader at 0x0")
    if full[PARTITION_TABLE_OFFSET : PARTITION_TABLE_OFFSET + 2] != PARTITION_MAGIC:
        raise SystemExit(f"{args.full}: no partition table at 0x{PARTITION_TABLE_OFFSET:X}")
    check_app_header(full[APP_OFFSET:], f"{args.full} at 0x{APP_OFFSET:X}")
    if full[APP_OFFSET : APP_OFFSET + len(app)] != app:
        raise SystemExit(f"{args.full}: app at 0x{APP_OFFSET:X} differs from {args.app}")
    if len(app) > LAUNCHER_SLOT_BYTES:
        print(f"WARNING: app is {len(app)} bytes, more than the {LAUNCHER_SLOT_BYTES}-byte slot M5Launcher 3.0.0 gave it")

    print(f"release assets OK: {args.full.name} ({len(full)} bytes), {args.app.name} ({len(app)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
