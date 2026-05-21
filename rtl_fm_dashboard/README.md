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
