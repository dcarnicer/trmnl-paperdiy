#!/usr/bin/env python3
"""Drive a PaperDIY that runs the TRMNL_X_PAPERDIY_dev build over its USB cable.

The dev build listens on the serial port at the end of each wake; a "CMD:" line
holds it awake in a command loop (src/paperdiy_dev_remote.cpp). This tool
catches the board, runs the steps given on the command line or in scenario
files, prints the device log as "| ..." and its own lines as "> ...".

  python3 scripts/paperdiy/remote.py status shot:home
  python3 scripts/paperdiy/remote.py refresh until:"request result" shot:after
  python3 scripts/paperdiy/remote.py server:http://192.168.1.42:8080 refresh
  python3 scripts/paperdiy/remote.py -f scenario.txt
  python3 scripts/paperdiy/remote.py sleep            # let it sleep again

Steps:

  ping                 version and session counter
  status               battery, power, Wi-Fi, server, panel, memory
  shot[:NAME]          save the framebuffer as NAME.png (shot-001.png, ...)
  refresh              restart as if A had been pressed (= button:short)
  button:short|double|long
                       restart as a button wake with that press; long opens the
                       settings portal, which blocks until it closes (15 min at most)
  wake:timer           restart as a timer wake (no logo, quiet retries)
  restart              restart as a cold boot (the power button's click)
  server[:URL|:-]      show the API URL, set it, or restore the default. Keeps
                       the API key, unlike the portal
  hold                 keep the board in the loop
  free                 the next wake sleeps normally
  sleep                leave the loop: the board sleeps now
  wait:S               pause S seconds, printing the log
  until:[S:]TEXT       wait until a log line since the last command contains TEXT
                       (S seconds, 60 by default)
  raw:LINE             send LINE as it is (raw:CMD:HELP)

The port is --port, else $PAPERDIY_PORT, else the first /dev/cu.usbmodem*.
Shots go to .pio/paperdiy-remote/ unless -o says otherwise. Never leave the dev
build on a board you do not control: anything on its USB cable can press A and
change its server.
"""
from __future__ import annotations

import argparse
import glob
import os
import select
import shlex
import struct
import sys
import termios
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / ".pio" / "paperdiy-remote"
DEFAULT_CYCLE_TIMEOUT = 180.0  # a wake: Wi-Fi, /api/display, download, refresh
PORTAL_TIMEOUT = 16 * 60.0  # button:long opens the portal, which closes after 15 min

# Commands that restart the board, and the name it answers with
RESTARTS = {
    "refresh": "REFRESH",
    "button:short": "BUTTON:SHORT",
    "button:double": "BUTTON:DOUBLE",
    "button:long": "BUTTON:LONG",
    "wake:timer": "WAKE:TIMER",
    "restart": "RESTART",
}


class RemoteError(RuntimeError):
    pass


