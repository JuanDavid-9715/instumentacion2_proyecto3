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

python app.py           # arranca captura + detector + web
python app.py --probe   # abre la cámara, informa lo negociado y termina
pytest                  # pruebas, no requieren hardware ni video
```

La interfaz web queda en `http://<ip-de-la-pi>:8000`.

---

## 5. Arquitectura (obligatoria)

**Monolito en capas, un solo proceso, un solo puerto.** No separar en servicios.

```
app.py                 composición: arranca todo
config.yaml            umbrales, ROI, polaridad, device
core/config.py         carga y validación del YAML
core/capture.py        hilo único dueño del frame
core/detector.py       detección (sin hardware)
core/state.py          máquina de estados + histéresis (sin hardware)
core/actuator.py       relé + fail-safe
core/runtime.py        detector -> histéresis -> relé, estado compartido
web/server.py          FastAPI: /stream, /api/state, /api/config
web/static/index.html  interfaz
tests/                 pruebas sin hardware ni video
odd/tasks/             documentos de trabajo por funcionalidad
```

Reglas:

- **Un solo `VideoCapture`.** El hilo de captura es el único dueño del frame. El detector y el stream web lo consumen desde `queue.Queue(maxsize=1)`.
- El frame se comparte **de solo lectura**. Quien necesite dibujar (el overlay del stream) trabaja sobre una copia.
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
- No commitear videos, capturas, modelos, `config.local.yaml` ni `MEMORY.md`.
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

`MEMORY.md` es **local y no se versiona**: está en `.gitignore` y nunca se publica. Contiene el estado del proyecto, las decisiones tomadas con su justificación, los datos de hardware medidos y lo pendiente.

Regla de contenido: no debe incluir datos personales, credenciales, direcciones IP, rutas absolutas de la máquina ni notas que no quieras ver publicadas.

Si el archivo existe en la máquina, **léelo antes de empezar a trabajar** y actualízalo al cerrar un bloque. Si no existe, trabaja solo con este archivo.

Para decidir dónde va una información nueva:

- ¿Es estado que cambia y es local? → `MEMORY.md`
- ¿Es una regla que no cambia y se puede publicar? → este archivo

---

## 11. Trabajo remoto en la Raspberry Pi

El PC de desarrollo y la Raspberry Pi son dos entornos del mismo proyecto. El PC tiene más recursos y sirve para desarrollar y probar; la Pi es el destino final.

### Autorización

El trabajo remoto **no está implícito** en el permiso para trabajar en este repositorio. Antes de conectarse a la Pi, el usuario debe autorizar explícitamente en la sesión:

1. **Destino**: host o IP y usuario.
2. **Operaciones**: qué se permite ejecutar allí.
3. **Credencial**: la llave SSH a usar.

Si falta cualquiera de los tres, no se conecta y se trabaja en local.

### Protocolo

1. Desarrollar y probar en el PC, con `MockOutput` y video pregrabado.
2. Verificar en local: `pytest` y una corrida del detector sobre video.
3. Desplegar a la Pi por `scp` o `rsync`.
4. Ejecutar en la Pi: `pytest`, luego `python app.py`.
5. Verificar desde el navegador del PC: `http://<ip-de-la-pi>:8000`.

### Reglas

- Las llaves SSH son del usuario. **Nunca** se guardan contraseñas, tokens ni llaves privadas en el repositorio.
- La Pi permanece en la red local. **Nunca** se expone SSH a internet.
- No se instala nada en la Pi sin decirlo antes.
- El PLC no se toca desde ningún entorno.
- Todo comando remoto pasa por la regla `ask` del runtime: el usuario ve y aprueba cada uno.
- Si el SSH no está disponible, se continúa en local y se reporta el bloqueo. No se adivina ni se prueba el destino.

### Paridad de entornos

El mismo código corre en ambos entornos sin cambios. La diferencia entre PC y Pi —cámara real frente a archivo, GPIO frente a `MockOutput`— se selecciona por configuración, nunca duplicando código.
