"""Host side of the PaperDIY dev remote, against a fake board on a pty.

The fake plays the firmware's part of the protocol (src/paperdiy_dev_remote.cpp):
READY/PONG/STATUS lines, a framebuffer dump that loses a row the first time,
log lines between rows, and a restart that comes back to the loop.
"""
import json
import os
from pathlib import Path
import select
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "paperdiy"))
sys.path.insert(1, str(ROOT / "scripts"))

import remote as rm  # noqa: E402
import smoke  # noqa: E402


class FakeBoard(threading.Thread):
    def __init__(self, framebuffer: bytes, lose_a_row_once: bool = True):
        super().__init__(daemon=True)
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.framebuffer = framebuffer
        self.lose_a_row = lose_a_row_once
        self.received = []
        self.held = False
        self.seq = 0
        self.url = ""
        self.leftover = ""  # half a line, as when the board's USB buffer overflowed
        self.done = threading.Event()

    def send(self, text: str) -> None:
        os.write(self.master, (text + "\n").encode())

    def run(self) -> None:
        pending = b""
        while not self.done.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                pending += os.read(self.master, 4096)
            except OSError:
                return
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = self.leftover + line.decode().strip()
                self.leftover = ""
                self.handle(text)

    def stop(self) -> None:
        self.done.set()
        self.join(1)
        os.close(self.master)
        os.close(self.slave)

    def ready(self, wake: int) -> None:
        self.seq += 1
        self.send(f"REMOTE:READY seq={self.seq} version=1.8.20-4 wake={wake}")

    def handle(self, line: str) -> None:
        self.received.append(line)
        if line == "":
            return
        if line == "CMD:HOLD":
            if self.held:
                self.send("REMOTE:OK HOLD")
            else:
                self.held = True
                self.ready(0)
                self.leftover = "CMD:"  # the rest of a flooded HOLD was lost
                self.send("REMOTE:ERR unknown CMD:HOOLD")  # a mangled one, answered late
        elif line == "CMD:PING":
            self.send(f"REMOTE:PONG version=1.8.20-4 fw=1.8.20 seq={self.seq} hold=1")
        elif line == "CMD:STATUS":
            self.send('REMOTE:STATUS version=1.8.20-4 pm1=1 battery_mv=4120 usb=1 wifi_saved=1 wifi=0 '
                      f'ssid="Home net" api_url="{self.url}" api_key=1 panel=960x540 bpp=4')
        elif line == "CMD:SHOT":
            self.shot()
        elif line in ("CMD:REFRESH", "CMD:WAKE:TIMER"):
            name = line[len("CMD:"):]
            self.send(f"REMOTE:OK {name} restarting hold={int(self.held)}")
            kind = "timer" if name == "WAKE:TIMER" else "button"
            self.send(f"[  120][I][wake_init.cpp:116] PaperDIY dev remote: simulating a {kind} wake")
            result = self.fetch_screen()
            self.send(f"[ 9900][I][bl.cpp:23]: request result - {result}")
            if self.held:
                self.ready(4 if kind == "timer" else 2)
        elif line == "CMD:FREE":
            self.held = False
            self.send("REMOTE:OK FREE")
        elif line == "CMD:SERVER":
            self.send(f'REMOTE:SERVER url="{self.url}" default="https://trmnl.app"')
        elif line.startswith("CMD:SERVER:"):
            old, self.url = self.url, line[len("CMD:SERVER:"):]
            if self.url == "-":
                self.url = ""
            self.send(f'REMOTE:OK SERVER old="{old}" new="{self.url}"')
        elif line == "CMD:SLEEP":
            self.send("REMOTE:OK SLEEP")
        elif line.startswith("CMD:"):
            self.send(f"REMOTE:ERR unknown {line}")

    def fetch_screen(self) -> str:
        """What a wake does against a test server: /api/display, then the PNG."""
        if not self.url.startswith("http://127.0.0.1:"):  # its own server: not reached from the tests
            return "HTTPS_SUCCESS"
        with urllib.request.urlopen(self.url + "/api/display", timeout=5) as answer:
            display = json.load(answer)
        with urllib.request.urlopen(display["image_url"], timeout=5) as answer:
            png = answer.read()
        rows = decode_gray_png(png)
        if rows is None:
            return "HTTPS_WRONG_IMAGE_FORMAT"
        self.framebuffer = b"".join(rows)
        self.send("[ 9000][I][display.cpp:477] keeping FastEPD's ED047TC1 gray matrix")
        return "HTTPS_SUCCESS"

    def shot(self) -> None:
        width, height, bpp = 960, 540, 4
        pitch = width * bpp // 8
        self.send(f"#shot {width} {height} {bpp} 0 {pitch * height}")
        for y in range(height):
            if y == 5 and self.lose_a_row:  # the USB link dropped it
                self.lose_a_row = False
                continue
            if y == 10:
                self.send("[ 9950][I][wifi] a log line from another task")
            self.send(f"#r {y} {self.framebuffer[y * pitch:(y + 1) * pitch].hex()}")
        self.send(f"#end {zlib.crc32(self.framebuffer) & 0xFFFFFFFF:08x}")


