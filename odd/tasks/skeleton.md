# Skeleton — sistema de visión, sin hardware ni video

**Objetivo:** esqueleto ejecutable y probado en el PC. Detector v0 validado con imágenes sintéticas. La calibración con video real queda para la sesión siguiente.
**Hoy:** 2026-11-11. **Ejecutor de pruebas:** `.venv/bin/pytest`.

## Specs

- **S1.** Un solo proceso, un solo puerto. `python app.py` arranca captura, detección y web.
- **S2.** Un único `VideoCapture`, propiedad exclusiva del hilo de captura. El detector y el stream web consumen copias desde colas `queue.Queue(maxsize=1)` que descartan el frame viejo.
- **S3.** `core/detector.py` y `core/state.py` no importan cámara, GPIO ni framework web. Se prueban sin hardware.
- **S4.** Toda la configuración numérica vive en `config.yaml`. El dispositivo de video sale de la configuración, nunca hardcodeado.
- **S5.** Detector: máscara HSV sobre amarillo/naranja, morfología, contornos, umbral de área mínima. Reporta área y cajas en coordenadas de frame completo.
- **S6.** Máquina de estados con histéresis: `confirm_frames` frames sucios para pausar, `clear_frames` frames limpios para reanudar.
- **S7.** Fail-safe: `try/except/finally`, manejadores de `SIGINT` y `SIGTERM`, y el relé arranca desenergizado. Si el detector lanza una excepción, la línea se pausa.
- **S8.** Polaridad: relé energizado = línea OK; desenergizado = pausa. `active_low` configurable para módulos de relé activos en bajo.
- **S9.** Web: `/` interfaz, `/stream` MJPEG, `/api/state` JSON, `/api/config` lectura y escritura en memoria. Interfaz en español, código en inglés.
- **S10.** `pytest` corre sin cámara, sin video y sin GPIO. Todos los tests deben pasar hoy.

## Tasks

| ID | Specs | Ruta | Estado |
|---|---|---|---|
| T1 | S4 | inline | hecho — `core/config.py`, `config.yaml`, `tests/test_config.py` |
| T2 | S3,S5,S10 | inline | hecho — `core/detector.py`, `tests/test_detector.py` |
| T3 | S3,S6 | inline | hecho — `core/state.py`, `tests/test_state.py` |
| T4 | S7,S8 | inline | hecho — `core/actuator.py`, `tests/test_actuator.py` |
| T5 | S2 | inline | hecho — `core/capture.py`, `tests/test_capture.py` |
| T6 | S1,S9 | inline | hecho — `app.py`, `web/`, `tests/test_web.py` |
| T7 | S10 | inline | hecho — 91 pruebas en verde |

## Log

**L1.** Pedido original (resumen; el texto íntegro está en la sesión): detectar maíz sobre la banda negra de ingreso, avisar al PLC con una señal digital sin modificar el PLC, Raspberry Pi 4 + OpenCV clásico, interfaz web en español, fail-safe obligatorio, desarrollo primero en el PC.

**L2.** «si, pero todavia no tengo videos pregrabado, eso lo vamos hacer mañana»

**Decisión L2-1:** el detector v0 se valida con frames sintéticos (banda negra, maíz amarillo, arroz gris) generados con NumPy. No se bloquea el avance por falta de video.

**Decisión L2-2:** el relé arranca desenergizado y solo se energiza tras procesar el primer frame válido. La línea permanece en pausa hasta que el sistema demuestre que ve.

**Evidencia L3 — pruebas.** `pytest`: **91 passed** en 2.7 s, sin cámara, sin GPIO y sin video.

**Evidencia L3 — punta a punta con video sintético.** `app.py` con `camera.source: file` y un clip negro → maíz → negro:

```
line RUNNING (contaminated=False area=0 px boxes=0)
line PAUSED  (contaminated=True  area=3481 px boxes=1)
line RUNNING (contaminated=False area=0 px boxes=0)
signal 15 received: pausing the line
capture thread stopped after 90 frames
```

`/api/state`, `/api/config` y `/` respondieron correctamente durante la corrida.

**Evidencia L3 — cámara real.** `python app.py --probe` abrió `/dev/video2` a 1280×720 @ 30 fps con la receta de `AGENTS.md` §6.

**Nota L3.** OpenCV instalado resultó ser **5.0.0**, no 4.x. La API usada (`inRange`, `findContours`, `morphologyEx`, `imencode`, `VideoWriter_fourcc`) se comporta igual. Verificado antes de escribir código.

## Log — verificación independiente (L4)

**Veredicto del verificador:** S1–S10 cumplidos, **0 bloqueantes**, 91 pruebas en verde.

**Dos hallazgos de seguridad corregidos en L4:**

1. **Pérdida de cámara dejaba el relé energizado.** Sin frames, el snapshot quedaba congelado en `ok=true` y la línea seguía avanzando a ciegas. Corregido con `safety.frame_timeout_s`: si no se analiza un frame en ese tiempo, `Runtime.force_pause()` pausa la línea. Verificado de punta a punta quitando el video a mitad de corrida.
2. **`SIGTERM` podía ser deshecho por el frame en vuelo.** El manejador desenergizaba, pero `process()` en curso volvía a energizar. Corregido con `Runtime.begin_shutdown()`, que marca un evento; `_command()` se niega a energizar después y deshace el comando si la señal llegó en la ventana de carrera.

**Correcciones menores:** el detector intercambia umbrales y kernel como una sola tupla (sin lectura rasgada), y se sustituyeron dos aserciones vacuas de `test_runtime.py`.

**Pruebas aportadas por el verificador y adoptadas:** `tests/test_app_failsafe.py` (nivel proceso: SIGTERM, error fatal, excepción del detector, fallo de apertura, pérdida de cámara), `tests/test_state_boundaries.py`, `tests/test_capture_bus.py`, más dos casos en `test_config.py` y uno en `test_web.py`.

**Resultado final:** **111 pruebas, 5 corridas consecutivas en verde**, estables.
