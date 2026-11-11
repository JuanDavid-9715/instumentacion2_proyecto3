# AGENTS.md

> Instrucciones para agentes de IA que trabajen en este repositorio.
> Este archivo describe **reglas estables**. El estado cambiante del proyecto vive en `MEMORY.md`.

---

## 1. Proyecto

Máquina de llenado de cajas de arroz, controlada por un PLC. Este repositorio contiene **una sola integración nueva**: un sistema de visión que detecta material no deseado (maíz) sobre la banda transportadora de ingreso y avisa al PLC cerrando una salida.

El PLC **no se modifica**. El sistema solo entrega una señal digital.

---

## 2. Hardware y topología

| Elemento | Detalle |
|---|---|
| Controlador existente | PLC FX3U-24MT (clon Mitsubishi). **Solo lectura: no se modifica ni su lógica ni su programa.** |
| Cómputo | Raspberry Pi 4 (Raspberry Pi OS 64-bit) |
| Sensor | Cámara USB UVC (clase estándar, sin SDK propietario) |
| Interfaz | Módulo de relé convencional, contacto **NC** |
| Entrada del PLC | 1 entrada digital X a 24 V DC |
| Banda | Negra, ~30 cm de recorrido |

Flujo:

```
Cámara USB → Raspberry Pi 4 → detector → máquina de estados → relé (NC) → entrada X del PLC → pausa
```

### Polaridad (crítica)

- Relé **energizado = línea OK**. Contacto **NC** abierto → entrada X en 0.
- Relé **desenergizado = pausa**. Contacto NC cerrado → entrada X en 24 V.
- Se desenergiza cuando: hay contaminación, el proceso falla, o la Raspberry pierde energía.

---

## 3. Stack

- Python 3.11+
- OpenCV (`opencv-python-headless`) y NumPy
- PyYAML para `config.yaml`
- FastAPI + Uvicorn para la interfaz web
- `gpiozero` con backend `lgpio`. **No** usar `RPi.GPIO`: está obsoleto en Raspberry Pi OS Bookworm
- pytest

---

## 4. Comandos

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python app.py     # arranca captura + detector + web
pytest            # pruebas, no requieren hardware
```

La interfaz web queda en `http://<ip-de-la-pi>:8000`.

---

## 5. Arquitectura (obligatoria)

**Monolito en capas, un solo proceso, un solo puerto.** No separar en servicios.

```
app.py                 composición: arranca todo
config.yaml            umbrales, ROI, polaridad, device
core/capture.py        hilo único dueño del frame
core/detector.py       detección (sin hardware)
core/state.py          máquina de estados + histéresis (sin hardware)
core/actuator.py       relé + fail-safe
web/server.py          FastAPI: /stream, /api/state, /api/config
web/static/index.html  interfaz
tests/
```

Reglas:

- **Un solo `VideoCapture`.** El hilo de captura es el único dueño del frame. El detector y el stream web lo consumen desde `queue.Queue(maxsize=1)`.
- `detector` y `state` **no importan** cámara, GPIO ni framework web. Se prueban con video pregrabado y con un `MockOutput`.
- Toda la configuración numérica vive en `config.yaml`. Nada de umbrales hardcodeados.
- El dispositivo de video se lee de `config.yaml`, nunca hardcodeado: en el PC de desarrollo es `/dev/video2`, en la Pi será otro.

---

## 6. Captura de video — receta validada

```python
cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))  # obligatorio
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
```

Antes de capturar, fijar por V4L2:

```bash
v4l2-ctl -d "$DEV" -c auto_exposure=1
v4l2-ctl -d "$DEV" -c exposure_time_absolute=50
v4l2-ctl -d "$DEV" -c white_balance_automatic=0
v4l2-ctl -d "$DEV" -c white_balance_temperature=5600
v4l2-ctl -d "$DEV" -c backlight_compensation=0
v4l2-ctl -d "$DEV" -c exposure_dynamic_framerate=0
```

Motivos, no los cambies sin medir:

- Sin `MJPG`, OpenCV negocia YUYV y cae a **5 fps a 720p**.
- Sin bloquear exposición y balance de blancos, los umbrales se invalidan solos.
- `backlight_compensation` viene activo y altera la exposición.
- `exposure_dynamic_framerate` permite que la cámara baje los fps con poca luz.

---

## 7. Regla no negociable: fail-safe

Cualquier salida del proceso debe dejar la salida en estado seguro:

1. `try/except/finally`: el `finally` desenergiza el relé.
2. Manejadores de `SIGINT` y `SIGTERM` que desenergizan antes de salir.
3. Una excepción no capturada nunca debe dejar el relé energizado.

Sin esta regla, un fallo del software se traduce en producto contaminado saliendo de la línea.

---

## 8. Prohibiciones

- No modificar el PLC, su programa ni su cableado.
- No agregar `systemd`, autostart ni watchdog de servicio. El sistema se inicia a mano.
- No usar Django.
- No agregar frameworks de frontend (React, Vue, bundlers). Solo HTML, CSS y JS plano.
- No commitear videos, capturas, modelos ni `config.local.yaml`.
- No dividir la aplicación en microservicios ni en procesos separados.
- No reescribir este archivo sin un motivo concreto.

---

## 9. Convenciones

- Código, identificadores y comentarios: **inglés**.
- Documentación, mensajes de interfaz y texto de cara al usuario: **español**.
- Conventional Commits. Sin atribución a IA ni `Co-Authored-By`.
- Configuración en YAML, validada al arrancar.

---

## 10. Memoria del proyecto

Antes de empezar a trabajar, **lee `MEMORY.md`**. Ahí están el estado actual, las decisiones ya tomadas con su justificación, los datos de hardware medidos y lo que falta.

Al cerrar un bloque de trabajo, actualízalo. La bitácora es append-only.

Para decidir dónde va una información nueva:

- ¿Cambia cuando el proyecto avanza? → `MEMORY.md`
- ¿Es una regla que no cambia? → este archivo