def find_port(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("PAPERDIY_PORT")
    if env:
        return env
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*"):
        candidates = sorted(glob.glob(pattern))
        if candidates:
            return candidates[0]
    return None


# ---- the serial link ------------------------------------------------------


class Link:
    """Line-based serial link that survives the board restarting.

    The ESP32-S3's USB Serial/JTAG port can vanish while the chip restarts or
    sleeps; reads and writes then drop the file descriptor and the next poll
    reopens the port when it comes back.
    """

    def __init__(self, port: str | None = None, echo: bool = True, log_path: Path | None = None):
        self.port_arg = port
        self.port: str | None = None
        self.fd: int | None = None
        self.buffer = bytearray()
        self.lines: list[str] = []
        self.echo = echo
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        self.t0 = time.monotonic()
        self.shot_rows = 0

    # -- output --

    def say(self, text: str) -> None:
        print(f"> {text}", flush=True)
        self._log(">", text)

    def _log(self, kind: str, text: str) -> None:
        if self.log:
            self.log.write(f"{time.monotonic() - self.t0:9.3f} {kind} {text}\n")
            self.log.flush()

    # -- port --

    def _open(self, port: str) -> int:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag: raw
        attrs[1] = 0  # oflag: raw
        # No HUPCL: dropping DTR/RTS on close could reset the chip
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0  # lflag: raw, no echo
        attrs[4] = attrs[5] = termios.B115200  # the USB Serial/JTAG ignores it
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        return fd

    def connect(self) -> bool:
        """Opens the port if it is there. True when open."""
        if self.fd is not None:
            return True
        port = find_port(self.port_arg)
        if not port or not os.path.exists(port):
            return False
        try:
            self.fd = self._open(port)
        except OSError:
            return False
        if self.port is not None:
            self.say(f"port back: {port}")
        self.port = port
        self.buffer.clear()
        return True

    def drop(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            self.say("port gone (the board restarted or is asleep)")

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.log:
            self.log.close()
            self.log = None

    # -- I/O --

    def write_line(self, text: str) -> bool:
        if not self.connect():
            return False
        data = text.encode() + b"\n"
        try:
            view = memoryview(data)
            deadline = time.monotonic() + 1.0
            while view:
                _, ready, _ = select.select([], [self.fd], [], 0.2)
                if ready:
                    view = view[os.write(self.fd, view):]
                elif time.monotonic() > deadline:
                    return False  # the board is not reading yet (still booting)
        except BlockingIOError:
            return False
        except OSError:
            self.drop()
            return False
        self._log("<", text)
        return True

    def poll(self, timeout: float) -> list[str]:
        """Reads for up to `timeout` seconds; returns the new complete lines."""
        deadline = time.monotonic() + timeout
        new: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if self.fd is None and not self.connect():
                if remaining <= 0:
                    return new
                time.sleep(min(0.2, remaining))
                continue
            try:
                ready, _, _ = select.select([self.fd], [], [], max(0.0, min(0.2, remaining)))
                if ready:
                    data = os.read(self.fd, 65536)
                    if not data:  # readable with nothing to read: the device went away
                        self.drop()
                        continue
                    self.buffer.extend(data)
            except BlockingIOError:
                pass
            except OSError:
                self.drop()
                continue
            while True:
                index = self.buffer.find(b"\n")
                if index < 0:
                    break
                raw = bytes(self.buffer[:index])
                del self.buffer[: index + 1]
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                self._take(line)
                new.append(line)
            if new or remaining <= 0:
                return new

    def _take(self, line: str) -> None:
        self.lines.append(line)
        if line.startswith("#r "):  # framebuffer rows: counted, not printed
            self.shot_rows += 1
            return
        if self.echo:
            print(f"| {line}", flush=True)
        self._log("|", line)

    def wait_for(self, predicate: Callable[[str], bool], timeout: float, since: int | None = None) -> str:
        """Feeds every line from `since` (default: from now) to `predicate`, in
        order, until it returns True; returns that line."""
        index = len(self.lines) if since is None else since
        deadline = time.monotonic() + timeout
        while True:
            while index < len(self.lines):
                line = self.lines[index]
                index += 1
                if predicate(line):
                    return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.poll(min(0.5, remaining))


# ---- framebuffer dumps ----------------------------------------------------


@dataclass
class Shot:
    width: int
    height: int
    bpp: int
    rotation: int
    data: bytes  # packed rows as in the FastEPD buffer

    @property
    def pitch(self) -> int:
        return (self.width * self.bpp + 7) // 8


class ShotCollector:
    """Collects one "#shot / #r / #end" dump from the device lines."""

    def __init__(self) -> None:
        self.header: tuple[int, int, int, int, int] | None = None
        self.rows: dict[int, bytes] = {}
        self.crc: int | None = None
        self.error: str | None = None
        self.bad_rows = 0

    def feed(self, line: str) -> bool:
        """True once the dump is over (complete or not)."""
        if line.startswith("REMOTE:ERR SHOT"):
            self.error = line
            return True
        if line.startswith("#shot "):
            try:
                width, height, bpp, rotation, size = (int(v) for v in line.split()[1:6])
            except ValueError:
                self.error = f"bad header: {line!r}"
                return True
            self.header = (width, height, bpp, rotation, size)
            self.rows = {}
            return False
        if self.header is None:
            return False
        if line.startswith("#r "):
            parts = line.split(" ", 2)
            pitch = (self.header[0] * self.header[2] + 7) // 8
            try:
                y = int(parts[1])
                data = bytes.fromhex(parts[2])
            except (IndexError, ValueError):
                self.bad_rows += 1
                return False
            if len(data) == pitch and 0 <= y < self.header[1]:
                self.rows[y] = data
            else:
                self.bad_rows += 1
            return False
        if line.startswith("#end "):
            try:
                self.crc = int(line.split()[1], 16)
            except (IndexError, ValueError):
                self.error = f"bad end: {line!r}"
            return True
        return False

    def result(self) -> Shot:
        if self.error:
            raise RemoteError(self.error)
        if self.header is None or self.crc is None:
            raise RemoteError("no complete dump")
        width, height, bpp, rotation, size = self.header
        missing = [y for y in range(height) if y not in self.rows]
        if missing:
            raise RemoteError(f"{len(missing)} rows missing or cut (first {missing[0]})")
        data = b"".join(self.rows[y] for y in range(height))
        if len(data) != size:
            raise RemoteError(f"{len(data)} bytes, header said {size}")
        if zlib.crc32(data) & 0xFFFFFFFF != self.crc:
            raise RemoteError("CRC mismatch")
        return Shot(width, height, bpp, rotation, data)


_GRAY4_HI = bytes((b >> 4) * 17 for b in range(256))
_GRAY4_LO = bytes((b & 0x0F) * 17 for b in range(256))
_GRAY2 = [bytes(((b >> s) & 3) * 85 for s in (6, 4, 2, 0)) for b in range(256)]
_GRAY1 = [bytes(255 if b & (0x80 >> i) else 0 for i in range(8)) for b in range(256)]


def shot_gray_rows(shot: Shot) -> list[bytes]:
    """8-bit gray rows (0 black, 255 white), as the panel shows the levels."""
    rows = []
    for y in range(shot.height):
        packed = shot.data[y * shot.pitch:(y + 1) * shot.pitch]
        if shot.bpp == 4:
            row = bytearray(len(packed) * 2)
            row[0::2] = packed.translate(_GRAY4_HI)
            row[1::2] = packed.translate(_GRAY4_LO)
        elif shot.bpp == 2:
            row = bytearray(b"".join(_GRAY2[b] for b in packed))
        elif shot.bpp == 1:
            row = bytearray(b"".join(_GRAY1[b] for b in packed))
        else:
            raise RemoteError(f"unsupported {shot.bpp} bpp")
        rows.append(bytes(row[: shot.width]))
    return rows


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(width: int, height: int, bit_depth: int, rows: list[bytes]) -> bytes:
    """A grayscale PNG (color type 0) from packed rows of `bit_depth` bits."""
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, 0, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw, 9))
            + _png_chunk(b"IEND", b""))


