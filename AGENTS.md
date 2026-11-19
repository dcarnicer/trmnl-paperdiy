# PaperDIY maintenance

This repo is the community port of the TRMNL firmware to the M5Stack PaperDIY
(K159). Only changes that reach the PaperDIY path matter: `PARALLEL_EPD`,
FastEPD, the ED047TC1 panel, the M5PM1, the portal and image decoding.

## Trigger: dependency and upstream updates

When the user asks to update dependencies, pull upstream news, review M5Stack
components or check FastEPD/decoder/platform updates — for example
"actualizar dependencias", "bajar novedades de upstream", "componentes de M5",
"repos oficiales", "OTA" or "nuevas versiones" — run:

```bash
make check-updates
```

The target runs `scripts/paperdiy/check_upstreams.py` and prints a read-only
report. Do not bump anything from the report without checking the relevant
source first, and do not turn the trigger into an automatic update: the report
is a starting point, not a merge.

## What to look at

| Source | Why it matters | Where |
|---|---|---|
| `usetrmnl/trmnl-firmware` | Upstream firmware. Merge only commits that affect the PaperDIY path; skip unrelated board changes. | `upstream/main`, `docs/paperdiy.md` |
| `m5stack/M5PM1` | PM1 register map, timer, LVP and shutdown. | `src/paperdiy_board.cpp` |
| `m5stack/M5PaperDIY-UserDemo` | Official board/power/panel reference. | `src/paperdiy_board.cpp`, `docs/paperdiy.md` |
| `bmorcelli/Launcher` | OTA/partition layout, NVS keys and boot settings. | `src/paperdiy_board.cpp`, `docs/paperdiy.md` |
| `bitbank2/FastEPD` | Panel init, parallel bus and the gray matrix that drives the ED047TC1. | pinned in `platformio.ini`; `src/display.cpp` |
| `bitbank2/bb_epaper` | Not used by the PaperDIY (`lib_ignore = bb_epaper`); only relevant to the OG/other boards. | `platformio.ini` |
| `bitbank2/PNGdec` / `JPEGDEC` | Image decoding and security/boundary fixes. | `platformio.ini`, `src/png_render.cpp`, `src/jpeg_render.cpp` |
| `pioarduino/platform-espressif32` | Toolchain/framework; a platform bump changes app size and `sdkconfig`. | `platformio.ini`; keep PlatformIO Core 6.1.19 (`make venv`) |
| `dcarnicer/paperdiy-reader` | Sibling port; PM1, battery curve and board validation. | `src/paperdiy_board.cpp`, `src/paperdiy_battery.cpp` |
| `m5stack/M5GFX` / `M5Unified` | Not used by this port (FastEPD drives the panel). Ignore unless a shared component changes. | — |

## Applying an update

1. Prefer a focused pin bump or a cherry-picked commit; never bump a whole
   dependency set "because it is newer".
2. Keep the upstream platform/toolchain unless the change is required for the
   PaperDIY; a platform bump changes `sdkconfig` and the Launcher slot margin.
3. Run:

   ```bash
   make build
   make test
   ```

4. If the change touches the panel, PM1, power, sleep or image rendering, do a
   hardware smoke test: flash the `_dev` factory image, confirm
   `keeping FastEPD's ED047TC1 gray matrix` in the serial log and display a
   4 bpp gradient (`scripts/mock_server.py`).
5. Bump `PAPERDIY_PORT_REVISION` in `include/paperdiy_ota.h` and publish a
   release tag only after the hardware test. The GitHub release workflow builds
   `trmnl-paperdiy-latest.bin`, the app-only image and `latest.json`; M5Burner
   publication is manual (the package generator is in
   `scripts/paperdiy/make_m5burner_package.py`).

## Build environment

Use `make venv` once and then `make build`, `make test` and
`make flash-app PORT=...`. PlatformIO Core 6.1.19 is required: 6.2.0 ships
SCons 4.11.1 and breaks pioarduino's 4.8.1 package with
`No module named 'SCons.Tool.FortranCommon'`.

Do not commit `.platformio-core/`, `.platformio-venv/`, `.pio/` or `dist/`.
