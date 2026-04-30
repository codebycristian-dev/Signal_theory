"""
rtl_sweep_pro.core.peak_detector
================================

Threshold + prominence + minimum-distance peak detection for stitched
spectra. Wraps :func:`scipy.signal.find_peaks` and converts the output into a
list of typed :class:`Peak` records that the GUI and exporters consume.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks


@dataclass
class Peak:
    frequency_hz: float
    level_db: float
    prominence_db: float
    bin_index: int


def detect_peaks(
    freqs_hz: np.ndarray,
    psd_db: np.ndarray,
    threshold_db: float = -70.0,
    min_prominence_db: float = 6.0,
    min_distance_hz: float = 5e3,
) -> list[Peak]:
    """Return peaks above ``threshold_db`` with the given prominence/distance.

    ``min_distance_hz`` is converted to bins using the median bin spacing
    of ``freqs_hz`` so it works on stitched spectra where the spacing may
    not be perfectly uniform.
    """
    if freqs_hz.size != psd_db.size:
        raise ValueError("freqs and psd must have the same size.")
    if freqs_hz.size < 3:
        return []

    df = float(np.median(np.diff(freqs_hz)))
    if df <= 0:
        return []
    distance_bins = max(1, int(round(min_distance_hz / df)))

    idx, props = find_peaks(
        psd_db,
        height=threshold_db,
        prominence=min_prominence_db,
        distance=distance_bins,
    )

    return [
        Peak(
            frequency_hz=float(freqs_hz[i]),
            level_db=float(psd_db[i]),
            prominence_db=float(props["prominences"][k]),
            bin_index=int(i),
        )
        for k, i in enumerate(idx)
    ]
