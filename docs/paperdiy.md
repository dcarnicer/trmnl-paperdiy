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
| Botón TRMNL | Ninguno | A (GPIO4), que también despierta del deep sleep |
| Alimentación | — | `CHG_EN` y DCDC de 5 V activados en cada arranque; corte por batería baja a 3,4 V |
| OTA | El servidor la ofrece | Desactivada (`DISABLE_FIRMWARE_UPDATE`) |
| Modelo enviado | `m5_papers3` | `m5_papers3` (`API_DEVICE_MODEL`); en la lista interna es `m5_paperdiy` |

La OTA está desactivada porque el servidor, al ver el modelo de la PaperS3,
mandaría el firmware de la PaperS3, y porque con M5Launcher una escritura OTA
puede machacar otra app instalada. Para actualizar, instala una imagen nueva.

El código propio de la placa está en `include/paperdiy_board.h` y
`src/paperdiy_board.cpp`. Los cambios en el código común van tras
`#ifdef BOARD_TRMNL_X_PAPERDIY`.

## Compilar

```bash
pio run -e TRMNL_X_PAPERDIY
```

Genera `.pio/build/TRMNL_X_PAPERDIY/trmnl-paperdiy-factory.bin`: bootloader,
tabla de particiones (`boards/paperdiy_partitions.csv`) y app, para grabar en
`0x0`.

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

### Sin Launcher

```bash
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX write_flash 0x0 .pio/build/TRMNL_X_PAPERDIY/trmnl-paperdiy-factory.bin
```

Esto borra lo que hubiera instalado. Antes, haz una copia del firmware actual.

## Primer arranque

1. El dispositivo abre el punto de acceso **TRMNL**. Conéctate y elige tu red
   Wi-Fi.
2. Servidor:
   - **Nube de TRMNL:** necesitas una licencia BYOD. Registra el dispositivo
     con su MAC y elige el modelo **M5PaperS3** (960×540).
   - **Servidor propio (BYOS):** escribe su URL en el campo de URL de la API
     del portal.

### Con LaraPaper

- Auto-Join da de alta el dispositivo, pero **sin modelo**: LaraPaper solo lo
  asigna con la cabecera `Model-Id`, que este firmware no envía. Edita el
  dispositivo y elige **M5PaperS3** (960×540, PNG de 4 bits).
- Sin modelo, LaraPaper sirve el BMP de 800×480 de la TRMNL original, y con
  FastEPD el firmware no dibuja BMP: la pantalla no cambia.
- Guardar el servidor en el portal también guarda la opción de log
  detallado, desactivada por defecto. A partir de ahí la variante `_dev` deja
  de escribir por USB. Se vuelve a activar desde el portal.

## Botones

| Acción | Efecto |
|---|---|
| A, pulsación corta | Despierta y refresca |
| A, doble pulsación | Función especial asignada en el servidor |
| A, 5–15 s | Olvida la Wi-Fi |
| A, más de 15 s | Borra las credenciales del dispositivo |
| Encendido (PM1), un clic | Reinicia el ESP32 (refresca) |
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

Pendiente:

- [ ] Primera imagen real de `/api/display` (necesita licencia BYOD o un
      servidor propio).
- [ ] Lectura de USB y de carga con batería sola.
- [ ] Consumo en deep sleep con el raíl EPD cortado. Si es alto, fase 2:
      apagar con el temporizador del M5PM1 (`TIM_CNT`/`TIM_CFG`, acción
      POWERON) en vez del deep sleep del ESP32.
- [ ] Instalación y arranque desde M5Launcher; LittleFS de 448 KB.
- [ ] Ghosting con la matriz de grises de la PaperS3.
