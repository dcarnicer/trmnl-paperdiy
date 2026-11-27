"""The PlatformIO core is chosen per platform release, not per checkout."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/pio_core_dir.py"
spec = importlib.util.spec_from_file_location("pio_core_dir", SCRIPT)
pio_core_dir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pio_core_dir)

PIOARDUINO = (
    "https://github.com/pioarduino/platform-espressif32/releases/download/"
    "55.03.37/platform-espressif32.zip"
)


class CoreKeyTest(unittest.TestCase):
    def test_pioarduino_release_names_the_core(self):
        self.assertEqual(pio_core_dir.core_key(PIOARDUINO), "pioarduino-55.03.37")

    def test_registry_spec_keeps_name_and_version(self):
        for spec_text in ("platformio/espressif32@6.12.0", "espressif32@6.12.0"):
            self.assertEqual(pio_core_dir.core_key(spec_text), "espressif32-6.12.0")

    def test_other_urls_do_not_share_a_core(self):
        first = pio_core_dir.core_key("https://example.org/a/platform.zip")
        second = pio_core_dir.core_key("https://example.org/b/platform.zip")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("platform-"))


class ScriptTest(unittest.TestCase):
    def run_script(self, config, exit_code=0):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        (directory / "config.json").write_text(json.dumps(config))
        pio = directory / "pio"
        pio.write_text('#!/bin/sh\ncat "$CONFIG"\nexit "$EXIT"\n')
        pio.chmod(0o755)
        env = {
            **os.environ,
            "CONFIG": str(directory / "config.json"),
            "EXIT": str(exit_code),
            "HOME": str(directory),
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--pio", str(pio), "TRMNL_X_PAPERDIY"],
            cwd=directory, env=env, capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip()), directory

    def test_environment_platform_overrides_the_env_section(self):
        core, home = self.run_script([
            ["env", [["platform", "espressif32@6.12.0"]]],
            ["env:TRMNL_X_PAPERDIY", [["platform", PIOARDUINO]]],
        ])
        self.assertEqual(core, home / ".platformio-cores" / "pioarduino-55.03.37")

    def test_environment_without_platform_uses_the_env_section(self):
        core, home = self.run_script([
            ["env", [["platform", "espressif32@6.12.0"]]],
            ["env:TRMNL_X_PAPERDIY", [["board", "esp32s3_n16r8"]]],
        ])
        self.assertEqual(core, home / ".platformio-cores" / "espressif32-6.12.0")

    def test_unreadable_config_keeps_the_checkout_core(self):
        core, directory = self.run_script([], exit_code=1)
        self.assertEqual(core.resolve(), directory / ".platformio-core")


flash_spec = importlib.util.spec_from_file_location(
    "flash_app", ROOT / "scripts/paperdiy/flash_app.py")
flash_app = importlib.util.module_from_spec(flash_spec)
flash_spec.loader.exec_module(flash_app)


class FlashAppCoreTest(unittest.TestCase):
    def test_explicit_core_wins(self):
        with patch.dict(os.environ, {"PLATFORMIO_CORE_DIR": "/explicit/core"}):
            self.assertEqual(flash_app.platformio_core_dir(), Path("/explicit/core"))

    def test_without_variable_uses_the_makefile_core(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"PLATFORMIO_CORE_DIR": ""}), \
                patch.object(flash_app.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=temporary + "\n")
            self.assertEqual(flash_app.platformio_core_dir(), Path(temporary))

    def test_without_make_uses_the_global_core(self):
        with patch.dict(os.environ, {"PLATFORMIO_CORE_DIR": ""}), \
                patch.object(flash_app.subprocess, "run", side_effect=OSError):
            self.assertEqual(flash_app.platformio_core_dir(), Path.home() / ".platformio")


if __name__ == "__main__":
    unittest.main()
