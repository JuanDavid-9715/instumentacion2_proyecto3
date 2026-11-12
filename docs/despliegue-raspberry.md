# Despliegue en la Raspberry Pi 4

Guía de puesta en marcha del sistema completo en la Raspberry, desde cero hasta la
prueba con el PLC. El PLC **no** se modifica: solo se conecta un contacto seco a una
entrada digital existente.

---

## 0. Antes de tocar nada

| Elemento | Detalle |
|---|---|
| Raspberry Pi 4 | Raspberry Pi OS **64 bits** (Bookworm) |
| Cámara USB UVC | la misma del PC. Aparece como `/dev/videoN` |
| Módulo de relé | con optoacoplador, contacto **NC**, alimentación 5 V |
| Entrada del PLC | 1 entrada digital X a 24 V DC, libre |

### Cableado del relé

| Pi (pin físico) | Módulo de relé | Nota |
|---|---|---|
| GPIO 17 → **pin 11** | `IN` | señal de control |
| GND → **pin 6** | `GND` | masa común |
| 5 V → **pin 2** (o 4) | `VCC` | alimenta la bobina, **no** desde 3.3 V |
| — | `COM` + `NC` | contacto seco hacia la entrada X del PLC |

**Nunca** metas 24 V a la Raspberry. El relé es el que aísla.

### Polaridad, que es lo que puede salir mal

- Relé **energizado** → NC abierto → entrada X en 0 V → **la línea marcha**.
- Relé **desenergizado** → NC cerrado → entrada X en 24 V → **la línea se pausa**.

Si el módulo es activo en alto, el comportamiento se invierte y la línea marcharía con
la Pi muerta. Ese es el fallo que hay que descartar **antes** de conectar el PLC
(§8). En `config.yaml`, `actuator.active_low: true` corresponde a módulos que se
energizan con un 0 lógico, que es lo habitual.

---

## 1. Dejar el código listo en el PC

**Antes de desplegar, comprueba que la calibración está dentro del código.** Mientras
haya cambios sin commitear, un `git clone` en la Pi trae la versión vieja sin el
polígono de la banda.

```bash
git status --short
git diff --stat
```

Dos caminos:

**A. Commit y push** (recomendado, deja historial y permite clonar):

```bash
git add -A
git commit -m "feat(detector): aísla la banda con polígono y descarta la estructura naranja"
git push
```

**B. Copiar el árbol de trabajo tal cual**, sin commitear (§3, opción `tar`). Sirve
para probar ya, pero la Pi queda con código que no está en ningún sitio versionado.

---

## 2. Sistema base en la Pi

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-dev build-essential v4l-utils
```

---

## 3. Copiar el proyecto

Sustituye `IP` y `pi` por los tuyos.

**A. Desde Git** (solo si hiciste el push de §1):

```bash
ssh pi@IP
git clone https://github.com/JuanDavid-9715/instumentacion2_proyecto3.git ~/proyecto3
```

**B. Copiando el árbol de trabajo** (desde el PC, en la raíz del repo):

```bash
tar czf - --exclude=.venv --exclude=.git --exclude=__pycache__ \
  --exclude='*.pyc' --exclude=.pytest_cache --exclude=MEMORY.md . \
  | ssh pi@IP 'mkdir -p ~/proyecto3 && tar xzf - -C ~/proyecto3'
```

`MEMORY.md` se excluye a propósito: es estado local del PC y no se publica.

`rsync` no está instalado en el PC de desarrollo; el `tar` de arriba hace lo mismo sin
dependencias extra.

---

## 4. Identificar la cámara

```bash
v4l2-ctl --list-devices
```

Salida típica:

```
USB Camera: USB Camera (usb-0000:01:00.0-1.1):
        /dev/video0
        /dev/video1
```

Un UVC expone **dos nodos**: el primero es captura, el segundo suele ser metadatos.
Confirma cuál sirve video y qué formatos acepta:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext | head -30
```

Tiene que aparecer `MJPG` con `1280x720`. Si el nodo equivocado da error o solo
`YUYV`, prueba con el otro.

