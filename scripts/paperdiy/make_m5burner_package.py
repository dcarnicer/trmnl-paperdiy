#!/usr/bin/env python3
"""Build an M5Burner package from the TRMNL_X_PAPERDIY build.

The M5Burner format is a directory with m5burner.json and a firmware/ folder
whose files are named <name>_<hex-address>.bin. We ship the factory image at
0x0 so bootloader, partition table and app stay together. Adapted from
dcarnicer/paperdiy-reader.

Usage:
    python3 scripts/paperdiy/make_m5burner_package.py --version 1.8.20-1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BUILD = PROJECT_DIR / ".pio" / "build" / "TRMNL_X_PAPERDIY"
DEFAULT_OUTPUT = PROJECT_DIR / "dist" / "m5burner"
M5BURNER_DIR = PROJECT_DIR / "m5burner"
FIRMWARE_SLUG = "TRMNLPaperDIY"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="X.Y.Z-N, as in the release tag")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    factory = args.build_dir / "trmnl-paperdiy-factory.bin"
    if not factory.is_file():
        raise SystemExit(f"missing {factory}; run `pio run -e TRMNL_X_PAPERDIY` first")

    package_name = f"trmnl-paperdiy-{args.version}"
    package_dir = args.output_dir / package_name
    if package_dir.exists():
        shutil.rmtree(package_dir)
    (package_dir / "firmware").mkdir(parents=True)

    metadata = json.loads((M5BURNER_DIR / "m5burner.json").read_text(encoding="utf-8"))
    metadata["version"] = args.version
    (package_dir / "m5burner.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    shutil.copy2(factory, package_dir / "firmware" / f"{FIRMWARE_SLUG}_0x0.bin")
    shutil.copy2(PROJECT_DIR / "LICENSE", package_dir / "LICENSE")
    shutil.copy2(M5BURNER_DIR / "PACKAGE_README.md", package_dir / "README.md")
    cover = M5BURNER_DIR / "cover.png"
    if cover.is_file():
        shutil.copy2(cover, package_dir / "cover.png")
    else:
        print("note: no m5burner/cover.png; the Publish dialog asks for one")

    archive = args.output_dir / f"{package_name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))

    # The desktop Publish dialog takes a single .bin, named like the GitHub release asset
    binary = args.output_dir / "trmnl-paperdiy-latest.bin"
    shutil.copy2(factory, binary)

    print(f"package: {package_dir}")
    print(f"archive (repo/PR route): {archive}  sha256 {sha256(archive)}")
    print(f"binary (desktop Publish dialog): {binary}  sha256 {sha256(binary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