def write_shot_png(path: Path, shot: Shot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(shot.width, shot.height, 8, shot_gray_rows(shot)))


def parse_fields(line: str) -> dict[str, str]:
    """"REMOTE:STATUS a=1 ssid="My net"" -> {"a": "1", "ssid": "My net"}."""
    fields = {}
    for token in shlex.split(line)[1:]:
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


# ---- the remote -----------------------------------------------------------


class Remote:
    def __init__(self, link: Link, cycle_timeout: float = DEFAULT_CYCLE_TIMEOUT):
        self.link = link
        self.cycle_timeout = cycle_timeout
        self.ready: dict[str, str] = {}
        self.last_mark = 0  # where `until:` starts looking: the last command's first line

    def say(self, text: str) -> None:
        self.link.say(text)

    def grab(self, timeout: float = 120.0) -> str:
        """Catches the board: sends CMD:HOLD until it answers from its loop."""
        deadline = time.monotonic() + timeout
        last_send = 0.0
        hinted = False
        since = len(self.link.lines)
        while time.monotonic() < deadline:
            if not self.link.connect():
                if not hinted:
                    self.say("waiting for the board on USB; if it is asleep, press A")
                    hinted = True
                time.sleep(0.3)
                continue
            if time.monotonic() - last_send >= 1.0:
                self.link.write_line("CMD:HOLD")
                last_send = time.monotonic()
            self.link.poll(0.3)
            for line in self.link.lines[since:]:
                if line.startswith("REMOTE:READY") or line == "REMOTE:OK HOLD":
                    if line.startswith("REMOTE:READY"):
                        self.ready = parse_fields(line)
                    self._flush_partial_line()
                    return line
        raise RemoteError("no answer to CMD:HOLD: is the TRMNL_X_PAPERDIY_dev build on the board, "
                          "on USB (it never holds on battery)?")

    def _flush_partial_line(self) -> None:
        """Ends whatever is left in the board's line buffer.

        The CMD:HOLD lines sent while the board is busy overflow its 256-byte USB
        buffer and lose bytes, which can leave half a line ("CMD:") waiting for
        the next command. A bare newline turns it into a line of its own, which
        the board rejects as unknown; the answers are drained here."""
        self.link.write_line("")
        self.link.poll(0.5)

    @staticmethod
    def _is_error_for(answer: str, line: str) -> bool:
        """A REMOTE:ERR for this command, not for the leftovers of an earlier one."""
        if not answer.startswith("REMOTE:ERR"):
            return False
        name = line[len("CMD:"):].split(":", 1)[0]
        return answer == f"REMOTE:ERR unknown {line}" or answer.startswith(f"REMOTE:ERR {name}")

    def command(self, line: str, expect: str, timeout: float = 15.0) -> str:
        since = len(self.link.lines)
        if not self.link.write_line(line):
            raise RemoteError(f"could not send {line} (port gone?)")
        try:
            answer = self.link.wait_for(lambda l: l.startswith(expect) or self._is_error_for(l, line),
                                        timeout, since)
        except TimeoutError:
            raise RemoteError(f"no {expect} after {line} in {timeout:.0f} s") from None
        if answer.startswith("REMOTE:ERR"):
            raise RemoteError(answer)
        return answer

    def ping(self) -> dict[str, str]:
        return parse_fields(self.command("CMD:PING", "REMOTE:PONG"))

    def status(self) -> dict[str, str]:
        return parse_fields(self.command("CMD:STATUS", "REMOTE:STATUS"))

    def server(self, url: str | None = None) -> dict[str, str]:
        if url is None:
            return parse_fields(self.command("CMD:SERVER", "REMOTE:SERVER"))
        return parse_fields(self.command(f"CMD:SERVER:{url}", "REMOTE:OK SERVER"))

    def hold(self) -> None:
        self.command("CMD:HOLD", "REMOTE:OK HOLD")

    def free(self) -> None:
        self.command("CMD:FREE", "REMOTE:OK FREE")

    def sleep(self) -> None:
        self.command("CMD:SLEEP", "REMOTE:OK SLEEP")

    def shot(self, retries: int = 3) -> Shot:
        problem = ""
        for attempt in range(retries):
            collector = ShotCollector()
            since = len(self.link.lines)
            if not self.link.write_line("CMD:SHOT"):
                raise RemoteError("could not send CMD:SHOT (port gone?)")
            try:
                self.link.wait_for(collector.feed, 60.0, since)
                return collector.result()
            except TimeoutError:
                problem = "the dump never ended"
            except RemoteError as exc:
                if collector.error:
                    raise
                problem = str(exc)
            self.say(f"shot incomplete ({problem}), asking again")
        raise RemoteError(f"no complete shot after {retries} tries: {problem}")

    def cycle(self, step: str, timeout: float | None = None) -> list[str]:
        """Restarts the board as `step` simulates and waits for the end of that
        wake. Returns the device lines of the wake."""
        name = RESTARTS[step]
        if timeout is None:
            timeout = PORTAL_TIMEOUT if step == "button:long" else self.cycle_timeout
        since = len(self.link.lines)
        answer = self.command(f"CMD:{name}", f"REMOTE:OK {name} restarting")
        # Search from the command on: the answer and the wake's lines can arrive in one read
        if "hold=1" not in answer:
            # Not held: the wake ends in sleep. Wait for it to reach the server.
            try:
                self.link.wait_for(lambda l: "request result" in l or "go to sleep" in l, timeout, since)
            except TimeoutError:
                self.say("the wake did not report a request result (it may have slept already)")
            return self.link.lines[since:]
        try:
            line = self.link.wait_for(lambda l: l.startswith("REMOTE:READY"), timeout, since)
        except TimeoutError:
            raise RemoteError(f"the board did not come back to the loop within {timeout:.0f} s") from None
        self.ready = parse_fields(line)
        return self.link.lines[since:]