Anota el número: va en la configuración del §6.

---

## 5. Entorno Python y dependencias

```bash
cd ~/proyecto3
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install lgpio
```

`lgpio` es el backend de GPIO de Bookworm. Se compila al instalarlo, por eso hace
falta `build-essential` y `python3-dev` del §2.

Si la compilación de `lgpio` falla, alternativa con el paquete del sistema:

```bash
sudo apt install -y python3-lgpio
deactivate && rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Verifica que gpiozero encuentra un backend real** (aquí no debe decir `mock`):

```bash
python - <<'PY'
from gpiozero import Device
print("fabrica de pines:", Device.pin_factory)
PY
```

Debe imprimir algo con `lgpio` o `gpiod`. Si dice `None`, no hay backend y el relé no
va a funcionar aunque el resto arranque.

---

## 6. Configuración de la Pi

El código es el mismo que en el PC. Lo único distinto son dos valores: el número de
cámara y el backend del relé.

```bash
cp config.yaml config.local.yaml
nano config.local.yaml
```

Cambia **solo** esto:

```yaml
camera:
  device: 0          # el nodo confirmado en §4

actuator:
  backend: gpio      # en el PC es "mock"
  gpio_pin: 17
  active_low: true
```

`config.local.yaml` está en `.gitignore`, así que no se publica.

> **No hagas un archivo parcial.** El cargador rellena las secciones que faltan con
> los valores por defecto del código, que **no** son los calibrados: `hue_min` 12,
> `sat_min` 120, `min_area_px` 300 y el polígono de la banda desactivado. Un
> `config.local.yaml` de tres líneas arranca el sistema con la calibración vieja y va
> a fallar justo en lo que ya se corrigió. Copia el archivo completo y edita dos
> líneas.

---

## 7. Controles de la cámara (cada sesión)

Los controles V4L2 **no sobreviven** a un reinicio ni a reconectar la cámara, y el
proyecto no usa autostart por decisión de alcance. Se aplican a mano al empezar:

```bash
v4l2-ctl -d /dev/video0 \
  -c white_balance_automatic=1 \
  -c backlight_compensation=0 \
  -c exposure_dynamic_framerate=0 \
  -c auto_exposure=1 \
  -c exposure_time_absolute=300 \
  -c gain=0
