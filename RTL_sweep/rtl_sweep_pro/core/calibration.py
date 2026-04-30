"""
rtl_sweep_pro.core.calibration
==============================

PSD calibration utilities.

The RTL-SDR has no absolute power calibration. Out of the engine, PSD is in
**dB(W/Hz)** referenced to full-scale of the 8-bit ADC. To compare against a
real power meter or signal generator the user must measure a known carrier
of known power and compute an offset. This module provides:

* :func:`apply_offset` — single offset (linear correction)
* :func:`solve_offset_from_reference` — least-squares fit between a reference
  level (in dBm) and a measured peak level (in dBFS).

These keep calibration explicit: there is no hidden "magic factor".
"""

from __future__ import annotations

import numpy as np


def apply_offset(psd_db: np.ndarray, offset_db: float) -> np.ndarray:
    """Return ``psd_db + offset_db`` (in dB)."""
    return psd_db + float(offset_db)


def solve_offset_from_reference(
    reference_dbm: float,
    measured_dbfs: float,
) -> float:
    """Compute the calibration offset.

    Given a known reference signal at ``reference_dbm`` whose measured peak
    level in the calibrated PSD is ``measured_dbfs``, the offset that makes
    the engine read dBm is::

        offset_db = reference_dbm - measured_dbfs

    The user is expected to use a properly attenuated, well-known carrier
    (signal generator, calibrated noise source).
    """
    return float(reference_dbm) - float(measured_dbfs)


def dbm_to_watts(dbm: float | np.ndarray) -> float | np.ndarray:
    return 1e-3 * np.power(10.0, np.asarray(dbm) / 10.0)


def watts_to_dbm(p: float | np.ndarray) -> float | np.ndarray:
    p = np.maximum(np.asarray(p), 1e-30)
    return 10.0 * np.log10(p / 1e-3)
