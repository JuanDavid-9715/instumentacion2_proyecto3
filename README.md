# Detección de material no deseado en línea de llenado de arroz

Sistema de visión artificial que identifica maíz y otros elementos ajenos al arroz sobre la banda transportadora de ingreso, y pausa la línea avisando al PLC.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-4-A22846?logo=raspberrypi&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-5C3EE8?logo=opencv&logoColor=white)
![Estado](https://img.shields.io/badge/estado-en%20desarrollo-yellow)

Proyecto académico · Instrumentación Industrial II · Ingeniería Electrónica · Universidad ECCI

---

## El problema

La máquina llena cajas de arroz de forma automática: una banda de ingreso transporta el material, una tolva lo dosifica y un servomotor corta el llenado. Todo lo controla un PLC.

El proceso no distingue entre arroz y maíz. Si un grano de maíz entra a la línea, termina dentro de una caja.

Este proyecto agrega un sensor que el PLC no tenía: **una cámara**. Cuando la cámara detecta material no deseado sobre la banda de ingreso, la Raspberry Pi desenergiza un relé y el PLC pausa la línea. Nada más. **El PLC no se modifica.**

---

## Cómo funciona

```
┌──────────┐     ┌──────────────────┐     ┌──────────────┐     ┌──────────┐
│ Cámara   │────▶│ Raspberry Pi 4   │────▶│ Relé (NC)    │────▶│ PLC      │
│ USB      │     │ captura+detector │     │ energizado=OK│     │ pausa    │
└──────────┘     └──────────────────┘     └──────────────┘     └──────────┘
                          │
                          ▼
                  ┌──────────────────┐
                  │ Interfaz web     │
                  │ video + estado   │
                  └──────────────────┘
```

El detector aprovecha una ventaja del montaje: **la banda es negra**. Cualquier objeto claro resalta por contraste. El arroz es blanco y de baja saturación; el maíz es amarillo, saturado y de mayor tamaño. Con segmentación en espacio HSV y filtrado por área se separan sin necesidad de entrenar un modelo.

---

## Seguridad por diseño

La polaridad está elegida para que **todo falle del lado seguro**:

| Situación | Relé | Entrada del PLC | Línea |
|---|---|---|---|
| Arroz limpio | energizado | 0 V | corre |
| Maíz detectado | desenergizado | 24 V | **pausa** |
| Raspberry apagada o sin energía | desenergizado | 24 V | **pausa** |
| Proceso terminado o con excepción | desenergizado | 24 V | **pausa** |

Usar el contacto **NC** y mantener la bobina energizada mientras el sistema está sano convierte la pérdida de energía o un fallo de software en una condición segura, sin hardware adicional.

---

## Hardware

| Elemento | Detalle |
|---|---|
| Cómputo | Raspberry Pi 4 |
| Sensor | Cámara USB UVC 1280×720 @ 30 fps |
| Interfaz | Módulo de relé, contacto NC |
| Controlador | PLC FX3U-24MT (sin modificaciones) |
| Iluminación | Luz fija y difusa sobre la banda |

---

## Estructura del repositorio

```
.
├── app.py               Punto de entrada: arranca captura, detector y web
├── config.yaml          Umbrales, ROI, polaridad y dispositivo de video
├── core/
│   ├── capture.py       Hilo único dueño del frame
│   ├── detector.py      Detección de material no deseado
│   ├── state.py         Máquina de estados con histéresis
│   └── actuator.py      Salida al relé, con fail-safe
├── web/
│   ├── server.py        API y stream MJPEG (FastAPI)
│   └── static/          Interfaz en HTML, CSS y JS plano
├── tests/
├── AGENTS.md            Reglas de trabajo para agentes de IA
├── MEMORY.md            Estado, decisiones y bitácora del proyecto
└── requirements.txt
```

---

## Requisitos

- Python 3.11 o superior
- Raspberry Pi OS de 64 bits (en la Raspberry)
- Una cámara UVC; en Linux aparece como `/dev/videoN`

---

## Instalación

```bash
git clone https://github.com/<usuario>/<repo>.git
cd <repo>

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Uso

```bash
python app.py
```

La interfaz queda disponible en:

```
http://<ip-de-la-pi>:8000
```

Desde ahí se ve el video en vivo, el estado actual del detector y el último evento registrado.

---

## Estado del proyecto

- [x] Definición de arquitectura, stack y polaridad
- [x] Validación de las capacidades reales de la cámara
- [ ] Captura y detector sobre video pregrabado
- [ ] Verificación eléctrica del relé contra el PLC
- [ ] Integración extremo a extremo
- [ ] Interfaz web
- [ ] Ajuste de falsos positivos con producto real

---

## Documentación

- [`AGENTS.md`](AGENTS.md) — arquitectura, comandos, reglas y prohibiciones
- [`MEMORY.md`](MEMORY.md) — estado, decisiones justificadas, hardware medido y pendientes

---

## Notas

El PLC y su programa no forman parte de este repositorio y no se modifican. El sistema solo entrega una señal digital por una entrada existente.
