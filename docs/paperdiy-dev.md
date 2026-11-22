# PaperDIY: control remoto y smoke tests por USB

La variante `TRMNL_X_PAPERDIY_dev` trae un control remoto por el puerto serie
USB. Sirve para comprobar la app sin tocar los botones, a mano o desde un
agente: retener la placa despierta, simular el botón A o un despertar por
temporizador, apuntarla a un servidor de pruebas, leer su estado y capturar el
framebuffer como PNG. `make smoke` lo usa para el smoke test de hardware que
pide `AGENTS.md`.

> **No lo publiques ni lo dejes en una placa ajena.** Cualquier cosa conectada
> a su cable USB puede pulsar A y cambiar su servidor. Solo lo compila el
> entorno `_dev` (`-D PAPERDIY_DEV_REMOTE=1`); el release, el workflow de
> releases y M5Burner llevan el entorno normal, sin este código.

Es la misma idea que el `CMD:KEY` del lector
([paperdiy-reader](https://github.com/dcarnicer/paperdiy-reader),
`docs/paperdiy/12-serial-testing.md`) y el `remote.py` de Tamagotcho, adaptada
a cómo funciona TRMNL: cada refresco es un arranque completo (despertar, Wi-Fi,
`/api/display`, descarga, pintar, dormir), así que el remoto actúa al final de
cada despertar y simula los despertares reiniciando la placa.

## Grabar la variante dev

```bash
make flash-dev PORT=/dev/cu.usbmodemXXXX   # solo la app, como make flash-app
make flash-app PORT=/dev/cu.usbmodemXXXX   # vuelta al build normal
```

Las dos conservan NVS (Wi-Fi, servidor, API key), LittleFS y M5Launcher.
Visto en hardware el 22/11/2026:

- Con una app en marcha y el USB conectado, esptool mete la placa en modo
  descarga él solo (reset por USB-JTAG); no hace falta tocar nada.
- Si la placa duerme, ponla en modo descarga a mano: botón de encendido unos
  2 s, hasta que el LED rojo parpadee.
- El reset por RTS del final de esptool la deja a menudo en el bootloader (el
  puerto sigue ahí, mudo), sobre todo tras el modo descarga del PM1. El reset
  por watchdog la arrancó siempre, también en ese caso:
  `esptool --chip esp32s3 --port … --before no-reset --after watchdog-reset chip-id`.
  Si no, un clic en encendido.

## Cómo funciona

Al final de cada despertar, justo antes de dormir o de que el PM1 apague la
placa (`enterSleep()` y `goToSleepButtonOnly()`), el firmware mira el puerto
serie:

- Si llegó una línea `CMD:` durante el despertar, o llega en los 1,5 s
  siguientes, la placa se queda despierta en un bucle de comandos y escribe
  `REMOTE:READY seq=<n> version=<versión> wake=<causa>`.
- Si no, duerme como siempre. Un despertar sin host solo tarda esos 1,5 s más.

Mientras está retenida:

- El límite de 20 minutos despierta se para; el bucle tiene el suyo: sin
  comandos durante 15 minutos, suelta la placa y duerme.
- Si se desenchufa el USB, suelta la placa y duerme (con batería, el PM1 la
  apaga). Con batería nunca la retiene.
- Los comandos que simulan un despertar reinician la placa con
  `ESP.restart()` y anotan en memoria RTC qué despertar simular. La retención
  también va en RTC, así que el siguiente despertar acaba otra vez en el bucle
  hasta `CMD:FREE` o `CMD:SLEEP`. Un apagado o el PM1 la borran.
- Al entrar en el bucle confirma la imagen como hace al dormir
  (`paperdiy_ota::confirmRunningImage()`), para que un reinicio del remoto no
  provoque la vuelta atrás de una OTA recién instalada.

## Comandos

Una línea por comando, `CMD:<NOMBRE>[:<ARG>]`, con mayúsculas. Las respuestas
empiezan por `REMOTE:`; el resto de líneas es el log habitual.

| Comando | Efecto | Respuesta |
|---|---|---|
| `CMD:PING` | Versión y número de sesión | `REMOTE:PONG version=… fw=… seq=… hold=…` |
| `CMD:HOLD` | Retener la placa (el host lo envía para cogerla) | `REMOTE:READY …` al entrar, luego `REMOTE:OK HOLD` |
| `CMD:STATUS` | Estado en una línea `clave=valor` | `REMOTE:STATUS …` (abajo) |
| `CMD:SHOT` | Volcado del framebuffer | `#shot` / `#r` / `#end` (abajo) |
| `CMD:REFRESH` | Igual que `CMD:BUTTON:SHORT` | `REMOTE:OK REFRESH restarting hold=…` |
| `CMD:BUTTON:SHORT` | Reinicia como un despertar por el botón A con pulsación corta: logo, errores a la vista y refresco | `REMOTE:OK BUTTON:SHORT restarting …` |
| `CMD:BUTTON:DOUBLE` | Lo mismo con doble pulsación: la función especial del servidor | igual |
| `CMD:BUTTON:LONG` | Lo mismo con pulsación de 5–15 s: abre el portal de ajustes (bloquea hasta que se cierra, 15 min como mucho) | igual |
| `CMD:WAKE:TIMER` | Reinicia como un despertar por temporizador: sin logo, errores reintentados en silencio | `REMOTE:OK WAKE:TIMER restarting …` |
| `CMD:RESTART` | Reinicia como un arranque en frío (el clic del botón de encendido) | `REMOTE:OK RESTART restarting …` |
| `CMD:SERVER` | URL de la API guardada (vacía: la de por defecto) | `REMOTE:SERVER url="…" default="https://trmnl.app"` |
| `CMD:SERVER:<url>` | Guarda otra URL (`http://` o `https://`, menos de 128 caracteres) | `REMOTE:OK SERVER old="…" new="…"` |
| `CMD:SERVER:-` | Vuelve a la URL por defecto | igual |
| `CMD:FREE` | El siguiente despertar duerme normal (la placa sigue en el bucle) | `REMOTE:OK FREE` |
| `CMD:SLEEP` | Sale del bucle y duerme ya | `REMOTE:OK SLEEP` |
| `CMD:HELP` | Lista de comandos | `REMOTE:HELP …` |

Un comando desconocido responde `REMOTE:ERR unknown …` y la lista.

**`CMD:SERVER` no borra la API key ni el friendly ID**, al contrario que el
portal, que obliga a emparejar de nuevo al cambiar de servidor. Así el smoke
test apunta la placa al mock y la devuelve a su servidor tal como estaba.

No hay comando para la pulsación de más de 15 s (borra las credenciales) ni
para el botón B (sin uso).

### `STATUS`

```
REMOTE:STATUS version=1.8.20-4 wake=2 pm1=1 battery_mv=4162 wake_battery_mv=4160 battery_pct=100
  usb=1 charging=0 launcher=0 wifi_saved=1 wifi=0 ssid="" rssi=0 ip= api_url="http://…"
  api_key=1 friendly_id="ABC123" refresh_s=900 panel=960x540 bpp=4 rotation=0 heap=… psram=…
  uptime_ms=… seq=3
```

(En una sola línea.) El despertar apaga la Wi-Fi en cuanto recibe la imagen
(`display_session.cpp`), así que al final suele salir `wifi=0`; `wifi_saved`
dice si tiene redes guardadas. `wake` es la causa del ESP-IDF: 0 arranque en frío, 2
botón (EXT0), 4 temporizador. `battery_mv` es una lectura nueva del PM1 y
`wake_battery_mv`, la del arranque (la que va en las cabeceras). Los valores
con espacios van entre comillas.

### Captura

```
#shot 960 540 4 0 259200     ancho, alto, bpp, rotación, bytes
#r 0 0000…ffff               una fila en hexadecimal, tal como está en el buffer
…
#r 539 …
#end 1a2b3c4d                CRC-32 (zlib) de todas las filas
```

Es el `currentBuffer()` de FastEPD tal como lo dejó el último refresco
(`display_sleep()` solo apaga el bus). A 4 bpp, el nibble alto es el píxel par;
0 es negro y 15 blanco. A 1 bpp, bit a 1 es blanco. Los PNG de 4 bits en gris
se copian tal cual al buffer, así que la captura se puede comparar byte a byte
con el PNG servido. Las líneas de log de otras tareas pueden caer entre filas,
nunca dentro de una. Es el framebuffer, no el panel: el ghosting o un panel que
no pinta no se ven aquí.

## `scripts/paperdiy/remote.py`

Solo usa la librería estándar. Coge la placa (`CMD:HOLD` cada segundo hasta
que contesta), ejecuta los pasos y escribe el log de la placa como `| …` y el
suyo como `> …`. Si la placa duerme, pulsa A: el comando espera 120 s.

```bash
python3 scripts/paperdiy/remote.py status shot:home
python3 scripts/paperdiy/remote.py refresh until:"request result" shot:after
python3 scripts/paperdiy/remote.py server:http://192.168.1.42:8080 refresh
python3 scripts/paperdiy/remote.py wake:timer shot:timer
python3 scripts/paperdiy/remote.py -f escenario.txt
python3 scripts/paperdiy/remote.py server:- free sleep   # dejarla como estaba
make remote PORT=/dev/cu.usbmodemXXXX STEPS="status shot:home"
```

| Paso | Hace |
|---|---|
| `ping`, `status` | Los comandos del mismo nombre |
| `shot[:NOMBRE]` | Guarda la captura como `NOMBRE.png` (gris de 8 bits). Reintenta hasta 3 veces si se pierde una fila o no cuadra el CRC |
| `refresh`, `button:short\|double\|long`, `wake:timer`, `restart` | Reinicia y espera a `REMOTE:READY` (180 s; 16 min con `button:long`). Si se había mandado `free`, espera a la línea `request result` |
| `server`, `server:URL`, `server:-` | Ver, cambiar o restaurar la URL |
| `hold`, `free`, `sleep` | Los comandos del mismo nombre |
| `wait:S` | Espera S segundos mostrando el log |
| `until:[S:]TEXTO` | Espera una línea de log que contenga TEXTO desde el último comando (60 s por defecto) |
| `raw:LÍNEA` | Envía LÍNEA tal cual (`raw:CMD:HELP`) |

Un escenario es un fichero con los mismos pasos, separados por espacios, con
comentarios `#` y comillas de shell. Opciones: `--port` (si no,
`$PAPERDIY_PORT` o el primer `/dev/cu.usbmodem*`), `-o DIR` (por defecto
`.pio/paperdiy-remote/`), `--raw` (guarda también el buffer empaquetado),
`--log FICHERO`, `-q` y los tiempos `--grab-timeout` y `--cycle-timeout`.

El puerto se abre sin `HUPCL` (cerrar no toca DTR/RTS) y, si desaparece porque
la placa reinicia o duerme, se vuelve a abrir solo.

## Smoke test: `make smoke`

```bash
make flash-dev PORT=/dev/cu.usbmodemXXXX
make smoke PORT=/dev/cu.usbmodemXXXX                     # o SMOKE_ARGS=--errors
python3 scripts/paperdiy/smoke.py --port … --errors --host-ip 192.168.1.42
```

Requisitos: la variante `_dev`, la placa por USB, con una Wi-Fi desde la que
llegue a este Mac y dada de alta en su servidor (con API key). El test no la
empareja con el mock: si no tiene API key, se para.

Pasos:

1. Coge la placa y comprueba `STATUS`: PM1, USB, batería entre 3,0 y 4,45 V,
   una Wi-Fi guardada, API key y panel de 960×540.
2. Arranca `scripts/mock_server.py` (puerto 8086, `--mock-port`) con un PNG de
   960×540 a 4 bits en gris: 16 barras de 0 a 15 en la mitad de arriba, de 15
   a 0 en la de abajo, y entre ambas una franja de píxeles negro/blanco
   alternos que detecta nibbles cambiados. Apunta la placa al mock con
   `CMD:SERVER`.
3. **Despertar por botón** (`CMD:REFRESH`): `REMOTE:READY wake=2`,
   `request result - HTTPS_SUCCESS`, `keeping FastEPD's ED047TC1 gray matrix`,
   ningún `FastEPD reported error` ni cuelgue (`Guru Meditation`, `Backtrace`,
   `abort()`, `Awake time limit`), y el mock ha servido `/api/display` y la
   imagen. La captura tiene que ser igual al PNG byte a byte.
4. **Despertar por temporizador** (`CMD:WAKE:TIMER`): lo mismo con `wake=4`.
5. Con `--errors`: el mock sirve una imagen basura una vez. Tiene que dar un
   resultado de error sin colgarse, y el siguiente despertar vuelve a pintar el
   degradado.
6. Siempre, aunque algo falle: restaura la URL de la API. Después, salvo
   `--keep-hold`, manda `CMD:FREE` y un último refresco contra el servidor de
   la placa, que acaba durmiendo normal con su pantalla.

Al terminar escribe `PASS` o `FAIL` con el número de comprobaciones, y deja en
`.pio/paperdiy-smoke/<fecha-hora>/` el log de la placa y del mock, el PNG
servido, las capturas, `status.json` y `report.json`. El mock va con `-q`, así
que la API key de la placa (cabecera `Access-Token`) no se guarda en el log.

Después **mira el panel**: 16 grises distintos por mitad y negro profundo
arriba a la izquierda. La captura no ve el panel.

Efectos secundarios: los logs que la placa tenía guardados se envían al mock
(`/api/log`) y no llegan a su servidor, y el intervalo de 900 s del mock queda
guardado hasta la siguiente respuesta de su servidor, que es el refresco final
del paso 6.

## Lo que no cubre

- El panel físico (ghosting, negros, que pinte): mirarlo a ojo.
- Batería, el apagado y el temporizador del PM1, el corte por batería baja y
  el consumo: con batería el remoto nunca retiene la placa.
- El botón de encendido y el botón A reales; el portal (`button:long` lo
  abre, pero se maneja desde el móvil); la OTA real contra GitHub.
- Las primeras líneas de cada arranque se pueden perder mientras el USB vuelve
  tras el reinicio. Las comprobaciones usan las que salen después (Wi-Fi,
  refresco, `REMOTE:READY`).

## Pruebas del arnés

`make test` ejecuta también `scripts/tests/`. `test_paperdiy_remote.py`
prueba `remote.py` y `smoke.py` contra una placa simulada en un pty: capturas
con filas perdidas y log intercalado, reinicios, `until:`, la restauración del
servidor y un `smoke.py --errors` completo contra el `mock_server.py` real. El
parser de comandos del firmware (`lib/trmnl/src/paperdiy_remote_command.cpp`)
tiene sus pruebas nativas en `test/test_paperdiy_remote_command/`.

## Estado

Probado el 22/11/2026 en una PaperDIY (MAC `44:1B:F6:D0:EA:08`) por USB, con
FastEPD `bd2d9c6` y TRMNL `1.8.20-4` `_dev` grabado sobre CrossPoint (copia
completa de la flash antes y restaurada después, verificada con
`verify-flash`):

- `make smoke` con `--errors`, run `20261122-205558`: **PASS, 37/37**. Los dos
  despertares (botón y temporizador) y la recuperación tras el error pintan el
  degradado; el framebuffer es igual al PNG byte a byte; la imagen basura da
  `HTTPS_WRONG_IMAGE_FORMAT` sin cuelgue. La URL del servidor volvió a la suya.
- Panel a ojo: 16 grises por mitad y negro profundo (confirmado por el
  usuario).
- Al reiniciar, el puerto USB desaparece y vuelve en ~1,2 s; `remote.py`
  reconecta solo. Se pierden las primeras líneas del arranque.
- La captura de 960×540 a 4 bpp (520 KB en hexadecimal) llega en ~1,7 s.
- Dos fallos encontrados y corregidos en la primera pasada:
  - Los `CMD:HOLD` enviados mientras la placa estaba ocupada desbordaron su
    buffer USB de 256 bytes y dejaron media línea pegada al comando siguiente
    (`CMD:CMD:PING`). El firmware se queda con el último `CMD:` de la línea y
    `remote.py` cierra la línea pendiente al coger la placa.
  - El despertar apaga la Wi-Fi en cuanto recibe la imagen, así que el smoke
    test ya no exige `wifi=1` al final: mira `wifi_saved`.
- Release `1.8.20-5`, misma placa: la primera pasada dio 37/37, pero una
  captura tardó 60 s porque la línea `#end` se quedó en el buffer de salida del
  USB hasta la siguiente escritura. El bucle hace `Serial.flush()` tras cada
  respuesta; la segunda pasada (`20261122-213951`) dio 37/37 en 69 s sin
  reintentos.
