#!/usr/bin/env python3
"""Print the PlatformIO core directory to build a project environment with.

There is one core per platform release under ~/.platformio-cores, for example
pioarduino-55.03.37 or espressif32-6.12.0. Every clone, worktree and project
that pins the same release shares it, so its toolchain and framework are
downloaded once. Different releases never share a core, so one cannot replace
the framework or toolchain another one pinned (pioarduino and the official
platform both install packages called framework-arduinoespressif32).

Usage: pio_core_dir.py [--pio PIO] ENV
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

CORES = Path.home() / ".platformio-cores"


def env_platform(env, pio):
    out = subprocess.run(
        [pio, "project", "config", "--json-output"],
        capture_output=True, text=True, check=True,
    ).stdout
    sections = {name: dict(options) for name, options in json.loads(out)}
    for name in (f"env:{env}", "env"):
        platform = sections.get(name, {}).get("platform")
        if platform:
            return platform
    return None


def core_key(platform):
    release = re.search(
        r"/pioarduino/platform-espressif32/releases/download/([^/]+)/", platform)
    if release:
        return f"pioarduino-{release.group(1)}"
    if "://" in platform:
        stem = Path(platform.split("?")[0]).stem or "platform"
        digest = hashlib.sha1(platform.encode()).hexdigest()[:8]
        return f"{stem}-{digest}"
    # Registry spec, e.g. platformio/espressif32@6.12.0 -> espressif32-6.12.0
    name = platform.split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")


def main():
    args = sys.argv[1:]
    pio = "pio"
    if len(args) >= 2 and args[0] == "--pio":
        pio, args = args[1], args[2:]
    if len(args) != 1:
        sys.exit(__doc__.strip().splitlines()[-1])
    try:
        platform = env_platform(args[0], pio)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        platform = None
        print(f"pio_core_dir: cannot read the project config ({exc})", file=sys.stderr)
    if not platform:
        # Old behaviour: a core inside the checkout.
        print(Path.cwd() / ".platformio-core")
        return
    print(CORES / core_key(platform))


if __name__ == "__main__":
    main()
