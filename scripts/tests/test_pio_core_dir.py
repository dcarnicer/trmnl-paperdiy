"""The PlatformIO core is chosen per pinned platform release, not per checkout."""

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
PROJECT = ROOT
spec = importlib.util.spec_from_file_location("pio_core_dir", SCRIPT)
pio_core_dir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pio_core_dir)

ENV = "TRMNL_X_PAPERDIY"
PIOARDUINO = (
    "https://github.com/pioarduino/platform-espressif32/releases/download/"
    "55.03.37/platform-espressif32.zip"
)


class CoreKeyTest(unittest.TestCase):
    def test_pioarduino_release_names_the_core(self):
        self.assertEqual(pio_core_dir.core_key(PIOARDUINO), "pioarduino-55.03.37")

    def test_registry_range_and_exact_pin_do_not_share_a_core(self):
        exact = pio_core_dir.core_key("platformio/espressif32@6.12.0")
        ranged = pio_core_dir.core_key("platformio/espressif32@^6.12.0")
        self.assertTrue(exact.startswith("espressif32-6.12.0-"))
        self.assertNotEqual(exact, ranged)

    def test_other_urls_and_paths_get_distinct_non_empty_keys(self):
        first = pio_core_dir.core_key("https://example.org/a/platform.zip")
        second = pio_core_dir.core_key("https://example.org/b/platform.zip")
        self.assertNotEqual(first, second)
        self.assertTrue(pio_core_dir.core_key("/local/platform/").startswith("platform-"))


class ScriptTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.pio = self.directory / "pio"
        self.pio.write_text('#!/bin/sh\ncat "$CONFIG"\nexit "${EXIT:-0}"\n')
        self.pio.chmod(0o755)

    def run_script(self, config, pio=None, extra_env=None):
        (self.directory / "config.json").write_text(json.dumps(config))
        env = {**os.environ, "CONFIG": str(self.directory / "config.json"),
               "HOME": str(self.directory), **(extra_env or {})}
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--pio", pio or str(self.pio), ENV],
            cwd=self.directory, env=env, capture_output=True, text=True,
        )

    def test_pinned_release_shares_a_core_under_home(self):
        result = self.run_script([[f"env:{ENV}", [["platform", PIOARDUINO]]]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()),
                         self.directory / ".platformio-cores" / "pioarduino-55.03.37")

    def test_options_that_rebuild_the_framework_keep_the_checkout_core(self):
        for option in pio_core_dir.CHECKOUT_CORE_OPTIONS:
            with self.subTest(option=option):
                result = self.run_script([[f"env:{ENV}", [
                    ["platform", PIOARDUINO], [option, ["CONFIG_X=y"]]]]])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(result.stdout.strip()), PROJECT / ".platformio-core")

    def test_board_manifest_custom_sdkconfig_keeps_the_checkout_core(self):
        boards = self.directory / "boards"
        boards.mkdir()
        (boards / "tuned.json").write_text(json.dumps({"espidf": {"custom_sdkconfig": ["X=y"]}}))
        result = self.run_script([
            ["platformio", [["boards_dir", str(boards)]]],
            [f"env:{ENV}", [["platform", PIOARDUINO], ["board", "tuned"]]],
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), PROJECT / ".platformio-core")

    def test_empty_custom_sdkconfig_still_keeps_the_checkout_core(self):
        result = self.run_script([[f"env:{ENV}", [
            ["platform", PIOARDUINO], ["custom_sdkconfig", ""]]]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), PROJECT / ".platformio-core")

    def test_board_espidf_override_keeps_the_checkout_core(self):
        result = self.run_script([[f"env:{ENV}", [
            ["platform", PIOARDUINO], ["board_espidf.custom_sdkconfig", ["X=y"]]]]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), PROJECT / ".platformio-core")

    def test_multi_word_pio_command_works(self):
        result = self.run_script([[f"env:{ENV}", [["platform", PIOARDUINO]]]],
                                 pio=f"sh {self.pio}")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_environment_fails_without_a_path(self):
        result = self.run_script([["env:other", [["platform", PIOARDUINO]]]])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(f"[env:{ENV}]", result.stderr)

    def test_failing_pio_fails_without_a_path(self):
        result = self.run_script([], extra_env={"EXIT": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")


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
