#!/usr/bin/env python3
"""Hardware smoke test of the PaperDIY over its USB cable.

Needs the TRMNL_X_PAPERDIY_dev build on the board (make flash-dev), the board
on USB, on a Wi-Fi network this Mac can be reached from, and registered with
its server (it keeps its API key; the test never pairs it with the mock).

  python3 scripts/paperdiy/smoke.py
  python3 scripts/paperdiy/smoke.py --port /dev/cu.usbmodem1101 --errors
  make smoke PORT=/dev/cu.usbmodem1101

What it does:

1. Catches the board (CMD:HOLD) and checks STATUS: PM1, USB, battery, Wi-Fi,
   API key and the 960x540 panel.
2. Starts scripts/mock_server.py serving a 960x540 4-bit grayscale PNG: 16
   bars 0-15 on the top half, 15-0 on the bottom half and a band of
   alternating black/white pixels between them. Points the board at it with
   CMD:SERVER (NVS; the API key and friendly ID stay).
3. A button wake (CMD:REFRESH): the log must show FastEPD's ED047TC1 gray
   matrix, HTTPS_SUCCESS and no FastEPD error or crash; the framebuffer must
   equal the PNG byte for byte (4-bit PNGs are copied straight into it).
4. A timer wake (CMD:WAKE:TIMER), with the same checks.
5. With --errors: a garbage image must give an error result without a crash,
   and the next wake must draw the gradient again.
6. Always: restores the board's API URL. Then, unless --keep-hold, lets it go
   (CMD:FREE) and runs one last real wake against its own server, which ends
   in normal sleep.

The serial capture is the framebuffer, not the panel: look at the panel too
(16 distinct grays, deep black on the left of the top half). The log, shots
and report.json go to .pio/paperdiy-smoke/<date-time>/.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))

import mock_server  # noqa: E402  (scripts/mock_server.py)
import remote as rm  # noqa: E402  (scripts/paperdiy/remote.py)

ROOT = HERE.parents[1]
MOCK = HERE.parent / "mock_server.py"
DEFAULT_OUT = ROOT / ".pio" / "paperdiy-smoke"

WIDTH, HEIGHT = 960, 540
BAR = WIDTH // 16  # 60 px, even, so both pixels of a byte share a bar
BAND = range(260, 280)  # alternating black/white pixels: catches swapped nibbles

WAKE_BUTTON = 2  # ESP_SLEEP_WAKEUP_EXT0
WAKE_TIMER = 4  # ESP_SLEEP_WAKEUP_TIMER
CRASH_MARKERS = ("Guru Meditation", "Backtrace:", "abort() was called", "Awake time limit")


# ---- the test image -------------------------------------------------------


def gradient_rows() -> list[bytes]:
    """Packed 4-bit rows, high nibble first, as in both the PNG and FastEPD's buffer."""
    up = bytes(((2 * j) // BAR) * 0x11 for j in range(WIDTH // 2))
    down = bytes((15 - (2 * j) // BAR) * 0x11 for j in range(WIDTH // 2))
    band = bytes([0x0F]) * (WIDTH // 2)  # even pixels black, odd pixels white
    return [band if y in BAND else (up if y < BAND.start else down) for y in range(HEIGHT)]


def compare(shot: rm.Shot, rows: list[bytes]) -> tuple[int, str]:
    """(pixels that differ, description of the first one)."""
    if (shot.width, shot.height, shot.bpp) != (WIDTH, HEIGHT, 4):
        return WIDTH * HEIGHT, f"shot is {shot.width}x{shot.height} at {shot.bpp} bpp, not 960x540 at 4"
    expected = b"".join(rows)
    if shot.data == expected:
        return 0, ""
    differ = 0
    first = ""
    for i, (got, want) in enumerate(zip(shot.data, expected)):
        if got == want:
            continue
        for shift, offset in ((4, 0), (0, 1)):
            g, w = (got >> shift) & 0xF, (want >> shift) & 0xF
            if g != w:
                differ += 1
                if not first:
                    y, x = divmod(i * 2 + offset, WIDTH)
                    first = f"first at x={x} y={y}: level {g}, expected {w}"
    return differ, first


# ---- the mock server ------------------------------------------------------


class Mock:
    """scripts/mock_server.py in a child process, its output kept and logged."""

    def __init__(self, port: int, image: Path, log_path: Path, extra: list[str] | None = None):
        self.lines: list[str] = []
        self.log = open(log_path, "a", encoding="utf-8")
        args = [sys.executable, "-u", str(MOCK), "--port", str(port), "--image-file", str(image),
                "--refresh", "900", "-q", *(extra or [])]
        # -q: one line a request, so the board's API key is never written to the log
        self.proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.lines.append(line)
            self.log.write(line + "\n")
            self.log.flush()

    def wait_listening(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any("listening on" in line for line in self.lines):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.1)
        return False

    def count(self, text: str) -> int:
        return sum(text in line for line in self.lines)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.thread.join(2)
        if self.proc.stdout:
            self.proc.stdout.close()
        self.log.close()


# ---- the run --------------------------------------------------------------


class Abort(Exception):
    pass


class Smoke:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out: Path = args.out / stamp
        self.out.mkdir(parents=True, exist_ok=True)
        self.link = rm.Link(args.port, echo=not args.quiet, log_path=self.out / "device.log")
        self.remote = rm.Remote(self.link, cycle_timeout=args.cycle_timeout)
        self.steps: list[dict] = []
        self.notes: list[str] = []
        self.failed = False
        self.status: dict[str, str] = {}
        self.original_url: str | None = None
        self.server_changed = False
        self.mock: Mock | None = None
        self.rows = gradient_rows()
        self.image = self.out / "gradient.png"
        self.image.write_bytes(rm.png_bytes(WIDTH, HEIGHT, 4, self.rows))

    # -- recording --

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append({"step": name, "ok": ok, "detail": detail})
        self.link.say(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        if not ok:
            self.failed = True
        return ok

    def require(self, name: str, ok: bool, detail: str = "") -> None:
        if not self.record(name, ok, detail):
            raise Abort(name)

    def note(self, text: str) -> None:
        self.notes.append(text)
        self.link.say(f"note: {text}")

    # -- checks --

    def check_wake(self, label: str, lines: list[str], wake: int, served_before: tuple[int, int]) -> str | None:
        text = "\n".join(lines)
        results = re.findall(r"request result - (\w+)", text)
        result = results[-1] if results else None
        self.record(f"{label}: back in the loop as wake {wake}", self.remote.ready.get("wake") == str(wake),
                    f"REMOTE:READY wake={self.remote.ready.get('wake')}")
        self.record(f"{label}: request result HTTPS_SUCCESS", result == "HTTPS_SUCCESS",
                    result or "no 'request result' line")
        self.record(f"{label}: FastEPD's ED047TC1 gray matrix", "keeping FastEPD's ED047TC1 gray matrix" in text)
        self.record(f"{label}: no FastEPD error", "FastEPD reported error" not in text)
        crash = [m for m in CRASH_MARKERS if m in text]
        self.record(f"{label}: no crash or abort", not crash, ", ".join(crash))
        assert self.mock is not None
        deadline = time.monotonic() + 3.0  # its output is read by another thread
        while True:
            displays = self.mock.count("/api/display -> 200") - served_before[0]
            images = self.mock.count("done: 200") - served_before[1]
            if (displays >= 1 and images >= 1) or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        self.record(f"{label}: the mock served /api/display and the image", displays >= 1 and images >= 1,
                    f"{displays} display, {images} image")
        if "simulating a" not in text:
            self.note(f"{label}: the boot lines before the USB came back were not seen")
        return result

    def served(self) -> tuple[int, int]:
        assert self.mock is not None
        return self.mock.count("/api/display -> 200"), self.mock.count("done: 200")

    def check_shot(self, label: str) -> None:
        shot = self.remote.shot()
        rm.write_shot_png(self.out / f"{label}.png", shot)
        differ, first = compare(shot, self.rows)
        self.record(f"{label}: framebuffer equals the gradient PNG", differ == 0,
                    f"{differ} pixels differ, {first}" if differ else "")

    def start_mock(self, extra: list[str] | None = None) -> None:
        if self.mock:
            self.mock.stop()
        self.mock = Mock(self.args.mock_port, self.image, self.out / "mock.log", extra)
        listening = self.mock.wait_listening()
        self.require("mock server listening", listening,
                     "" if listening else f"port {self.args.mock_port} (busy? use --mock-port)")

    # -- phases --

    def run(self) -> int:
        try:
            self.prepare()
            self.wakes()
        except (Abort, rm.RemoteError, TimeoutError) as exc:
            if not isinstance(exc, Abort):
                self.record("device", False, str(exc) or type(exc).__name__)
        except KeyboardInterrupt:
            self.record("run", False, "interrupted")
        finally:
            self.restore()
            if self.mock:
                self.mock.stop()
            if not self.args.keep_hold:
                self.release()
            self.report()
            self.link.close()
        return 1 if self.failed else 0

    def prepare(self) -> None:
        try:
            ready = self.remote.grab(self.args.grab_timeout)
        except rm.RemoteError as exc:
            self.require("board held over USB", False, f"{exc} Flash it with: make flash-dev PORT=...")
        self.record("board held over USB", True, ready)
        pong = self.remote.ping()
        self.record("dev remote answers", bool(pong.get("version")), f"version {pong.get('version')}")

        status = self.status = self.remote.status()
        (self.out / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        self.require("PM1 answers", status.get("pm1") == "1")
        self.record("on USB", status.get("usb") == "1")
        mv = int(status.get("battery_mv", "-1"))
        self.record("battery voltage plausible", 3000 <= mv <= 4450, f"{mv} mV, {status.get('battery_pct')} %")
        self.record("panel 960x540", status.get("panel") == f"{WIDTH}x{HEIGHT}", status.get("panel", ""))
        registered = status.get("api_key") == "1"
        self.require("registered with its server", registered,
                     "" if registered else "no API key: set the board up with its server first")
        # Wi-Fi is off by now (the wake turns it off once the image is in): ask for a saved network
        if "wifi_saved" in status:
            saved = status["wifi_saved"] == "1"
            self.require("a Wi-Fi network saved", saved, "" if saved else "set one up in the portal first")
        self.original_url = status.get("api_url", "")

        self.start_mock()
        hosts = [self.args.host_ip] if self.args.host_ip else mock_server.discover_local_ips()
        self.require("a LAN address for this Mac", bool(hosts), hosts[0] if hosts else "pass --host-ip")
        url = f"http://{hosts[0]}:{self.args.mock_port}"
        self.remote.server(url)
        self.server_changed = True
        self.require("board pointed at the mock", self.remote.server().get("url") == url, url)

    def wakes(self) -> None:
        before = self.served()
        lines = self.remote.cycle("refresh")
        self.check_wake("button wake", lines, WAKE_BUTTON, before)
        self.check_shot("button-wake")

        before = self.served()
        lines = self.remote.cycle("wake:timer")
        self.check_wake("timer wake", lines, WAKE_TIMER, before)
        self.check_shot("timer-wake")

        if self.args.errors:
            self.start_mock(["--image", "garbage:1"])
            lines = self.remote.cycle("refresh")
            text = "\n".join(lines)
            results = re.findall(r"request result - (\w+)", text)
            result = results[-1] if results else ""
            self.record("garbage image: an error result", bool(result) and result != "HTTPS_SUCCESS",
                        result or "no 'request result' line")
            crash = [m for m in CRASH_MARKERS if m in text]
            self.record("garbage image: no crash or abort", not crash, ", ".join(crash))
            self.record("garbage image: back in the loop", self.remote.ready.get("wake") == str(WAKE_BUTTON))
            before = self.served()
            lines = self.remote.cycle("refresh")
            self.check_wake("after the error", lines, WAKE_BUTTON, before)
            self.check_shot("after-error")

    def restore(self) -> None:
        if not self.server_changed:
            return
        original = self.original_url or "-"
        for attempt in range(2):
            try:
                if attempt:
                    self.remote.grab(self.args.cycle_timeout)
                self.remote.server(original)
                now = self.remote.server().get("url", "")
                self.server_changed = not self.record("API URL restored", now == (self.original_url or ""),
                                                      repr(now))
                return
            except (rm.RemoteError, TimeoutError) as exc:
                self.link.say(f"restoring the API URL: {exc}")
        self.record("API URL restored", False,
                    f"run: python3 scripts/paperdiy/remote.py server:{original}")

    def release(self) -> None:
        if self.server_changed:
            return  # keep it held: it still points at the mock
        try:
            self.remote.free()
            lines = self.remote.cycle("refresh")
        except (rm.RemoteError, TimeoutError) as exc:
            self.note(f"could not let the board go: {exc}")
            return
        results = re.findall(r"request result - (\w+)", "\n".join(lines))
        if results:
            self.note(f"last wake against the board's own server: {results[-1]}; it sleeps now")
        else:
            self.note("let the board go; its last wake was not seen to the end")

    def report(self) -> None:
        passed = sum(step["ok"] for step in self.steps)
        report = {
            "result": "FAIL" if self.failed else "PASS",
            "passed": passed,
            "steps": self.steps,
            "notes": self.notes,
            "status": self.status,
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        (self.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        self.link.say(f"{report['result']}: {passed}/{len(self.steps)} checks; files in {self.out}")
        if not self.failed:
            self.link.say("now look at the panel: 16 grays per half, deep black at the top left")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="serial port (default: $PAPERDIY_PORT or the first /dev/cu.usbmodem*)")
    parser.add_argument("--host-ip", help="this Mac's LAN address, as the board reaches it (default: detected)")
    parser.add_argument("--mock-port", type=int, default=8086, help="mock server port (8086)")
    parser.add_argument("--errors", action="store_true", help="also check a broken image and the recovery")
    parser.add_argument("--keep-hold", action="store_true",
                        help="leave the board held in the loop instead of a last real wake")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the run's files go")
    parser.add_argument("-q", "--quiet", action="store_true", help="do not echo the device log")
    parser.add_argument("--grab-timeout", type=float, default=120.0, help="seconds to wait for the board (120)")
    parser.add_argument("--cycle-timeout", type=float, default=rm.DEFAULT_CYCLE_TIMEOUT,
                        help="seconds a wake may take (180)")
    return Smoke(parser.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
