#!/usr/bin/env python3
"""Validate a PaperDIY release tag against the firmware version.

PaperDIY releases are versioned <TRMNL firmware X.Y.Z>-<N>, N being the port
revision: tag paperdiy-vX.Y.Z-N (or paperdiy-vX.Y.Z-N-rc.M). X.Y.Z must match
FW_MAJOR/MINOR/PATCH_VERSION in include/config.h. Adapted from
dcarnicer/paperdiy-reader.

Usage:
    python3 scripts/paperdiy/check_release_version.py paperdiy-v1.8.20-1
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
TAG_RE = re.compile(r"^paperdiy-v(?P<fw>\d+\.\d+\.\d+)-(?P<rev>[1-9]\d*)(?:-rc\.[1-9]\d*)?$")


def firmware_version() -> str:
    text = (PROJECT_DIR / "include" / "config.h").read_text(encoding="utf-8")
    parts = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        match = re.search(rf"^#define FW_{name}_VERSION\s+(\d+)\s*$", text, re.MULTILINE)
        if not match:
            raise SystemExit(f"missing FW_{name}_VERSION in include/config.h")
        parts.append(match.group(1))
    return ".".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", default=os.environ.get("GITHUB_REF_NAME", ""))
    args = parser.parse_args()

    match = TAG_RE.match(args.tag)
    if not match:
        raise SystemExit(f"bad tag {args.tag!r}: expected paperdiy-vX.Y.Z-N or paperdiy-vX.Y.Z-N-rc.M")
    fw = firmware_version()
    if match.group("fw") != fw:
        raise SystemExit(f"tag {args.tag} is for firmware {match.group('fw')}, include/config.h says {fw}")
    print(f"release tag OK: {args.tag} (firmware {fw}, port revision {match.group('rev')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
