# M5Burner package

Metadata and checklist to publish the PaperDIY port to M5Burner, whose catalog
LauncherHub mirrors: once published, M5Launcher > OTA can install it. Adapted
from dcarnicer/paperdiy-reader. Generated packages go to `dist/m5burner/`.

## Build the package

```bash
pio run -e TRMNL_X_PAPERDIY
python3 scripts/paperdiy/make_m5burner_package.py --version 1.8.20-1
```

It produces `dist/m5burner/trmnl-paperdiy-latest.bin` (for the M5Burner
desktop **Publish** dialog) and `trmnl-paperdiy-<version>.zip` (for the
`m5stack/M5Stack-Firmware` repository/PR route). Both carry the full factory
image (bootloader, partition table and app at 0x0). Never package the `_dev`
build: it logs the API key over serial.

## Test before publishing

1. On a PaperDIY, erase the flash and burn the package with M5Burner.
2. Check the Wi-Fi portal, `/api/setup` and a first screen against your server.
3. Install it from M5Launcher too and check a timer wake on battery.
4. Check the package boots with an empty NVS (no Wi-Fi or API key from the
   development device).

## Publish

With your own M5Stack account, either:

1. **M5Burner desktop app**: `USER CUSTOM` → `Publish`, with name, version,
   description, device `M5Stack PaperDIY`, GitHub URL, the `.bin` and a cover.
2. **M5Stack-Firmware PR**: add this repository to `firmware-repo.list` in a
   fork of `m5stack/M5Stack-Firmware`, with `m5burner.json` and `firmware/`.

Confirm the exact device name M5Burner shows before publishing, and update
`firmware_category.device` in `m5burner.json` if it differs. A cover image is
still to do (`m5burner/cover.png`).
