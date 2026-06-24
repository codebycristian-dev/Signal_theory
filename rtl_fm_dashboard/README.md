# RTL-SDR FM Dashboard local

Arquitectura separada y escalable:

```text
rtl_fm_dashboard/
├── backend/
│   ├── main.py          # API FastAPI + WebSocket PSD
│   ├── sdr_service.py   # orquestador RTL-SDR, DSP, audio y estado
│   ├── dsp.py           # demod FM, de-emphasis, Welch PSD, métricas
│   ├── audio.py         # sounddevice OutputStream
│   ├── config.py        # configuración centralizada
│   └── requirements.txt
└── frontend/
    ├── index.html       # dashboard web
    ├── styles.css       # UI moderna
    └── app.js           # REST + WebSocket + gráfica canvas
```

## 1. Instalar backend

Desde la carpeta raíz del proyecto:

```bash
python -m venv .venv
```

En Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

En Git Bash:

```bash
source .venv/Scripts/activate
pip install -r backend/requirements.txt
```

## 2. Ejecutar backend

Desde la carpeta raíz:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Prueba rápida:

```text
http://127.0.0.1:8000/api/health
http://127.0.0.1:8000/docs
```

## 3. Ejecutar frontend

En otra terminal:

```bash
cd frontend
python -m http.server 5173
```

Abrir en el navegador:

```text
http://127.0.0.1:5173
```

## 4. Flujo interno

```text
RTL-SDR → IQ queue → RF LPF → demod FM → resample 250k→48k
        → de-emphasis → LPF audio → audio queue → sounddevice

IQ block → Welch PSD → métricas → WebSocket → dashboard
```

## 5. Notas importantes

- El frontend no toca el RTL-SDR.
- El backend es el único dueño del dispositivo.
- Si cambias frecuencia, ganancia o sample rate con el receptor activo, el backend reinicia el servicio de forma controlada.
- La PSD se envía por WebSocket cada 0.2 s aproximadamente.
- El audio sale por el dispositivo de audio del computador donde corre el backend.

## 6. Localizacion espectral por consola

El script `backend/spectral_scan.py` captura IQ desde la RTL-SDR/Noelec Smart y estima:

- localizacion espectral de cada senal detectada
- frecuencia central por centroide de potencia
- ancho de banda CFAR y ancho ocupado 99%
- potencia instantanea y promedio relativo en dBFS
- SNR aproximado contra el piso local

El algoritmo usa Welch + CFAR + waterfall + agrupamiento de bins contiguos:

```powershell
python -m backend.spectral_scan --center-freq 105.7M --sample-rate 250k --gain 35 --duration 10
```

Salida en JSONL:

```powershell
python -m backend.spectral_scan --center-freq 105.7M --sample-rate 250k --jsonl mediciones.jsonl
```

Guardar waterfall final:

```powershell
python -m backend.spectral_scan --center-freq 105.7M --sample-rate 250k --waterfall-npz waterfall.npz
```

Opciones utiles:

- `--cfar-threshold-db`: sensibilidad de deteccion sobre el piso local.
- `--cfar-train` y `--cfar-guard`: celdas de entrenamiento y guarda.
- `--min-bins`: ancho minimo en bins para aceptar una senal.
- `--min-persistence`: votos minimos en el waterfall para filtrar detecciones transitorias.
- `--dc-notch-hz`: ignora detecciones alrededor de la frecuencia sintonizada si aparece espurio DC.

Las potencias son relativas al flujo IQ digital (`dBFS_relative`), no dBm calibrado.
