# Build helpers for the PaperDIY port.
#
# The pioarduino platform pins exact toolchain/framework packages. Keeping them
# in a project-local core directory stops other PlatformIO projects (which share
# ~/.platformio) from replacing them with a different version.
PIO       ?= $(if $(wildcard $(CURDIR)/.platformio-venv/bin/pio),$(CURDIR)/.platformio-venv/bin/pio,pio)
PYTHON    ?= python3.14
ENV       ?= TRMNL_X_PAPERDIY
CORE_DIR  ?= $(CURDIR)/.platformio-core
VENV      ?= $(CURDIR)/.platformio-venv
PORT      ?=

PIO_RUN = PLATFORMIO_CORE_DIR="$(CORE_DIR)" $(PIO) run -e $(ENV)
PORT_ARG = $(if $(PORT),--port "$(PORT)",)

.PHONY: venv build test check-updates flash-app factory monitor clean

# PlatformIO Core 6.2.0 moves SCons to 4.11.1 and fights pioarduino's 4.8.1
# package; 6.1.19 is the version the release workflow uses.
venv:
	$(PYTHON) -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip install --quiet "platformio==6.1.19"
	"$(VENV)/bin/pio" --version

build:
	$(PIO_RUN)

test:
	PLATFORMIO_CORE_DIR="$(CORE_DIR)" $(PIO) test -e native

# Read-only report of upstream, M5Stack, bitbank2 and PlatformIO updates.
check-updates:
	python3 scripts/paperdiy/check_upstreams.py

# App-only update: keeps NVS (Wi-Fi, API URL), LittleFS and M5Launcher.
flash-app: build
	PLATFORMIO_CORE_DIR="$(CORE_DIR)" python3 scripts/paperdiy/flash_app.py $(PORT_ARG)

# First install or recovery: bootloader + partition table + app at 0x0.
factory: build
	@test -n "$(PORT)" || (echo "Usage: make factory PORT=/dev/cu.usbmodemXXXX" && exit 1)
	PLATFORMIO_CORE_DIR="$(CORE_DIR)" $(PIO) pkg exec -- esptool --chip esp32s3 --port "$(PORT)" --baud 921600 \
		write_flash 0x0 "$(CURDIR)/.pio/build/$(ENV)/trmnl-paperdiy-factory.bin"

monitor:
	PLATFORMIO_CORE_DIR="$(CORE_DIR)" $(PIO) device monitor -e $(ENV)

clean:
	$(PIO_RUN) -t clean