# ---- steps ----------------------------------------------------------------


def run_steps(remote: Remote, steps: list[str], out_dir: Path, raw: bool = False) -> None:
    shots = 0
    for step in steps:
        name, _, arg = step.partition(":")
        remote.say(f"step {step}")
        mark = len(remote.link.lines)
        if step in RESTARTS:
            remote.cycle(step)
        elif step == "ping":
            remote.say(" ".join(f"{k}={v}" for k, v in remote.ping().items()))
        elif step == "status":
            for key, value in remote.status().items():
                remote.say(f"  {key} = {value}")
        elif name == "shot":
            shots += 1
            shot = remote.shot()
            path = out_dir / f"{arg or f'shot-{shots:03d}'}.png"
            write_shot_png(path, shot)
            if raw:
                path.with_suffix(".bin").write_bytes(shot.data)
            remote.say(f"saved {path} ({shot.width}x{shot.height}, {shot.bpp} bpp)")
        elif name == "server":
            fields = remote.server(arg or None)
            remote.say(" ".join(f"{k}={v!r}" for k, v in fields.items()))
        elif step == "hold":
            remote.hold()
        elif step == "free":
            remote.free()
        elif step == "sleep":
            remote.sleep()
        elif name == "wait":
            deadline = time.monotonic() + float(arg or 1)
            while time.monotonic() < deadline:
                remote.link.poll(deadline - time.monotonic())
        elif name == "until":
            seconds, sep, text = arg.partition(":")
            try:
                timeout = float(seconds) if sep else 60.0
            except ValueError:
                timeout, text = 60.0, arg
            if not sep:
                text = arg
            try:
                remote.link.wait_for(lambda l, t=text: t in l, timeout, since=remote.last_mark)
            except TimeoutError:
                raise RemoteError(f"no log line with {text!r} within {timeout:.0f} s") from None
        elif name == "raw":
            remote.link.write_line(arg)
            remote.link.poll(1.0)
        else:
            raise RemoteError(f"unknown step {step!r} (see --help)")
        if name not in ("until", "wait"):
            remote.last_mark = mark


