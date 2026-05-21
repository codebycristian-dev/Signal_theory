import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .sdr_service import SDRService


class ConfigUpdate(BaseModel):
    center_freq: Optional[float] = Field(default=None, description="Hz")
    sample_rate: Optional[int] = None
    gain: Optional[float] = None
    rf_block_size: Optional[int] = None
    audio_rate: Optional[int] = None
    audio_block_size: Optional[int] = None
    volume: Optional[float] = None
    audio_gain: Optional[float] = None
    rf_lpf_cutoff: Optional[float] = None
    audio_lpf_cutoff: Optional[float] = None
    deemphasis_tau: Optional[float] = None
    psd_nperseg: Optional[int] = None
    psd_update_hz: Optional[float] = None
    psd_max_points: Optional[int] = None


app = FastAPI(
    title="RTL-SDR FM Dashboard Backend",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = SDRService()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return service.status()


@app.post("/api/start")
def start(cfg: ConfigUpdate | None = None):
    update = cfg.model_dump(exclude_none=True) if cfg else None
    return service.start(update)


@app.post("/api/stop")
def stop():
    return service.stop()


@app.post("/api/config")
def configure(cfg: ConfigUpdate):
    return service.configure(cfg.model_dump(exclude_none=True))


@app.websocket("/ws/psd")
async def ws_psd(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            psd = service.get_latest_psd()
            status_payload = service.status()

            await websocket.send_json(
                {
                    "type": "psd",
                    "running": status_payload["running"],
                    "status": status_payload,
                    "data": psd,
                }
            )

            await asyncio.sleep(0.20)

    except WebSocketDisconnect:
        return


@app.on_event("shutdown")
def shutdown():
    service.stop()
