"""
rtl_sweep_pro.core.sdr_controller
=================================

Thin abstraction over :mod:`pyrtlsdr` so the rest of the code base never
imports it directly. If ``pyrtlsdr`` is unavailable (or no device is
attached) a :class:`MockSDR` is returned that produces deterministic but
realistic synthetic IQ data — broadband noise floor plus a few configurable
carriers — so the GUI, sweep engine and exporters can be developed and
tested without hardware.

The two implementations share the same minimal interface:

* :meth:`open` / :meth:`close`
* :meth:`set_sample_rate`
* :meth:`set_center_freq`
* :meth:`set_gain`
* :meth:`set_freq_correction`
* :meth:`set_agc`
* :meth:`read_samples`        → ``np.ndarray[complex64]`` in ±1.0
* :attr:`is_mock`             → ``True`` for the synthetic backend
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from rtlsdr import RtlSdr  # type: ignore
    _HAS_PYRTLSDR = True
except Exception:  # pragma: no cover — optional dependency
    _HAS_PYRTLSDR = False


# --------------------------------------------------------------------------- #
# Public abstract interface
# --------------------------------------------------------------------------- #

class SDRBase:
    is_mock: bool = False

    def open(self) -> None: ...
    def close(self) -> None: ...
    def set_sample_rate(self, fs: float) -> None: ...
    def set_center_freq(self, fc: float) -> None: ...
    def set_gain(self, gain_db: float) -> None: ...
    def set_freq_correction(self, ppm: int) -> None: ...
    def set_agc(self, enabled: bool) -> None: ...
    def read_samples(self, n: int) -> np.ndarray: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# --------------------------------------------------------------------------- #
# Real RTL-SDR
# --------------------------------------------------------------------------- #

class RealSDR(SDRBase):
    """Wrapper around :class:`rtlsdr.RtlSdr`."""

    is_mock = False

    def __init__(self, device_index: int = 0) -> None:
        if not _HAS_PYRTLSDR:
            raise RuntimeError("pyrtlsdr is not installed.")
        self._index = device_index
        self._dev: Optional["RtlSdr"] = None

    def open(self) -> None:
        if self._dev is None:
            self._dev = RtlSdr(device_index=self._index)
            logger.info("Opened RTL-SDR device %d", self._index)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None
            logger.info("Closed RTL-SDR device.")

    def _require(self) -> "RtlSdr":
        if self._dev is None:
            raise RuntimeError("SDR not opened.")
        return self._dev

    def set_sample_rate(self, fs: float) -> None:
        self._require().sample_rate = float(fs)

    def set_center_freq(self, fc: float) -> None:
        self._require().center_freq = float(fc)

    def set_gain(self, gain_db: float) -> None:
        self._require().gain = float(gain_db)

    def set_freq_correction(self, ppm: int) -> None:
        if ppm == 0:
            return
        try:
            self._require().freq_correction = int(ppm)
        except Exception as e:
            logger.warning("freq_correction(%d) failed: %s", ppm, e)

    def set_agc(self, enabled: bool) -> None:
        try:
            self._require().set_agc_mode(bool(enabled))
        except Exception as e:
            logger.warning("set_agc_mode failed: %s", e)

    def read_samples(self, n: int) -> np.ndarray:
        # pyrtlsdr returns float64 complex in ±1 already.
        s = self._require().read_samples(int(n))
        return np.asarray(s, dtype=np.complex64)


# --------------------------------------------------------------------------- #
# Mock SDR — used when pyrtlsdr or hardware are unavailable
# --------------------------------------------------------------------------- #

class MockSDR(SDRBase):
    """Synthetic IQ generator that mimics the RTL-SDR pipeline.

    The synthetic spectrum contains:

    * complex AWGN noise floor at a configurable level,
    * a small DC spike (typical of zero-IF receivers),
    * a deterministic set of carriers expressed in *absolute RF frequency*
      so they appear at the right place during a sweep,
    * a slow wandering weak carrier to test detection.

    The class is deterministic given the same seed so tests reproduce.
    """

    is_mock = True

    def __init__(
        self,
        seed: int = 42,
        noise_floor_dbfs: float = -75.0,
        carriers: Optional[list[tuple[float, float]]] = None,
    ) -> None:
        # carriers: list of (frequency_hz, level_dbfs)
        self._rng = np.random.default_rng(seed)
        self._fs = 2.4e6
        self._fc = 100e6
        self._gain_db = 30.0
        self._noise = noise_floor_dbfs
        self._t0 = 0.0
        self._carriers = carriers or [
            (95.0e6, -40.0),
            (97.3e6, -35.0),
            (101.7e6, -25.0),
            (103.5e6, -50.0),
            (433.92e6, -30.0),
            (446.0e6, -55.0),
            (868.3e6, -45.0),
            (915.0e6, -40.0),
        ]

    # --- interface ----------------------------------------------------- #
    def open(self) -> None:
        logger.info("Opened MockSDR (no real hardware).")

    def close(self) -> None:
        logger.info("Closed MockSDR.")

    def set_sample_rate(self, fs: float) -> None:
        self._fs = float(fs)

    def set_center_freq(self, fc: float) -> None:
        self._fc = float(fc)

    def set_gain(self, gain_db: float) -> None:
        self._gain_db = float(gain_db)

    def set_freq_correction(self, ppm: int) -> None:
        pass

    def set_agc(self, enabled: bool) -> None:
        pass

    # --- generator ----------------------------------------------------- #
    def read_samples(self, n: int) -> np.ndarray:
        n = int(n)
        # Simulate USB transfer time so the engine actually sees latency.
        time.sleep(min(0.1, n / self._fs))

        # Noise floor in linear amplitude (per-sample sigma): converting a
        # PSD level (dBFS/Hz) ≈ noise_floor_dbfs - 10·log10(fs) into per-sample
        # variance. Approximate: σ² ≈ 10^(noise_floor_dbfs/10)
        sigma = 10 ** (self._noise / 20.0)
        iq = (
            self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)
        ) * (sigma / np.sqrt(2.0))

        t = (np.arange(n) + self._t0) / self._fs
        self._t0 += n

        # DC spike
        iq += 0.001 + 0.0005j

        # Static carriers — appear only if within fs/2 of fc
        for f_rf, level_dbfs in self._carriers:
            df = f_rf - self._fc
            if abs(df) < 0.45 * self._fs:  # inside passband
                amp = 10 ** (level_dbfs / 20.0)
                iq += amp * np.exp(1j * 2 * np.pi * df * t)

        # Wandering weak signal: sweeps slowly across a narrow band
        wander = 50e3 * np.sin(2 * np.pi * 0.05 * t[0])
        f_wander = self._fc + wander  # always near fc, so always visible
        df_w = f_wander - self._fc
        amp_w = 10 ** (-65.0 / 20.0)
        iq += amp_w * np.exp(1j * 2 * np.pi * df_w * t)

        return iq.astype(np.complex64)


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def open_sdr(
    prefer_mock: bool = False,
    device_index: int = 0,
) -> SDRBase:
    """Return a real SDR if possible, else the mock backend.

    ``prefer_mock=True`` forces the mock — useful for unit tests and demos.
    """
    if prefer_mock or not _HAS_PYRTLSDR:
        if not _HAS_PYRTLSDR and not prefer_mock:
            logger.warning("pyrtlsdr not installed — using MockSDR.")
        return MockSDR()
    try:
        sdr = RealSDR(device_index=device_index)
        sdr.open()
        return sdr
    except Exception as e:  # device missing, USB error…
        logger.warning("Could not open RTL-SDR (%s) — using MockSDR.", e)
        return MockSDR()
