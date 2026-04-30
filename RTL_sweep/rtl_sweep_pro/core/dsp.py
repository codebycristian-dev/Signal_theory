"""
rtl_sweep_pro.core.dsp
======================

Digital signal processing primitives used by the sweep engine.

The conventions used throughout this module:

* IQ samples are complex64 (`numpy.complex64`) and already DC-corrected and
  normalized to ±1.0 (the :class:`SDRController` does this).
* All FFTs are complex (no real-FFT shortcuts because IQ is complex).
* Power Spectral Densities are returned in **dB(W/Hz)** referenced to
  full-scale; conversion to dBm is done in :mod:`calibration` with a single
  user-supplied offset.

The module exposes:

* :func:`window_correction_factors`
* :func:`welch_psd_complex`   — averaged PSD for one capture
* :class:`SegmentResult`      — value object returned by the engine
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import get_window


# --------------------------------------------------------------------------- #
# Window analysis
# --------------------------------------------------------------------------- #

def window_correction_factors(window: np.ndarray) -> tuple[float, float]:
    """Return ``(coherent_gain, NENBW_bins)`` for ``window``.

    * **Coherent gain (CG)**: ``mean(w)``. Used to correct amplitude of
      coherent (CW) tones.
    * **Noise Equivalent Noise Bandwidth (NENBW)** in **bins**:
      ``N · sum(w²) / (sum(w))²``. Used to correct random noise PSD.

    For a rectangular window both equal 1.
    """
    w = np.asarray(window, dtype=np.float64)
    n = w.size
    s1 = w.sum()
    s2 = (w * w).sum()
    if s1 == 0:
        raise ValueError("Window has zero coherent gain.")
    coherent_gain = s1 / n
    nenbw_bins = n * s2 / (s1 * s1)
    return float(coherent_gain), float(nenbw_bins)


def make_window(name: str, n: int) -> np.ndarray:
    """Return a window of length ``n``. Kaiser uses β=8.6 (≈ Blackman-Harris)."""
    if name == "kaiser":
        w = get_window(("kaiser", 8.6), n, fftbins=True)
    else:
        w = get_window(name, n, fftbins=True)
    return w.astype(np.float64)


# --------------------------------------------------------------------------- #
# Welch-style averaged PSD for complex IQ
# --------------------------------------------------------------------------- #

def welch_psd_complex(
    iq: np.ndarray,
    fs: float,
    fft_size: int,
    n_averages: int,
    overlap_fraction: float,
    window_name: str,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return ``(freqs_baseband, psd_db, meta)``.

    ``freqs_baseband`` is in Hz, *centered on 0* (i.e. it must be added to
    ``fc`` outside this function to obtain absolute RF frequency).

    ``psd_db`` is in dB referenced to full-scale per Hz: ``10·log10(P/Hz)``.
    ``meta`` contains diagnostic numbers (``rbw_hz``, ``nenbw_bins``,
    ``n_blocks_used``, ``coherent_gain``).

    Implementation notes
    --------------------
    * Uses **frequency-domain shift** (``np.fft.fftshift``) so bin 0 sits in
      the middle of the array — convenient for stitching.
    * Power normalization uses the *NENBW correction* so that the result is
      a true PSD (W/Hz), not an energy spectrum.
    * Average is computed in **linear power** (variance reduction property of
      Welch) and converted to dB at the end.
    * If the capture is shorter than what ``n_averages`` requires, the
      function silently uses fewer blocks and reports it in ``meta``.
    """
    if iq.ndim != 1:
        raise ValueError("iq must be a 1-D complex array.")
    if not np.iscomplexobj(iq):
        raise ValueError("iq must be complex.")

    n = int(fft_size)
    hop = max(1, int(n * (1.0 - overlap_fraction)))
    n_avail = (len(iq) - n) // hop + 1 if len(iq) >= n else 0
    k = max(1, min(int(n_averages), n_avail))

    if k == 0:
        raise ValueError(
            f"Capture too short: need ≥ {n} samples, got {len(iq)}."
        )

    win = make_window(window_name, n)
    cg, nenbw_bins = window_correction_factors(win)
    rbw_hz = nenbw_bins * fs / n  # effective noise bandwidth per bin

    # Pre-compute the constant denominator for PSD scaling.
    # P_psd = |X|² / (fs · sum(w²))      [W/Hz, single-sided not applicable for complex]
    norm = fs * (win * win).sum()

    psd_accum = np.zeros(n, dtype=np.float64)
    for i in range(k):
        start = i * hop
        block = iq[start : start + n]
        x = np.fft.fft(block * win, n=n)
        psd_accum += (x.real * x.real + x.imag * x.imag) / norm

    psd_lin = psd_accum / k                       # average in linear power
    psd_lin = np.fft.fftshift(psd_lin)            # DC at center
    # 1e-30 floor avoids log10(0) without altering meaningful values.
    psd_db = 10.0 * np.log10(psd_lin + 1e-30)

    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / fs))

    meta = {
        "rbw_hz": float(rbw_hz),
        "nenbw_bins": float(nenbw_bins),
        "coherent_gain": float(cg),
        "n_blocks_used": int(k),
        "n_blocks_requested": int(n_averages),
        "fft_size": int(n),
        "overlap_fraction": float(overlap_fraction),
        "window": window_name,
    }
    return freqs.astype(np.float64), psd_db.astype(np.float64), meta


# --------------------------------------------------------------------------- #
# Segment value object
# --------------------------------------------------------------------------- #

@dataclass
class SegmentResult:
    """One processed capture for a single center frequency."""
    fc_hz: float
    freqs_hz: np.ndarray         # absolute RF frequencies (already fc + baseband)
    psd_db: np.ndarray           # dB(W/Hz), uncalibrated (dBFS-referenced)
    rbw_hz: float
    nenbw_bins: float
    n_blocks_used: int
    timestamp_unix: float
