import time
from typing import Any

import numpy as np
from scipy import signal


def fm_demod(iq: np.ndarray, prev_sample: complex) -> tuple[np.ndarray, complex]:
    """
    Demodulación FM por diferencia de fase:
        y[n] = angle(x[n] * conj(x[n-1]))
    Mantiene continuidad de fase entre bloques usando prev_sample.
    """
    if len(iq) == 0:
        return np.zeros(0, dtype=np.float32), prev_sample

    x = np.empty(len(iq) + 1, dtype=np.complex64)
    x[0] = np.complex64(prev_sample)
    x[1:] = iq.astype(np.complex64, copy=False)

    demod = np.angle(x[1:] * np.conj(x[:-1])).astype(np.float32)
    return demod, complex(iq[-1])


def de_emphasis_filter(fs: float, tau: float = 75e-6) -> tuple[list[float], list[float]]:
    """
    Filtro IIR de primer orden para de-emphasis.
    En Colombia/Américas normalmente se usa 75 us para FM comercial.
    """
    dt = 1.0 / fs
    alpha = dt / (tau + dt)
    b = [alpha]
    a = [1.0, alpha - 1.0]
    return b, a


def welch_psd(
    iq: np.ndarray,
    fs: float,
    center_freq: float,
    nperseg: int,
    max_points: int,
) -> dict[str, Any]:
    freqs, psd = signal.welch(
        iq,
        fs=fs,
        nperseg=min(nperseg, len(iq)),
        return_onesided=False,
        scaling="density",
    )

    freqs = np.fft.fftshift(freqs) + center_freq
    psd = np.fft.fftshift(psd)
    psd_db = 10.0 * np.log10(psd + 1e-20)

    # Downsample visual: conserva la forma sin saturar el WebSocket.
    if len(freqs) > max_points:
        idx = np.linspace(0, len(freqs) - 1, max_points).astype(np.int64)
        freqs_view = freqs[idx]
        psd_view = psd_db[idx]
    else:
        freqs_view = freqs
        psd_view = psd_db

    metrics = estimate_psd_metrics(freqs, psd_db, center_freq)

    return {
        "timestamp": time.time(),
        "freq_mhz": freqs_view.astype(np.float64).tolist(),
        "psd_db": psd_view.astype(np.float32).tolist(),
        "metrics": metrics,
    }


def estimate_psd_metrics(freqs_hz: np.ndarray, psd_db: np.ndarray, center_freq: float) -> dict[str, float | None]:
    """
    Métricas rápidas para el dashboard:
    - frecuencia pico
    - offset respecto al centro
    - nivel pico
    - piso aproximado por percentil
    - ancho ocupado aproximado a -20 dB del pico en la región conectada al pico
    """
    if len(freqs_hz) == 0 or len(psd_db) == 0:
        return {
            "peak_mhz": None,
            "offset_khz": None,
            "peak_db": None,
            "noise_floor_db": None,
            "bw20_khz": None,
        }

    finite = np.isfinite(psd_db)
    if not np.any(finite):
        return {
            "peak_mhz": None,
            "offset_khz": None,
            "peak_db": None,
            "noise_floor_db": None,
            "bw20_khz": None,
        }

    safe_psd = np.where(finite, psd_db, -300.0)
    peak_idx = int(np.argmax(safe_psd))
    peak_db = float(safe_psd[peak_idx])
    peak_freq = float(freqs_hz[peak_idx])

    noise_floor = float(np.percentile(safe_psd[finite], 20))
    threshold = peak_db - 20.0

    above = safe_psd >= threshold
    left = peak_idx
    right = peak_idx

    while left > 0 and above[left - 1]:
        left -= 1
    while right < len(above) - 1 and above[right + 1]:
        right += 1

    bw20_khz = float((freqs_hz[right] - freqs_hz[left]) / 1e3) if right > left else 0.0

    return {
        "peak_mhz": peak_freq / 1e6,
        "offset_khz": (peak_freq - center_freq) / 1e3,
        "peak_db": peak_db,
        "noise_floor_db": noise_floor,
        "bw20_khz": bw20_khz,
    }
