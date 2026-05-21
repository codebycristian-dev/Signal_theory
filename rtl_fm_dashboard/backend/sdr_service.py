import copy
import queue
import threading
import time
import traceback
from dataclasses import asdict

import numpy as np
from scipy import signal

from .audio import AudioPlayer
from .config import SDRConfig
from .dsp import de_emphasis_filter, fm_demod, welch_psd


class RTLReader(threading.Thread):
    def __init__(self, cfg: SDRConfig, iq_queue: queue.Queue, stop_event: threading.Event, error_callback):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.iq_queue = iq_queue
        self.stop_event = stop_event
        self.error_callback = error_callback
        self.sdr = None

    def run(self):
        try:
            from rtlsdr import RtlSdr

            self.sdr = RtlSdr()
            self.sdr.sample_rate = self.cfg.sample_rate
            self.sdr.center_freq = self.cfg.center_freq
            self.sdr.gain = self.cfg.gain

            while not self.stop_event.is_set():
                iq = self.sdr.read_samples(self.cfg.rf_block_size).astype(np.complex64)

                try:
                    self.iq_queue.put(iq, timeout=0.02)
                except queue.Full:
                    # Si el DSP se retrasa, descartamos bloque viejo para mantener tiempo real.
                    try:
                        self.iq_queue.get_nowait()
                        self.iq_queue.put_nowait(iq)
                    except queue.Empty:
                        pass

        except Exception as exc:
            self.error_callback(f"RTLReader: {exc}\n{traceback.format_exc()}")

        finally:
            if self.sdr is not None:
                try:
                    self.sdr.close()
                except Exception:
                    pass


class DSPWorker(threading.Thread):
    def __init__(
        self,
        cfg: SDRConfig,
        iq_queue: queue.Queue,
        audio_queue: queue.Queue,
        stop_event: threading.Event,
        psd_callback,
        level_callback,
        error_callback,
    ):
        super().__init__(daemon=True)

        self.cfg = cfg
        self.iq_queue = iq_queue
        self.audio_queue = audio_queue
        self.stop_event = stop_event
        self.psd_callback = psd_callback
        self.level_callback = level_callback
        self.error_callback = error_callback

        self.prev_sample = np.complex64(1 + 0j)
        self.last_psd_time = 0.0
        self.audio_fifo = np.zeros(0, dtype=np.float32)

        self.resamp_up, self.resamp_down = self.cfg.resample_ratio()

        self.rf_lpf_sos = signal.butter(
            6,
            self.cfg.rf_lpf_cutoff,
            btype="lowpass",
            fs=self.cfg.sample_rate,
            output="sos",
        )
        self.rf_lpf_zi = signal.sosfilt_zi(self.rf_lpf_sos).astype(np.complex64)

        self.de_b, self.de_a = de_emphasis_filter(self.cfg.audio_rate, self.cfg.deemphasis_tau)
        self.de_zi = signal.lfilter_zi(self.de_b, self.de_a).astype(np.float32) * 0.0

        self.audio_lpf_sos = signal.butter(
            5,
            self.cfg.audio_lpf_cutoff,
            btype="lowpass",
            fs=self.cfg.audio_rate,
            output="sos",
        )
        self.audio_lpf_zi = signal.sosfilt_zi(self.audio_lpf_sos).astype(np.float32) * 0.0

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    iq = self.iq_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                now = time.time()

                if now - self.last_psd_time >= (1.0 / self.cfg.psd_update_hz):
                    self.last_psd_time = now
                    psd_payload = welch_psd(
                        iq=iq,
                        fs=self.cfg.sample_rate,
                        center_freq=self.cfg.center_freq,
                        nperseg=self.cfg.psd_nperseg,
                        max_points=self.cfg.psd_max_points,
                    )
                    self.psd_callback(psd_payload)

                # Nivel RF para card del dashboard
                rf_rms = float(np.sqrt(np.mean(np.abs(iq) ** 2) + 1e-20))

                # Filtro RF antes del demodulador FM
                iq, self.rf_lpf_zi = signal.sosfilt(self.rf_lpf_sos, iq, zi=self.rf_lpf_zi)

                # Demodulación FM por fase diferencial
                demod, self.prev_sample = fm_demod(iq, self.prev_sample)

                # Remuestreo RF/audio
                audio = signal.resample_poly(
                    demod,
                    up=self.resamp_up,
                    down=self.resamp_down,
                    window=("kaiser", 8.0),
                ).astype(np.float32)

                # De-emphasis + LPF audio
                audio, self.de_zi = signal.lfilter(self.de_b, self.de_a, audio, zi=self.de_zi)
                audio, self.audio_lpf_zi = signal.sosfilt(
                    self.audio_lpf_sos, audio, zi=self.audio_lpf_zi
                )

                # Quitar DC residual por bloque
                audio = audio - np.mean(audio)

                # Ganancia y volumen
                audio = audio * self.cfg.audio_gain * self.cfg.volume

                # Limitador suave para evitar clipping duro
                audio = np.tanh(audio).astype(np.float32)

                audio_rms = float(np.sqrt(np.mean(audio**2) + 1e-20))
                self.level_callback(rf_rms=rf_rms, audio_rms=audio_rms)

                self.push_audio_fifo(audio)

        except Exception as exc:
            self.error_callback(f"DSPWorker: {exc}\n{traceback.format_exc()}")

    def push_audio_fifo(self, audio: np.ndarray) -> None:
        if len(audio) == 0:
            return

        self.audio_fifo = np.concatenate((self.audio_fifo, audio))
        block = self.cfg.audio_block_size

        while len(self.audio_fifo) >= block:
            chunk = self.audio_fifo[:block]
            self.audio_fifo = self.audio_fifo[block:]

            try:
                self.audio_queue.put_nowait(chunk)
            except queue.Full:
                # Mantener tiempo real: descarta audio viejo y conserva el más nuevo.
                try:
                    self.audio_queue.get_nowait()
                    self.audio_queue.put_nowait(chunk)
                except queue.Empty:
                    pass


