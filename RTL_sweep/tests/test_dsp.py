"""
Unit tests for rtl_sweep_pro.core.dsp

Validates:
* Window correction factors against known closed-form values.
* PSD scaling: a unit-amplitude complex tone produces the expected total
  power within the FFT bin nearest to its frequency.
* Welch averaging: variance of the noise PSD scales like 1/K_eff.
* The peak detector picks up a strong tone at the right frequency.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from rtl_sweep_pro.core.dsp import (
    make_window, welch_psd_complex, window_correction_factors,
)
from rtl_sweep_pro.core.peak_detector import detect_peaks


# --------------------------------------------------------------------------- #
# Window factors
# --------------------------------------------------------------------------- #

def test_rect_window_factors():
    w = np.ones(1024)
    cg, nenbw = window_correction_factors(w)
    assert cg == pytest.approx(1.0)
    assert nenbw == pytest.approx(1.0)


def test_hann_window_nenbw_known():
    # Hann NENBW ≈ 1.5 bins
    w = make_window("hann", 4096)
    _, nenbw = window_correction_factors(w)
    assert nenbw == pytest.approx(1.5, rel=2e-3)


def test_blackmanharris_nenbw_known():
    # Blackman-Harris NENBW ≈ 2.0044 bins
    w = make_window("blackmanharris", 4096)
    _, nenbw = window_correction_factors(w)
    assert nenbw == pytest.approx(2.0044, rel=5e-3)


# --------------------------------------------------------------------------- #
# Tone power
# --------------------------------------------------------------------------- #

def test_tone_power_localised_in_correct_bin():
    fs = 1.0e6
    n = 4096
    f0 = 100e3                  # 100 kHz tone, well inside passband
    t = np.arange(n) / fs
    iq = (1.0 * np.exp(1j * 2 * np.pi * f0 * t)).astype(np.complex64)

    freqs, psd_db, meta = welch_psd_complex(
        iq=iq, fs=fs, fft_size=n, n_averages=1,
        overlap_fraction=0.0, window_name="blackmanharris",
    )

    peak_idx = int(np.argmax(psd_db))
    f_peak = float(freqs[peak_idx])
    bin_hz = fs / n
    assert abs(f_peak - f0) <= 1.5 * bin_hz, f"peak at {f_peak}, expected {f0}"

    # The total integrated power around the peak should equal ~1 W (unit-amp
    # complex tone has unit power). Integrate ±5 bins to cover window leakage.
    psd_lin = np.power(10.0, psd_db / 10.0)
    df = bin_hz
    total_p = np.sum(psd_lin[peak_idx - 5 : peak_idx + 6]) * df
    assert 0.5 < total_p < 2.0, f"integrated power = {total_p}"


# --------------------------------------------------------------------------- #
# Welch averaging variance reduction
# --------------------------------------------------------------------------- #

def test_welch_reduces_noise_variance():
    fs = 1.0e6
    n = 1024
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(n * 64) + 1j * rng.standard_normal(n * 64)) / np.sqrt(2)
    iq = iq.astype(np.complex64)

    _, psd1, _ = welch_psd_complex(iq, fs, n, 1, 0.0, "hann")
    _, psd64, _ = welch_psd_complex(iq, fs, n, 64, 0.5, "hann")

    # Standard deviation of the PSD trace (in dB) must shrink with averaging.
    s1 = float(np.std(psd1))
    s64 = float(np.std(psd64))
    assert s64 < 0.5 * s1, f"variance not reduced: s1={s1:.2f} s64={s64:.2f}"


# --------------------------------------------------------------------------- #
# Peak detector
# --------------------------------------------------------------------------- #

def test_peak_detector_finds_strong_tone():
    fs = 1.0e6
    n = 4096
    f0 = 200e3
    t = np.arange(n) / fs
    iq = (np.exp(1j * 2 * np.pi * f0 * t)
          + 0.001 * (np.random.default_rng(1).standard_normal(n)
                     + 1j * np.random.default_rng(2).standard_normal(n))).astype(np.complex64)
    freqs, psd_db, _ = welch_psd_complex(
        iq=iq, fs=fs, fft_size=n, n_averages=1,
        overlap_fraction=0.0, window_name="blackmanharris",
    )
    peaks = detect_peaks(freqs, psd_db, threshold_db=-80, min_prominence_db=10)
    assert len(peaks) >= 1
    # Strongest peak must be near f0.
    strongest = max(peaks, key=lambda p: p.level_db)
    assert abs(strongest.frequency_hz - f0) < 2 * (fs / n)


# --------------------------------------------------------------------------- #
# Capture-length helper round-trip
# --------------------------------------------------------------------------- #

def test_samples_per_capture_consistency():
    from rtl_sweep_pro.config import SweepConfig
    cfg = SweepConfig(fft_size=4096, n_averages=8, overlap_fraction=0.5)
    n = cfg.samples_per_capture
    # n must allow exactly 8 blocks of 4096 with 50% overlap
    hop = cfg.fft_size // 2
    blocks = (n - cfg.fft_size) // hop + 1
    assert blocks == cfg.n_averages