def decode_gray_png(png: bytes):
    """Rows of an unfiltered grayscale PNG (what the tests serve), None if it is not one."""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos, idat, header = 8, b"", None
    while pos + 8 <= len(png):
        length, tag = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, _ = header
    pitch = (width * depth + 7) // 8
    raw = zlib.decompress(idat)
    return [raw[y * (pitch + 1) + 1:(y + 1) * (pitch + 1)] for y in range(height)]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.framebuffer = b"".join(smoke.gradient_rows())
        self.board = FakeBoard(self.framebuffer)
        self.board.start()
        self.addCleanup(self.board.stop)
        self.link = rm.Link(self.board.path, echo=False)
        self.addCleanup(self.link.close)
        self.remote = rm.Remote(self.link, cycle_timeout=5)

    def test_grab_ping_and_status(self):
        self.assertTrue(self.remote.grab(5).startswith("REMOTE:READY"))
        self.assertEqual(self.remote.ping()["version"], "1.8.20-4")
        status = self.remote.status()
        self.assertEqual(status["ssid"], "Home net")
        self.assertEqual(status["api_url"], "")
        self.assertEqual(status["panel"], "960x540")

    def test_shot_asks_again_after_a_lost_row(self):
        self.remote.grab(5)
        shot = self.remote.shot()
        self.assertEqual(shot.data, self.framebuffer)
        self.assertEqual(self.board.received.count("CMD:SHOT"), 2)
        self.assertEqual(smoke.compare(shot, smoke.gradient_rows()), (0, ""))

    def test_restart_comes_back_with_the_wake_lines(self):
        self.remote.grab(5)
        lines = self.remote.cycle("refresh")
        self.assertIn("[ 9900][I][bl.cpp:23]: request result - HTTPS_SUCCESS", lines)
        self.assertEqual(self.remote.ready["wake"], "2")

    def test_server_set_and_restore(self):
        self.remote.grab(5)
        self.remote.server("http://192.168.1.42:8086")
        self.assertEqual(self.remote.server()["url"], "http://192.168.1.42:8086")
        self.remote.server("-")
        self.assertEqual(self.remote.server()["url"], "")

    def test_steps_save_a_shot_and_wait_for_log_lines(self):
        with tempfile.TemporaryDirectory() as out:
            self.remote.grab(5)
            rm.run_steps(self.remote, ["refresh", "until:5:HTTPS_SUCCESS", "shot:after"], Path(out))
            png = (Path(out) / "after.png").read_bytes()
            self.assertTrue(png.startswith(b"\x89PNG"))
            width, height, depth = struct.unpack(">IIB", png[16:25])
            self.assertEqual((width, height, depth), (960, 540, 8))

    def test_until_fails_when_the_line_never_comes(self):
        self.remote.grab(5)
        with self.assertRaises(rm.RemoteError):
            rm.run_steps(self.remote, ["ping", "until:1:never logged"], Path(tempfile.gettempdir()))


class SmokeTests(unittest.TestCase):
    """smoke.py from start to end: the fake board fetches from the real mock server."""

    def test_full_run_with_errors_passes_and_restores_the_server(self):
        board = FakeBoard(bytes(960 * 540 // 2), lose_a_row_once=True)
        board.url = "https://byos.example"
        board.start()
        self.addCleanup(board.stop)
        with tempfile.TemporaryDirectory() as out:
            code = smoke.main(["--port", board.path, "--host-ip", "127.0.0.1", "--mock-port", str(free_port()),
                               "--out", out, "--errors", "-q", "--grab-timeout", "5", "--cycle-timeout", "10"])
            report = json.loads(next(Path(out).glob("*/report.json")).read_text())
        failed = [step for step in report["steps"] if not step["ok"]]
        self.assertEqual(failed, [])
        self.assertEqual(code, 0)
        self.assertEqual(board.url, "https://byos.example")  # restored
        self.assertFalse(board.held)  # let go: the last wake ends in sleep
        self.assertIn("CMD:SERVER:https://byos.example", board.received)


class ImageTests(unittest.TestCase):
    def test_gradient_png_holds_the_framebuffer_rows(self):
        rows = smoke.gradient_rows()
        png = rm.png_bytes(960, 540, 4, rows)
        width, height, depth, color = struct.unpack(">IIBB", png[16:26])
        self.assertEqual((width, height, depth, color), (960, 540, 4, 0))
        length = struct.unpack(">I", png[33:37])[0]
        raw = zlib.decompress(png[41:41 + length])
        decoded = [raw[i * 481 + 1:(i + 1) * 481] for i in range(540)]
        self.assertEqual(decoded, rows)

    def test_gradient_levels(self):
        rows = smoke.gradient_rows()
        self.assertEqual(rows[0][0], 0x00)  # black at the top left
        self.assertEqual(rows[0][-1], 0xFF)  # white at the top right
        self.assertEqual(rows[539][0], 0xFF)  # the bottom half runs the other way
        self.assertEqual(rows[270][0], 0x0F)  # the band: black, white, black...

    def test_compare_reports_swapped_nibbles(self):
        rows = smoke.gradient_rows()
        data = bytearray(b"".join(rows))
        data[270 * 480] = 0xF0  # one byte of the band, pixels swapped
        shot = rm.Shot(960, 540, 4, 0, bytes(data))
        differ, first = smoke.compare(shot, rows)
        self.assertEqual(differ, 2)
        self.assertIn("x=0 y=270", first)

    def test_gray_rows_per_depth(self):
        self.assertEqual(rm.shot_gray_rows(rm.Shot(2, 1, 4, 0, bytes([0x0F])))[0], bytes([0, 255]))
        self.assertEqual(rm.shot_gray_rows(rm.Shot(4, 1, 2, 0, bytes([0b00011011])))[0], bytes([0, 85, 170, 255]))
        self.assertEqual(rm.shot_gray_rows(rm.Shot(8, 1, 1, 0, bytes([0x81])))[0],
                         bytes([255, 0, 0, 0, 0, 0, 0, 255]))

    def test_collector_rejects_a_bad_crc(self):
        collector = rm.ShotCollector()
        for line in ("#shot 2 1 4 0 1", "#r 0 0f", "#end 00000000"):
            collector.feed(line)
        with self.assertRaises(rm.RemoteError):
            collector.result()


if __name__ == "__main__":
    unittest.main()