class SDRService:
    """
    Orquestador único: mantiene RTL-SDR, DSP, audio, estado y último PSD.
    FastAPI no toca directamente la radio; llama este servicio.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = SDRConfig()
        self.stop_event = threading.Event()

        self.iq_queue = None
        self.audio_queue = None

        self.reader = None
        self.dsp = None
        self.audio = None

        self.running = False
        self.last_error = None
        self.last_psd = None
        self.rf_rms = 0.0
        self.audio_rms = 0.0
        self.started_at = None

    def start(self, cfg_update: dict | None = None) -> dict:
        with self.lock:
            if self.running:
                return self.status()

            if cfg_update:
                self._apply_config_update(cfg_update)

            self.cfg.sanitize()

            self.stop_event = threading.Event()
            self.iq_queue = queue.Queue(maxsize=self.cfg.iq_queue_max)
            self.audio_queue = queue.Queue(maxsize=self.cfg.audio_queue_max)

            self.reader = RTLReader(
                cfg=copy.deepcopy(self.cfg),
                iq_queue=self.iq_queue,
                stop_event=self.stop_event,
                error_callback=self._set_error,
            )

            self.dsp = DSPWorker(
                cfg=copy.deepcopy(self.cfg),
                iq_queue=self.iq_queue,
                audio_queue=self.audio_queue,
                stop_event=self.stop_event,
                psd_callback=self._set_psd,
                level_callback=self._set_levels,
                error_callback=self._set_error,
            )

            self.audio = AudioPlayer(self.cfg, self.audio_queue)

            self.reader.start()
            self.dsp.start()
            self.audio.start()

            self.running = True
            self.started_at = time.time()
            self.last_error = None

            return self.status()

    def stop(self) -> dict:
        with self.lock:
            self.stop_event.set()

            if self.audio is not None:
                try:
                    self.audio.stop()
                except Exception as exc:
                    self._set_error(f"Audio stop: {exc}")

            self.running = False

            # No se bloquea indefinidamente esperando hilos: son daemon.
            self.reader = None
            self.dsp = None
            self.audio = None

            return self.status()

    def restart(self, cfg_update: dict | None = None) -> dict:
        self.stop()
        time.sleep(0.25)
        return self.start(cfg_update=cfg_update)

    def configure(self, cfg_update: dict) -> dict:
        with self.lock:
            was_running = self.running

        if was_running:
            return self.restart(cfg_update)

        with self.lock:
            self._apply_config_update(cfg_update)
            self.cfg.sanitize()
            return self.status()

    def status(self) -> dict:
        with self.lock:
            iq_size = self.iq_queue.qsize() if self.iq_queue is not None else 0
            audio_size = self.audio_queue.qsize() if self.audio_queue is not None else 0
            underflows = self.audio.underflows if self.audio is not None else 0
            uptime = time.time() - self.started_at if self.started_at and self.running else 0.0

            return {
                "running": self.running,
                "config": self.cfg.as_dict(),
                "queues": {
                    "iq": iq_size,
                    "iq_max": self.cfg.iq_queue_max,
                    "audio": audio_size,
                    "audio_max": self.cfg.audio_queue_max,
                },
                "levels": {
                    "rf_rms": self.rf_rms,
                    "audio_rms": self.audio_rms,
                },
                "underflows": underflows,
                "uptime_s": uptime,
                "last_error": self.last_error,
            }

    def get_latest_psd(self) -> dict | None:
        with self.lock:
            return copy.deepcopy(self.last_psd)

    def _apply_config_update(self, cfg_update: dict) -> None:
        allowed = set(asdict(self.cfg).keys())

        for key, value in cfg_update.items():
            if key in allowed and value is not None:
                setattr(self.cfg, key, value)

    def _set_psd(self, payload: dict) -> None:
        with self.lock:
            self.last_psd = payload

    def _set_levels(self, rf_rms: float, audio_rms: float) -> None:
        with self.lock:
            self.rf_rms = rf_rms
            self.audio_rms = audio_rms

    def _set_error(self, message: str) -> None:
        with self.lock:
            self.last_error = message
            self.running = False
            self.stop_event.set()