```

Sin esto la cámara vuelve a exposición automática: el brillo oscila, los umbrales se
invalidan solos y aparecen pausas falsas.

Comprueba que quedaron aplicados:

```bash
v4l2-ctl -d /dev/video0 --list-ctrls | grep -E "auto_exposure|exposure_time|white_balance_auto|gain"
```

---

## 8. Primera verificación, sin relé y sin PLC

**Cámara:**

```bash
python app.py -c config.local.yaml --probe
```

Esperado: `"opened": true`, `"width": 1280`, `"height": 720`, `"fps"` alrededor de 26.

**Pruebas:**

```bash
pytest
```

No necesitan cámara ni GPIO. Si pasan, el entorno está completo.

**Detección sin web** (deja la línea como está, no toca el relé si aún no lo
conectaste):

```bash
python app.py -c config.local.yaml --no-web
```

Pon maíz sobre la banda: en el log debe aparecer `line PAUSED`. Retíralo: tras
`clear_frames` frames limpios, `line RUNNING`.

---

## 9. Verificación eléctrica del relé

Con el PLC **desconectado**, multímetro en continuidad entre `COM` y `NC`:

```bash
python app.py -c config.local.yaml --no-web
```

| Momento | Bobina | COM–NC | Significado |
|---|---|---|---|
| Pi apagada | desenergizada | **continuidad** | seguro: pausa |
| App arrancando, sin frame aún | desenergizada | **continuidad** | seguro: pausa |
| Banda limpia detectada | energizada | **abierto** | marcha |
| Maíz detectado | desenergizada | **continuidad** | pausa |
| `Ctrl+C` | desenergizada | **continuidad** | pausa |

Si el primer renglón no se cumple (con la Pi apagada el contacto queda abierto), el
módulo es activo en alto: cambia `active_low` a `false` en `config.local.yaml` y
repite. **No conectes el PLC hasta que la fila "Pi apagada = continuidad" se cumpla.**

---

## 10. Conexión al PLC

El relé entrega un contacto seco, así que sirve tanto para una entrada que espera
24 V como para una que espera 0 V. No se toca el programa del PLC ni su lógica: solo
se lleva el contacto a una entrada X libre.

Orden obligatorio:

1. **PLC sin alimentar.** Conecta `COM` y `NC` en serie con la entrada X elegida.
2. **Energiza el PLC con la Pi apagada.** La línea debe quedar **en pausa**. Si
   arranca, el contacto está invertido o la entrada no es la que crees: apaga y
   revisa antes de seguir.
3. **Enciende la Pi y arranca el programa.** La línea debe seguir en pausa hasta el
   primer frame analizado, y luego liberarse sola.
4. **Pon maíz.** Debe pausar. Sácalo: debe reanudar tras unos 8 frames limpios.

Prueba de fallo, tan importante como la anterior:

5. **`Ctrl+C`** → la línea se pausa.
6. **Desconecta la cámara USB** → en `safety.frame_timeout_s` (2 s) la línea se pausa.
7. **Apaga la Pi de golpe** (desenchufar) → la línea se pausa.

Los tres últimos son la razón de ser del diseño fail-safe: si alguno no pausa, el
sistema no está listo para operar.

---

## 11. Operación normal

Arranque manual, en una terminal:

```bash
cd ~/proyecto3
source .venv/bin/activate
v4l2-ctl -d /dev/video0 -c white_balance_automatic=1 -c backlight_compensation=0 \
  -c exposure_dynamic_framerate=0 -c auto_exposure=1 -c exposure_time_absolute=300 -c gain=0
python app.py -c config.local.yaml
```

Desde el navegador del PC: `http://IP:8000`.

Para parar: `Ctrl+C` en esa terminal. El programa desenergiza el relé antes de salir.

---

## 12. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `ModuleNotFoundError: lgpio` | falta el backend de GPIO | §5: `pip install lgpio` o venv con `--system-site-packages` |
| `configuración no encontrada` | ruta del `-c` | ejecutar desde `~/proyecto3` |
| Arranca pero el relé nunca conmuta | `actuator.backend` sigue en `mock` | §6 |
| La cámara abre a 5 fps | no se aplicó MJPG o el nodo es el de metadatos | §4, §7 |
| `opened: false` | nodo equivocado u ocupado por otro proceso | `fuser -v /dev/video0` |
| La línea pausa sola sin maíz | exposición o balance automáticos | repetir §7 |
| La línea pausa sola en las esquinas | se movió la cámara | re-trazar `belt.points` (§13) |
| `ConfigError` al arrancar | un valor fuera de rango | el mensaje dice la clave exacta |
| Se ve el video pero con retraso | ancho de banda | bajar `web.stream_fps` |

---

## 13. Si hay que mover la cámara

`belt.points` y `roi` dependen del encuadre. Es lo único de la configuración que hay
que rehacer si cambia el montaje.

1. Guarda un frame real sin maíz.
2. Dibújalo con una rejilla de 100 px.
3. Traza el polígono **por dentro** de los rieles metálicos, no sobre el borde visible.
4. Verifica con la escena vacía que da 0 cajas en 80 frames seguidos.

---

## 14. Lista de comprobación para el informe

- [ ] `pytest` en verde en la Pi
- [ ] `--probe` con 1280×720 y ~26 fps
- [ ] Relé desenergizado con la Pi apagada (continuidad COM–NC)
- [ ] Línea en pausa al energizar el PLC con la Pi apagada
- [ ] Línea marcha con banda limpia
- [ ] Línea pausa con maíz y reanuda al retirarlo
- [ ] Línea pausa con `Ctrl+C`
- [ ] Línea pausa al desconectar la cámara en menos de 2 s
- [ ] Línea pausa al apagar la Pi de golpe
- [ ] Interfaz web accesible desde el PC
