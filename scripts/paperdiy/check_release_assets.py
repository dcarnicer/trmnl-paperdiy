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
# M5Launcher 3.0.0 gave this app a 1472 KB slot. An app over it cannot be installed
# under Launcher, so that fails unless --allow-launcher-oversize is given; warn when
# little room is left for the next changes.
LAUNCHER_SLOT_BYTES = 1472 * 1024
LAUNCHER_SLOT_WARN_BYTES = 32 * 1024


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
    parser.add_argument(
        "--allow-launcher-oversize",
        action="store_true",
        help="publish even if the app no longer fits M5Launcher's slot",
    )
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

    headroom = LAUNCHER_SLOT_BYTES - len(app)
    if headroom < 0 and not args.allow_launcher_oversize:
        raise SystemExit(
            f"{args.app}: app is {len(app)} bytes, {-headroom} over the {LAUNCHER_SLOT_BYTES}-byte "
            "slot M5Launcher 3.0.0 gave it: it can no longer be installed from Launcher. "
            "Pass --allow-launcher-oversize to publish anyway (standalone installs still fit "
            "the 3 MB app slot), or trim the app first."
        )
    if 0 <= headroom < LAUNCHER_SLOT_WARN_BYTES:
        print(
            f"WARNING: {args.app.name} leaves only {headroom} bytes in Launcher's "
            f"{LAUNCHER_SLOT_BYTES}-byte slot"
        )

    print(
        f"release assets OK: {args.full.name} ({len(full)} bytes), "
        f"{args.app.name} ({len(app)} bytes, Launcher headroom {headroom} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
