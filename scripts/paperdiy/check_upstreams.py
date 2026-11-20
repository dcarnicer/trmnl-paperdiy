#!/usr/bin/env python3
"""Read-only report of upstream and component updates that can affect the PaperDIY.

Checks:
- usetrmnl/trmnl-firmware: how far the local branch is from upstream/main.
- M5Stack: M5PM1, M5PaperDIY-UserDemo, M5GFX, M5Unified.
- bmorcelli/Launcher (the OTA/partition manager).
- bitbank2: FastEPD, bb_epaper, PNGdec, JPEGDEC.
- pioarduino/platform-espressif32.
- dcarnicer/paperdiy-reader (the sibling port).

It never changes dependency pins or flashes anything. Run `make check-updates`
and use the report to decide what to bump; then build, test and, for panel/PM1
changes, do a hardware smoke test.

GitHub needs a token for a useful rate limit. Set GITHUB_TOKEN/GH_TOKEN, or the
script reads `gh auth token` when the GitHub CLI is available.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
PLATFORMIO = PROJECT_DIR / "platformio.ini"
UPSTREAM_BRANCH = "upstream/main"

REPOS = [
    # (label, repo, kind)
    ("M5PM1", "m5stack/M5PM1", "m5"),
    ("M5PaperDIY-UserDemo", "m5stack/M5PaperDIY-UserDemo", "m5"),
    ("M5GFX", "m5stack/M5GFX", "m5"),
    ("M5Unified", "m5stack/M5Unified", "m5"),
    ("M5Launcher", "bmorcelli/Launcher", "m5"),
    ("FastEPD", "bitbank2/FastEPD", "bitbank"),
    ("bb_epaper", "bitbank2/bb_epaper", "bitbank"),
    ("PNGdec", "bitbank2/PNGdec", "bitbank"),
    ("JPEGDEC", "bitbank2/JPEGDEC", "bitbank"),
    ("pioarduino", "pioarduino/platform-espressif32", "platform"),
    ("paperdiy-reader", "dcarnicer/paperdiy-reader", "related"),
]


def find_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


TOKEN = find_token()


def github(path: str):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "trmnl-paperdiy-update-check",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (403, 429):
            print(f"  ! GitHub rate limit for {path}; set GITHUB_TOKEN", file=sys.stderr)
            return None
        raise
    except OSError as exc:
        print(f"  ! GitHub request failed for {path}: {exc}", file=sys.stderr)
        return None


def local_pins() -> dict[str, str]:
    text = PLATFORMIO.read_text(encoding="utf-8")
    section_match = re.search(r"^\[env:TRMNL_X_PAPERDIY\]\n(.*?)(?=^\[)", text, re.MULTILINE | re.DOTALL)
    section = section_match.group(1) if section_match else text

    def first(pattern: str, haystack: str = text) -> str:
        match = re.search(pattern, haystack)
        return match.group(1) if match else "?"

    return {
        "FastEPD": first(r"bitbank2/FastEPD\.git#([0-9a-fA-F]+)", section),
        "bb_epaper": first(r"bitbank2/bb_epaper\.git#([0-9a-fA-F]+)"),
        "PNGdec": first(r"bitbank2/PNGdec(?:@|\.git#)([^\s]+)"),
        "JPEGDEC": first(r"bitbank2/JPEGDEC(?:@|\.git#)([^\s]+)"),
        "platform": first(r"platform-espressif32/releases/download/([^/]+)/platform-espressif32\.zip", section),
    }


def short_sha(value: str | None) -> str:
    return (value or "?")[:12]


def release_line(repo: str) -> str:
    release = github(f"/repos/{repo}/releases/latest")
    if not release:
        return "no release"
    return f"{release.get('tag_name', '?')} ({release.get('published_at', '?')[:10]})"


def head_line(repo: str) -> str:
    commits = github(f"/repos/{repo}/commits?per_page=1")
    if not commits:
        return "no commits"
    item = commits[0]
    commit = item.get("commit", {})
    message = commit.get("message", "").splitlines()[0]
    return f"{short_sha(item.get('sha'))} ({commit.get('author', {}).get('date', '?')[:10]}) {message}"


def compare(repo: str, base: str, head: str) -> str:
    if not base or not head or base == "?" or head == "?":
        return ""
    data = github(f"/repos/{repo}/compare/{base}...{head}")
    if not data:
        return ""
    status = data.get("status", "?")
    ahead = data.get("ahead_by", 0)
    behind = data.get("behind_by", 0)
    return f"compare: {status}, {ahead} ahead / {behind} behind"


def upstream_report() -> None:
    print("== Upstream firmware ==")
    try:
        url = subprocess.check_output(["git", "remote", "get-url", "upstream"], cwd=PROJECT_DIR, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        url = "?"
    print(f"remote: {url}")
    try:
        subprocess.run(["git", "fetch", "--quiet", "upstream"], cwd=PROJECT_DIR, check=True, timeout=60)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  ! git fetch failed ({exc}); using existing refs", file=sys.stderr)
    try:
        counts = subprocess.check_output(
            ["git", "rev-list", "--left-right", "--count", f"HEAD...{UPSTREAM_BRANCH}"],
            cwd=PROJECT_DIR, text=True,
        ).split()
        ahead, behind = counts
        print(f"HEAD is {ahead} ahead, {behind} behind {UPSTREAM_BRANCH}")
        if behind != "0":
            pending = subprocess.check_output(
                ["git", "log", "--oneline", "--no-decorate", "-10", f"HEAD..{UPSTREAM_BRANCH}"],
                cwd=PROJECT_DIR, text=True,
            ).strip()
            print("pending upstream commits:")
            for line in pending.splitlines():
                print(f"  {line}")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"  ! could not compare with {UPSTREAM_BRANCH}: {exc}")
    print()


def repos_report() -> None:
    pins = local_pins()
    printed_header = set()
    for label, repo, kind in REPOS:
        if kind not in printed_header:
            print(f"== {kind} ==")
            printed_header.add(kind)
        release = release_line(repo)
        head = head_line(repo)
        pin = pins.get("platform" if label == "pioarduino" else label, "")
        print(f"{label:22} {release}")
        print(f"{'':22} head {head}")
        if pin and pin != "?":
            print(f"{'':22} local pin {pin}")
            if re.fullmatch(r"[0-9a-fA-F]{40}", pin):
                # The local pin is a commit; compare it with the current head.
                head_sha = head.split(" ", 1)[0]
                note = compare(repo, pin, head_sha)
                if note:
                    print(f"{'':22} {note}")
        if kind == "m5" and label in ("M5GFX", "M5Unified"):
            print(f"{'':22} note: this port uses FastEPD, not M5GFX/M5Unified")
        if label == "bb_epaper":
            print(f"{'':22} note: PaperDIY ignores bb_epaper (FastEPD drives the panel)")
        print()


def main() -> int:
    print("PaperDIY update report (read-only)\n")
    print(f"project: {PROJECT_DIR}")
    print(f"GitHub token: {'yes' if TOKEN else 'no (unauthenticated, 60 requests/hour)'}\n")
    upstream_report()
    repos_report()
    print("Next: bump only what affects the PaperDIY path, then run `make build` and `make test`;")
    print("for panel/PM1/power changes, do the hardware smoke test in docs/paperdiy.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
