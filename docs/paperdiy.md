# TRMNL en la M5Stack PaperDIY

Port comunitario del firmware de TRMNL para la **M5Stack PaperDIY** (K159:
ESP32-S3-WROOM-1-N16R8 y panel ED047TC1 de 4,7", 960×540, 16 grises). Parte
del entorno `TRMNL_X_PAPERS3`: la PaperDIY conecta el panel con los mismos
pines que la PaperS3, así que FastEPD lo maneja con `BB_PANEL_M5PAPERS3`. Lo
que cambia es el PMIC M5PM1.

## Qué cambia respecto a la PaperS3

| | PaperS3 | PaperDIY (este port) |
|---|---|---|
| Panel | FastEPD `BB_PANEL_M5PAPERS3` | Igual, y además el GPIO2 del M5PM1 (raíl EPD) se enciende antes del panel y se apaga al dormir |
| Batería | ADC en GPIO3 | M5PM1 (`VBAT`, I²C 41/42, 0x6E). En la PaperDIY, GPIO3 es el botón B |
| Carga y USB | No se leen | `CHG_STAT` (GPIO3 del PM1) y `PWR_SRC` del PM1 |
| Botón TRMNL | Ninguno | A (GPIO4). Despierta del deep sleep (con USB); con batería, el que despierta es el de encendido |
| Alimentación | — | `CHG_EN` y DCDC de 5 V activados en cada arranque; corte por batería baja (LVP del PM1) a 3,0 V |
| Sueño | Deep sleep del ESP32 | Con batería, apagado completo por el PM1 y encendido con su temporizador; con USB, deep sleep |
| OTA | El servidor la ofrece | Desde las releases de GitHub del port, solo sin Launcher; la del servidor se ignora |
| Modelo enviado | `m5_papers3` | `Model: m5_papers3` (`API_DEVICE_MODEL`) y `Model-Id: m5_paper_s3` (`API_DEVICE_MODEL_ID`); en la lista interna es `m5_paperdiy` |

La OTA del servidor se ignora (`DISABLE_FIRMWARE_UPDATE`): al ver el modelo
de la PaperS3, mandaría el firmware de la PaperS3. En su lugar el firmware se
actualiza desde las releases de GitHub del port (ver [OTA](#ota)).

El código propio de la placa está en `include/paperdiy_board.h`,
`src/paperdiy_board.cpp` y `src/paperdiy_sleep_state.cpp`. Los cambios en el
código común van tras `#ifdef BOARD_TRMNL_X_PAPERDIY` (y la cabecera
`Model-Id`, tras `#ifdef API_DEVICE_MODEL_ID`).

### Sueño con el PM1

Con batería (sin USB), el firmware no usa el deep sleep del ESP32: programa el
temporizador del M5PM1 (`TIM_CNT` 0x38–0x3B, `TIM_CFG` 0x3C con acción
POWERON, `TIM_KEY` 0xA5) y le pide que apague todo (`SYS_CMD` 0xA1). Al vencer
el temporizador el PM1 enciende la placa, que arranca en frío:

- `WAKE_SRC` (0x05) del PM1 dice que lo encendió el temporizador, y el firmware
  lo trata como un despertar por tiempo (sin logo, `Update-Source: timer`). El
  servidor ve `Reset-Reason: ESP_RST_POWERON`.
- La RAM RTC se pierde, así que el estado `RTC_DATA_ATTR` (imagen en pantalla,
  contador de refrescos parciales, estadísticas) va a NVS (`pd_sleep`) justo
  antes del apagado y vuelve al arrancar.
- Como no hay hora RTC, cada despertar sincroniza por NTP (unos 2 s más).
- El botón A no puede despertarlo (el ESP32 está sin corriente): para refrescar
  a mano, un clic en el de encendido.
- Si el PM1 no apaga (por ejemplo, porque se ha enchufado el USB), sigue con el
  deep sleep de siempre.

Con USB se mantiene el deep sleep: el consumo da igual y el log serie sigue
disponible.

### Batería

El M5PM1 no tiene medidor de carga, solo `VBAT` en mV. El porcentaje sale de
la curva de descarga medida en esta placa por el port de CrossPoint
([paperdiy-reader](https://github.com/dcarnicer/paperdiy-reader),
`BatteryMonitor.cpp`): por defecto la celda 604050 de 1500 mAh (4,10 V = 100 %,
3,43 V = 0 %), o la de 140 mAh del kit con `-D PAPERDIY_BATTERY_PROFILE_KIT`.
Se envía en `Percent-Charged` y LaraPaper lo usa para el porcentaje y los
avisos de batería baja.

- Se mide al despertar, antes de encender la pantalla y la Wi-Fi.
- Con USB y cargando, repite el último valor medido con batería (la tensión de
  carga no dice nada del estado); con la carga terminada, 100 %.
- Con batería aplica la curva con 8 mV de histéresis y guarda el valor en NVS
  (`pd_batt_pct`).
- Si con batería baja del 0 % de la curva, muestra "Battery empty" y se apaga
  con el PM1 sin temporizador hasta que se cargue y se pulse el botón de
  encendido. Es el corte que el lector hace a 3,4 V con el LVP del PM1, que
  aquí no se puede usar (ver abajo).

### Corte por batería baja (LVP del PM1)

El PM1 corta la alimentación si `VBAT` baja del umbral de `BATT_LVP` (0x08). A
3,4 V lo hacía en el primer refresco rápido de la pantalla con la batería en
reposo a 4,17 V, siempre, y la placa quedaba apagada hasta una pulsación larga
del botón de encendido. Solo con bajar el umbral a 3,0 V se arregló, así que el
PM1 parece mirar la caída de `VBAT` bajo la carga del refresco. 3,0 V solo
salta con la celda casi vacía. El lector de CrossPoint usa 3,4 V sin problema,
pero pinta con M5GFX; los refrescos rápidos de FastEPD consumen más de golpe.

### M5Launcher

El firmware detecta Launcher (su partición residente tiene el subtipo `test`)
y escribe en el log su configuración (`DDLB`, `LauncherOnKey`,
`LauncherKeyLvl`, del namespace NVS `launcher`). Con Launcher 3.0.0, configura:

- `GPIO on boot starts Launcher` = **A** (con `GPIO Level` = `0`): los
  encendidos del PM1 van directos a TRMNL; Launcher solo aparece si mantienes
  A al arrancar.
- `DeepSleep starts Launcher` **desactivado**: con USB, los despertares por
  temporizador usan deep sleep del ESP32 y, si está activado, pasarían antes
  por la pantalla de Launcher (un refresco extra por despertar).

Si falta alguna de las dos, el log lo recuerda al arrancar.

### OTA

`src/paperdiy_ota.cpp`. Tras pintar la pantalla, como mucho una vez al día,
lee `latest.json` de la última release de GitHub
(`releases/latest/download/latest.json`). Si su `version` (`X.Y.Z-N`) es
mayor que la propia, descarga `app_url` en el otro hueco con el
`FirmwareUpdateService` de TRMNL y reinicia. Las versiones `-rc` son
prereleases y GitHub no las da como `latest`.

- **Solo sin Launcher.** Bajo Launcher la app vive en un hueco del tamaño
  justo y no hay otro libre; se actualiza desde el catálogo de Launcher.
- **Dos huecos de 3 MB** (`boards/paperdiy_partitions.csv`, desde la
  1.8.20-2). Con la tabla de la 1.8.20-1, de un solo hueco, no se actualiza:
  hay que grabar una vez la imagen completa.
- **Batería:** solo con USB o con la batería al 50 % o más.
- **Vuelta atrás:** el bootloader se compila con
  `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. La imagen nueva solo se confirma
  (`esp_ota_mark_app_valid_cancel_rollback`) al llegar al sueño; si se cuelga
  o se reinicia antes, el bootloader vuelve a la anterior. Esta anota la
  versión fallida en NVS (`pd_ota_skip`) y no la vuelve a instalar. En la
  PaperDIY se salta el `esp_ota_mark_app_valid_cancel_rollback()` que
  `setup()` hace al arrancar en las placas que no son la TRMNL X.
- **Seguridad:** HTTPS sin comprobar el certificado (`setInsecure`, como el
  resto del firmware). El ESP32 comprueba el hash de la imagen al terminar,
  así que una descarga corrupta no se instala, pero no hay firma.
- Las variantes `_dev` no se actualizan solas. Para probar, compila con
  `-DPAPERDIY_OTA_IN_DEV -DPAPERDIY_OTA_ALLOW_HTTP`,
  `-DPAPERDIY_OTA_MANIFEST_URL=\"http://…/latest.json\"`,
  `-DPAPERDIY_OTA_CHECK_INTERVAL_S=60` y, para una versión que se cuelgue,
  `-DPAPERDIY_OTA_TEST_CRASH`.

`scripts/extra/paperdiy_sdkconfig_path.py` hace absoluta la ruta del
`sdkconfig`: con la relativa, pioarduino compilaba el bootloader con los
valores por defecto de ESP-IDF y sin vuelta atrás.

## Compilar

```bash
make venv     # una vez: PlatformIO Core 6.1.19 en .platformio-venv/
make build
```

Usa un core de PlatformIO local (`.platformio-core/`, ignorado por git) para
que otro proyecto que comparta `~/.platformio` no cambie la versión del
toolchain o del framework. La primera vez descarga unos 5 GB. Hace falta
PlatformIO Core 6.1.19: la 6.2.0 trae SCons 4.11.1 y pioarduino espera 4.8.1,
así que el enlazado falla con `No module named 'SCons.Tool.FortranCommon'`.
`make venv` crea el entorno con 6.1.19; usa `PYTHON=python3.13` si no tienes
`python3.14`. Equivale a:

```bash
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" .platformio-venv/bin/pio run -e TRMNL_X_PAPERDIY
```

Genera `.pio/build/TRMNL_X_PAPERDIY/trmnl-paperdiy-factory.bin`: bootloader,
tabla de particiones (`boards/paperdiy_partitions.csv`) y app, para grabar en
`0x0`. `make test` ejecuta las pruebas nativas.

La variante `_dev` añade `-D DEV_FIRMWARE=1` y `-D DO_NOT_LIGHT_SLEEP=1`: las
pantallas de mensaje hacen un light sleep de 1 s que corta el CDC por USB, y
sin ese flag el log serie se queda mudo hasta el siguiente arranque. El build
de release mantiene el light sleep.

## Instalar

### Con M5Launcher (junto al lector)

1. Copia `trmnl-paperdiy-factory.bin` a la SD.
2. En M5Launcher: **SD**, elige el archivo y luego **Install**. También puedes
   subirlo desde **WUI > OTA Update**.
3. Arranca TRMNL desde el menú de Launcher.

Con `GPIO on boot starts Launcher = A`, los despertares por temporizador van
directos a la última app que arrancó. Para volver al lector, mantén A
pulsado y pulsa el botón de encendido para abrir Launcher.

Con Launcher instalado **no grabes en `0x10000`** (ni `pio run -t upload`
ni `esptool write_flash 0x10000`): sobrescribirías Launcher.

Launcher crea una partición `spiffs` de 448 KB para esta app porque la
imagen la declara vacía. TRMNL guarda ahí la imagen actual, la anterior y el
logo; con dashboards normales cabe.

La partición de la app que crea Launcher es de 1472 KB. Con el Captive Portal
2.1 y el cambio de matriz de grises el firmware ocupa unos 1437 KB: quedan
unos 35 KB. `check_release_assets.py`
falla si la app no cabe (y avisa cuando queda menos de 32 KB); para publicar
igualmente, por ejemplo solo para instalaciones sin Launcher, pásale
`--allow-launcher-oversize`.

El entorno compila con `build_type = release` (el resto de la base usa
`debug`): con `debug` la app son unos 24 KB más y el margen de Launcher baja a
unos 12 KB. Para depurar, `pio debug` sigue generando una compilación de depuración.

### Actualizar sin perder Wi-Fi ni servidor

Si grabas con esptool o M5Burner en `0x0`, la imagen completa
(`trmnl-paperdiy-factory.bin`) reescribe el bootloader y la tabla de
particiones: úsala solo para la primera instalación. Para actualizar, graba
solo `firmware.bin` en la partición de TRMNL. NVS (redes Wi-Fi y URL del
servidor) y Launcher quedan intactos:

```bash
make flash-app PORT=/dev/cu.usbmodemXXXX
```

O, si ya has compilado:

```bash
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" \
  python3 scripts/paperdiy/flash_app.py --port /dev/cu.usbmodemXXXX
```

El script lee la tabla de particiones, identifica la app por su cabecera
(`trmnl-firmware`) o por la etiqueta de Launcher, comprueba que la nueva app
quepa en el hueco y la escribe en su offset. `--dry-run` muestra el plan sin
escribir nada; si la detección es ambigua, `--offset 0x750000` (el de tu
instalación) la salta. Sin Launcher escribe en el hueco activo y no arma la
vuelta atrás del OTA: para actualizaciones de release sigue usando la OTA de
GitHub. Si prefieres hacerlo a mano:

```bash
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX read_flash 0x8000 0xC00 pt.bin
python gen_esp32part.py pt.bin          # busca la línea de TRMNL
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX write_flash 0x750000 .pio/build/TRMNL_X_PAPERDIY/firmware.bin
```

Para entrar en modo descarga, mantén el botón de encendido unos 2 s (el LED
rojo parpadea). Al terminar, un clic en ese botón arranca la app. Bajo
Launcher, si no vuelve sola a TRMNL, elígelo en el menú.

### Sin Launcher

```bash
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX write_flash 0x0 .pio/build/TRMNL_X_PAPERDIY/trmnl-paperdiy-factory.bin
```

Esto borra lo que hubiera instalado. Antes, haz una copia del firmware actual.

Al pasar de la 1.8.20-1 (o de Launcher) a la tabla de dos huecos, la partición
de datos cambia de sitio: las redes Wi-Fi se pierden y hay que volver a
configurarlas en el portal. Viniendo de Launcher también se pierde la API key;
en LaraPaper, abre Auto-Join para que `/api/setup` la vuelva a entregar.

## Actualizar dependencias y upstream

```bash
make check-updates
```

Es un informe de solo lectura: compara `upstream/main`, consulta las releases y
las cabeceras de M5PM1, M5PaperDIY-UserDemo, M5Launcher, FastEPD, bb_epaper,
PNGdec, JPEGDEC, pioarduino y `paperdiy-reader`, y las enseña junto a los pins
locales de `platformio.ini`. Necesita red; si hay `gh` autenticado usa su token
para no agotar el límite de la API.

Qué merece la pena mirar:

- **Upstream**: solo lo que afecte a `PARALLEL_EPD`, FastEPD, portal, PM1 o
  decodificación. El resto de placas no se toca.
- **M5PM1 / M5PaperDIY-UserDemo**: registro, timer, LVP y apagado del PM1.
- **M5Launcher**: layout de particiones, NVS y opciones de arranque/OTA.
- **FastEPD**: init del panel y matriz de grises del ED047TC1.
- **PNGdec / JPEGDEC**: correcciones de decodificación y seguridad.
- **pioarduino**: cambiar de plataforma altera el `sdkconfig` y el margen de
  Launcher; no se sube sin probar.
- **M5GFX / M5Unified**: este port no las usa; el panel va por FastEPD.

El procedimiento completo (qué probar, cuándo subir `PAPERDIY_PORT_REVISION` y
cuándo publicar) está en `AGENTS.md`. Tras cualquier cambio que toque panel,
PM1 o imagen hay que hacer el smoke test de hardware antes de etiquetar.

## Releases

Al subir un tag `paperdiy-vX.Y.Z-N` (X.Y.Z = versión de `include/config.h`,
N = revisión del port), `.github/workflows/release-paperdiy.yml` compila
`TRMNL_X_PAPERDIY` y publica:

| Archivo | Uso |
|---|---|
| `trmnl-paperdiy-latest.bin` | Imagen completa (0x0) para M5Burner, esptool o M5Launcher |
| `trmnl-paperdiy-<versión>-app.bin` | Solo la app: la que descarga la OTA, y para actualizar bajo Launcher con esptool en el offset de su partición |
| `latest.json` | Manifiesto de la OTA: `version`, `app_url` (la app de ese tag), `app_size`, `app_sha256` |

`scripts/paperdiy/check_release_assets.py` comprueba que la app de las dos
imágenes es la misma, que quepa en la partición de 1472 KB de Launcher (falla
si no, y avisa cuando quedan menos de 32 KB) y que la imagen completa lleve
bootloader y tabla al principio. El workflow de release también pasa los tests
nativos (`pio test -e native`) antes de publicar; `.github/workflows/paperdiy.yml`
hace la compilación y esas comprobaciones en cada push a la rama `paperdiy`. Para
publicar en M5Burner (y así en el catálogo de Launcher), ver
`m5burner/README.md`.

## Primer arranque

1. El dispositivo abre el punto de acceso **TRMNL**. Conéctate: el portal
   (Captive Portal 2.1) tiene una entrada **Wi-Fi** con las redes guardadas
   (hasta cinco) y **Select a Network** para añadir una. **Save and Connect**
   guarda la red solo cuando el dispositivo ha llegado a conectarse; si falla,
   vuelve al portal con el motivo. **Done** sale y reinicia con una red
   guardada; sin pulsar nada, a los 15 minutos también sale.
2. Servidor, en **Advanced Settings > Server**:
   - **Nube de TRMNL:** necesitas una licencia BYOD. Registra el dispositivo
     con su MAC y elige el modelo **M5PaperS3** (960×540).
   - **Servidor propio (BYOS):** escribe su URL en el campo de URL de la API.

### Con LaraPaper

- Auto-Join da de alta el dispositivo con el modelo **M5PaperS3** (960×540,
  PNG de 4 bits) gracias a la cabecera `Model-Id: m5_paper_s3`. Desactiva
  Auto-Join después.
- Sin modelo, LaraPaper sirve el BMP de 800×480 de la TRMNL original, y con
  FastEPD el firmware no dibuja BMP: la pantalla no cambia. Si el dispositivo
  ya existía sin modelo, asígnalo a mano.
- El log detallado por USB es **Advanced Settings > Diagnostics > Serial
  logs**, desactivado por defecto y guardado al momento. Guardar el servidor
  ya no lo cambia: la variante `_dev` escribe por USB hasta que se desactive
  ahí, y se vuelve a activar desde el mismo sitio.

## Botones

| Acción | Efecto |
|---|---|
| A, pulsación corta | Despierta y refresca (solo con USB: con batería la placa está apagada) |
| A, doble pulsación | Función especial asignada en el servidor |
| A, 5–15 s | Abre el portal de ajustes (conserva las redes guardadas) |
| A, más de 15 s | Borra las credenciales del dispositivo |
| Encendido (PM1), un clic | Enciende o reinicia (refresca) |
| Encendido (PM1), 2 s | Modo descarga (LED rojo parpadeando) |
| Encendido (PM1), doble clic | Apaga. Para volver, pulsa encendido |
| B | Sin uso |

## Estado

Probado el 16/11/2026 en una PaperDIY por USB, con la variante
`TRMNL_X_PAPERDIY_dev` (log serie activado) y la nube de TRMNL sin licencia
BYOD:

- [x] El panel pinta con el raíl del PM1: logo, portal Wi-Fi y pantalla de
      "no registrado" (PNG de 960×540 a 1 bpp).
- [x] El PM1 responde y la batería se lee (`PaperDIY: battery 4160 mV`).
- [x] Portal Wi-Fi, conexión y `/api/setup`. El servidor reconoce el modelo
      como `m5_paper_s3`.
- [x] Despertar por temporizador (300 s) y con el botón A.
- [x] Sin bucle de despertares. En deep sleep, el GPIO4 queda flotando y EXT0
      lo leía como pulsado. Se corrige con el pull-up RTC en
      `paperdiy::prepareForDeepSleep()`.

Probado el 17/11/2026 con LaraPaper y bajo M5Launcher 3.0.0 (junto a
CrossPoint):

- [x] Instalación desde la SD de Launcher; LittleFS de 448 KB.
- [x] `/api/setup` y `/api/display` contra LaraPaper, PNG de 960×540 a 4 bpp.
- [x] Corte LVP a 3,0 V: el primer refresco rápido ya no apaga la placa.
- [x] Cabecera `Model-Id: m5_paper_s3`.
- [x] Con batería: apagado por el PM1 y encendido con su temporizador cada
      120 s durante 15 minutos (`Reset-Reason: ESP_RST_POWERON`,
      `Update-Source: timer`, `Wake-Time` recuperado de NVS). Con USB, deep
      sleep.

Probado el 17/11/2026 sin Launcher, con la variante `_dev` y versiones de
prueba servidas desde la red local:

- [x] Actualización 1.8.20-2 → 1.8.20-3: descarga, reinicio y `now running`.
      En la siguiente comprobación no vuelve a descargar.
- [x] Vuelta atrás: la 1.8.20-5 (`PAPERDIY_OTA_TEST_CRASH`) se cuelga en su
      primer arranque, el bootloader vuelve a la 1.8.20-3 y esta anota la 5
      como fallida.

Probado el 18/11/2026 tras integrar upstream (Captive Portal 2.1, TRMNL
`45e58db`) y subir la revisión del port a 3:

- [x] Compila (`TRMNL_X_PAPERDIY`), pasan los tests nativos y `pio check`.
- [x] La app mide 1.471.136 bytes (1437 KB) con `build_type = release`: cabe en
      los 1472 KB de Launcher, con unos 35 KB de margen.
- [x] Portal 2.1 en la placa: Wi-Fi, red guardada, **Advanced Settings >
      Server** y **Done**. Al salir, `/api/setup` contra LaraPaper (200, API
      key y friendly ID) y `/api/display` (200, PNG de 960×540 a 4 bpp,
      refresco de 900 s); la imagen se pinta y el equipo se duerme.
- [x] PM1 en hardware: fw `0x54`, `PWR_CFG 0x03`, LVP 2999 mV, VIN 5096 mV,
      batería 4166 mV; `Percent-Charged: 100`, `Battery-Charging: 0` y
      `USB-Connected: true` con el cargador terminado. El VIN se enmascaraba a
      12 bits en el log (`978 mV` en vez de `5096`); corregido.
- [ ] Un ciclo de despertar y dormir con batería con el portal nuevo.

Probado el 19/11/2026 en hardware real (smoke test de la revisión 4):

- [x] Flash completo de `_dev` en `0x0`: PM1 `fw 0x54`, `PWR_CFG 0x03`,
      LVP 2999 mV, VIN 5060 mV, batería 4162 mV.
- [x] Log `keeping FastEPD's ED047TC1 gray matrix`; gradiente 4 bpp de 16
      niveles servido por `scripts/mock_server.py`, con negro profundo
      (validado a ojo).
- [x] Portal Wi-Fi, `/api/setup` 200, `/api/display` 200 y descarga del PNG
      960×540 a 4 bpp; el equipo se duerme tras el refresco.
- [x] `make flash-app` escribe solo `app0` en `0x10000`; tras reiniciar,
      `/api/display` vuelve a funcionar **sin** `/api/setup`: NVS, Wi-Fi y URL
      del servidor se conservan.
- [x] Despertar con A en deep sleep (USB) y nuevo `/api/display`.

Pendiente:

- [x] `Percent-Charged` con USB (carga terminada: 100 %) y detección de
      Launcher con la tecla A configurada.
- [ ] Corte "Battery empty" con la batería real por debajo de 3,43 V.
- [ ] Medir el consumo apagado con el PM1 (se esperan unos 32 µA).
- [x] Portada para M5Burner (`m5burner/cover.png`) y primera publicación
      (1.8.20-1, pendiente de revisión).
- [ ] OTA real contra las releases de GitHub (HTTPS y redirección), con la
      primera release que publique `latest.json`.
- [ ] Ghosting con la matriz de grises de la PaperS3 (el negro ya se validó
      en hardware; falta medir el ghosting entre refrescos).
