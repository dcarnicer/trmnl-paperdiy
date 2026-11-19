#!/usr/bin/env python3
"""Update the TRMNL app on the PaperDIY without touching NVS or M5Launcher.

The factory image (`trmnl-paperdiy-factory.bin`) is for a first install: it
rewrites the bootloader and partition table at 0x0. This script reads the live
partition table, finds the partition that already holds this firmware (or the
Launcher slot whose label starts with "trmnl"), checks that the new app fits,
and writes `firmware.bin` there.

NVS (Wi-Fi credentials, the custom API URL), otadata and LittleFS are left
alone. It works both under M5Launcher and on a standalone install.

The board must be in download mode: hold the power button for about 2 seconds
until the red LED blinks. With M5Launcher, that also keeps Launcher installed.

Usage:
    python3 scripts/paperdiy/flash_app.py
    python3 scripts/paperdiy/flash_app.py --port /dev/cu.usbmodemXXXX
    python3 scripts/paperdiy/flash_app.py --dry-run

Run `make build` first.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_APP = PROJECT_DIR / ".pio" / "build" / "TRMNL_X_PAPERDIY" / "firmware.bin"
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_TABLE_SIZE = 0xC00
PARTITION_MAGIC = b"\xaa\x50"
APP_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_PROJECT = b"trmnl-firmware"
APP_DESC_VERSION_PREFIX = b"paperdiy-"
# ESP partition types/subtypes (esp_flash_partitions.h).
TYPE_APP = 0x00
SUBTYPE_FACTORY = 0x00
SUBTYPE_OTA_0 = 0x10
SUBTYPE_OTA_1 = 0x11
SUBTYPE_TEST = 0x20
TYPE_DATA = 0x01
SUBTYPE_DATA_OTA = 0x00


def find_esptool() -> list[str]:
    explicit = os.environ.get("ESPTOOL")
    if explicit:
        return [explicit]
    for name in ("esptool.py", "esptool"):
        path = shutil.which(name)
        if path:
            return [path]
    core_dir = Path(os.environ.get("PLATFORMIO_CORE_DIR", ""))
    if not core_dir:
        local_core = PROJECT_DIR / ".platformio-core"
        core_dir = local_core if local_core.is_dir() else Path.home() / ".platformio"
    penv_python = core_dir / "penv" / "bin" / "python"
    tool_esptool = core_dir / "packages" / "tool-esptoolpy" / "esptool.py"
    if penv_python.is_file() and tool_esptool.is_file():
        return [str(penv_python), str(tool_esptool)]
    try:
        import esptool  # noqa: F401

        return [sys.executable, "-m", "esptool"]
    except ImportError:
        raise SystemExit(
            "esptool not found. Run this with PlatformIO's Python, install esptool, or set ESPTOOL=/path/to/esptool.py"
        )


def find_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from serial.tools import list_ports

        ports = [p.device for p in list_ports.comports()]
    except ImportError:
        ports = []
    if not ports:
        ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                       + glob.glob("/dev/cu.wchusbserial*") + glob.glob("/dev/ttyACM*")
                       + glob.glob("/dev/ttyUSB*"))
    ports = [p for p in ports if "Bluetooth" not in p and "debug-console" not in p]
    if len(ports) == 1:
        return ports[0]
    if not ports:
        raise SystemExit("No serial port found; pass --port (or put the board in download mode and retry)")
    raise SystemExit("Several serial ports found; pass --port:\n  " + "\n  ".join(ports))


class Partition:
    def __init__(self, type_: int, subtype: int, offset: int, size: int, label: str) -> None:
        self.type = type_
        self.subtype = subtype
        self.offset = offset
        self.size = size
        self.label = label

    @property
    def is_ota_app(self) -> bool:
        return self.type == TYPE_APP and self.subtype in (SUBTYPE_OTA_0, SUBTYPE_OTA_1)

    def __str__(self) -> str:
        return (f"{self.label} (type {self.type:#x}, subtype {self.subtype:#x}, "
                f"offset {self.offset:#x}, size {self.size:#x})")


def parse_partitions(blob: bytes) -> list[Partition]:
    partitions: list[Partition] = []
    for start in range(0, len(blob) - 31, 32):
        entry = blob[start : start + 32]
        if entry == b"\xff" * 32:
            break
        if entry[:2] != PARTITION_MAGIC:
            continue
        type_, subtype = entry[2], entry[3]
        offset, size = struct.unpack_from("<II", entry, 4)
        label = entry[12:28].split(b"\x00", 1)[0].decode(errors="replace")
        partitions.append(Partition(type_, subtype, offset, size, label))
    return partitions


def run_esptool(esptool: list[str], port: str, args: list[str]) -> None:
    cmd = esptool + ["--chip", "esp32s3", "--port", port, "--baud", "921600"] + args
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"esptool failed ({exc.returncode}). Put the board in download mode (hold the power button ~2 s, "
            "the red LED blinks) and retry."
        )


def read_flash(esptool: list[str], port: str, offset: int, size: int) -> bytes:
    with tempfile.TemporaryDirectory(prefix="paperdiy-flash-") as tmp:
        out = Path(tmp) / "read.bin"
        run_esptool(esptool, port, ["read_flash", hex(offset), hex(size), str(out)])
        return out.read_bytes()


def read_app_identity(esptool: list[str], port: str, part: Partition) -> tuple[str, str] | None:
    blob = read_flash(esptool, port, part.offset, 0x80)
    if len(blob) < 0x70 or blob[0] != APP_MAGIC:
        return None
    if struct.unpack_from("<I", blob, 0x20)[0] != APP_DESC_MAGIC:
        return None
    version = blob[0x30:0x50].split(b"\x00", 1)[0].decode(errors="replace")
    project = blob[0x50:0x70].split(b"\x00", 1)[0].decode(errors="replace")
    return version, project


def is_trmnl_app(identity: tuple[str, str] | None) -> bool:
    if not identity:
        return False
    version, project = identity
    return project == APP_DESC_PROJECT.decode() or version.startswith(APP_DESC_VERSION_PREFIX.decode())


def active_ota_partition(esptool: list[str], port: str, parts: list[Partition],
                         candidates: list[Partition]) -> Partition | None:
    """Pick the slot the bootloader would boot, using otadata (standalone installs)."""
    otadata = next((p for p in parts if p.type == TYPE_DATA and p.subtype == SUBTYPE_DATA_OTA), None)
    if not otadata:
        return None
    blob = read_flash(esptool, port, otadata.offset, min(otadata.size, 0x2000))
    valid: list[tuple[int, int]] = []
    for start in (0, 0x1000):
        entry = blob[start : start + 32]
        if len(entry) < 32:
            continue
        seq = struct.unpack_from("<I", entry, 0)[0]
        state = struct.unpack_from("<I", entry, 24)[0]
        crc = struct.unpack_from("<I", entry, 28)[0]
        if seq in (0, 0xFFFFFFFF) or state in (3, 4):  # invalid or aborted
            continue
        if crc != zlib.crc32(entry[:4]) & 0xFFFFFFFF:
            continue
        valid.append((seq, start))
    if not valid:
        return None
    seq = max(valid)[0]
    ota_apps = sorted((p for p in parts if p.is_ota_app), key=lambda p: p.subtype)
    if not ota_apps:
        return None
    slot = (seq - 1) % len(ota_apps)
    chosen = ota_apps[slot]
    return chosen if chosen in candidates else None


def choose_partition(esptool: list[str], port: str, parts: list[Partition], app_size: int) -> Partition:
    # Launcher's resident app is subtype "test"; never overwrite it.
    candidates = [p for p in parts if p.type == TYPE_APP
                  and p.subtype in (SUBTYPE_FACTORY, SUBTYPE_OTA_0, SUBTYPE_OTA_1)
                  and p.size >= app_size]
    if not candidates:
        raise SystemExit("No app partition is large enough for firmware.bin")

    matches: list[Partition] = []
    for part in candidates:
        identity = read_app_identity(esptool, port, part)
        if is_trmnl_app(identity):
            matches.append(part)
    if not matches:
        # Old images may not carry this project name; fall back to the Launcher label.
        matches = [p for p in candidates if p.label.lower().startswith("trmnl")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        labelled = [p for p in matches if p.label.lower().startswith("trmnl")]
        if len(labelled) == 1:
            return labelled[0]
        active = active_ota_partition(esptool, port, parts, matches)
        if active:
            return active
        # Blank otadata: the bootloader falls back to the factory app, then app0.
        factory = [p for p in matches if p.subtype == SUBTYPE_FACTORY]
        if len(factory) == 1:
            return factory[0]
        ota0 = [p for p in matches if p.subtype == SUBTYPE_OTA_0]
        if len(ota0) == 1:
            return ota0[0]
        raise SystemExit(
            "Several TRMNL partitions found; pass --offset:\n  " + "\n  ".join(str(p) for p in matches)
        )
    raise SystemExit(
        "Could not identify the TRMNL app partition; pass --offset. Candidates:\n  "
        + "\n  ".join(str(p) for p in candidates)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP, help="app image to write (default: firmware.bin)")
    parser.add_argument("--port", help="serial port (default: the only USB serial port)")
    parser.add_argument("--offset", type=lambda value: int(value, 0),
                        help="app partition offset, if auto-detection is ambiguous")
    parser.add_argument("--dry-run", action="store_true", help="show the partition and stop")
    args = parser.parse_args()

    if not args.app.is_file():
        raise SystemExit(f"missing {args.app}; run `pio run -e TRMNL_X_PAPERDIY` first")
    app = args.app.read_bytes()
    if not app or app[0] != APP_MAGIC:
        raise SystemExit(f"{args.app} is not an ESP app image")

    esptool = find_esptool()
    port = find_port(args.port)
    table = read_flash(esptool, port, PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE)
    parts = parse_partitions(table)
    if not parts:
        raise SystemExit("No partition table found at 0x8000")

    if args.offset is not None:
        part = next((p for p in parts if p.offset == args.offset), None)
        if not part:
            raise SystemExit(f"No partition starts at {args.offset:#x}")
    else:
        part = choose_partition(esptool, port, parts, len(app))

    if len(app) > part.size:
        raise SystemExit(f"{args.app.name} is {len(app)} bytes but {part.label} is {part.size} bytes")
    free = part.size - len(app)
    print(f"port: {port}")
    print(f"app:  {args.app} ({len(app)} bytes)")
    print(f"slot: {part.label} at {part.offset:#x}, {free} bytes free")
    print("NVS, otadata and LittleFS are not touched: Wi-Fi and the API URL stay.")

    if args.dry_run:
        print("dry run: not writing")
        return 0

    run_esptool(esptool, port, ["write_flash", hex(part.offset), str(args.app)])
    print("done; the board resets. Under Launcher, pick TRMNL from the menu if it does not resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
