Import("env")
import shutil
import subprocess
from pathlib import Path

# M5Stack PaperDIY: one image to install at 0x0 with esptool, M5Burner or M5Launcher
# (Launcher reads the partition table inside it and copies the app). Unlike the
# TRMNL X, no prebuilt LittleFS image is needed: the firmware formats an empty one.


def post_build(source, target, env):
    build_dir = Path(env.subst("$BUILD_DIR"))
    output = build_dir / "trmnl-paperdiy-factory.bin"

    subprocess.run([
        # The build's Python: pioarduino installs esptool's dependencies only in its penv.
        env.subst("$PYTHONEXE"), str(Path(env.PioPlatform().get_package_dir("tool-esptoolpy")) / "esptool.py"),
        "--chip", "ESP32S3",
        "merge_bin",
        "-o", str(output),
        "--flash_mode", "dio",
        "--flash_freq", "80m",
        "--flash_size", "16MB",
        "0x0000", str(build_dir / "bootloader.bin"),
        "0x8000", str(build_dir / "partitions.bin"),
        "0x10000", str(build_dir / "firmware.bin"),
    ], check=True)
    print(f"PaperDIY factory image: {output}")

    # Keep the generic name too, so tooling that expects merged_firmware.bin works
    shutil.copyfile(output, build_dir / "merged_firmware.bin")


# On "buildprog", which always runs: a firmware.bin taken from the build cache skips its own post actions
env.AddPostAction("buildprog", post_build)
env.Depends("upload", "buildprog")