def read_scenario(path: Path) -> list[str]:
    return shlex.split(path.read_text(encoding="utf-8"), comments=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("steps", nargs="*", help="steps to run (see above)")
    parser.add_argument("-f", "--file", action="append", type=Path, default=[], help="scenario file (repeatable)")
    parser.add_argument("--port", help="serial port (default: $PAPERDIY_PORT or the first /dev/cu.usbmodem*)")
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="where shots go")
    parser.add_argument("--raw", action="store_true", help="also save each shot's packed framebuffer (.bin)")
    parser.add_argument("--log", type=Path, help="append the device log and the steps, with times, to this file")
    parser.add_argument("-q", "--quiet", action="store_true", help="do not echo the device log")
    parser.add_argument("--grab-timeout", type=float, default=120.0, help="seconds to wait for the board (120)")
    parser.add_argument("--cycle-timeout", type=float, default=DEFAULT_CYCLE_TIMEOUT,
                        help="seconds a restart may take to come back (180)")
    args = parser.parse_args(argv)

    steps = [step for path in args.file for step in read_scenario(path)] + args.steps
    if not steps:
        steps = ["ping"]

    link = Link(args.port, echo=not args.quiet, log_path=args.log)
    remote = Remote(link, cycle_timeout=args.cycle_timeout)
    try:
        ready = remote.grab(args.grab_timeout)
        remote.say(f"holding the board ({ready})")
        remote.last_mark = len(link.lines)
        run_steps(remote, steps, args.out, raw=args.raw)
        return 0
    except RemoteError as exc:
        link.say(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        link.say("interrupted; the board stays held (send `sleep` to let it go)")
        return 130
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
