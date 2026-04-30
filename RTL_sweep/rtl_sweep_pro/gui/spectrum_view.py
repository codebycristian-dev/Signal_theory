"""
rtl_sweep_pro.gui.spectrum_view
===============================

Live spectrum display backed by :mod:`pyqtgraph` for low-latency drawing.
Shows the live (most recent) trace plus an optional max-hold trace and
peak markers.
"""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..core.peak_detector import Peak

pg.setConfigOptions(antialias=False, useOpenGL=False)


class SpectrumView(QWidget):
    """A frequency-domain plot widget with live + max-hold + peak markers."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget(background="#101418")
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "PSD (dB / Hz)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.getAxis("left").setPen(pg.mkPen("#aaaaaa"))
        self.plot.getAxis("bottom").setPen(pg.mkPen("#aaaaaa"))
        self.plot.getAxis("left").setTextPen(pg.mkPen("#dddddd"))
        self.plot.getAxis("bottom").setTextPen(pg.mkPen("#dddddd"))

        self.live_curve = self.plot.plot(
            pen=pg.mkPen("#5fb0ff", width=1), name="Live",
        )
        self.maxhold_curve = self.plot.plot(
            pen=pg.mkPen("#ff8a3c", width=1, style=Qt.PenStyle.DashLine),
            name="Max-hold",
        )

        self._maxhold: Optional[np.ndarray] = None
        self._freqs_ref: Optional[np.ndarray] = None

        self.peak_scatter = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen("#ffffff"),
            brush=pg.mkBrush(QColor(255, 70, 70)),
            symbol="t",
        )
        self.plot.addItem(self.peak_scatter)

        legend = self.plot.addLegend(offset=(-10, 10))
        legend.setBrush(pg.mkBrush(20, 24, 28, 200))
        legend.setLabelTextColor("#dddddd")

        layout.addWidget(self.plot)
        self.plot.setYRange(-130, -20)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self._maxhold = None
        self._freqs_ref = None
        self.live_curve.setData([], [])
        self.maxhold_curve.setData([], [])
        self.peak_scatter.clear()

    def update_spectrum(self, freqs_hz: np.ndarray, psd_db: np.ndarray) -> None:
        if freqs_hz.size == 0:
            return

        # Update max-hold first (in full resolution).
        if self._maxhold is None or self._maxhold.size != psd_db.size:
            self._maxhold = psd_db.copy()
            self._freqs_ref = freqs_hz
        else:
            self._maxhold = np.maximum(self._maxhold, psd_db)

        # Downsample for display when the array is huge.
        if freqs_hz.size > 200_000:
            stride = freqs_hz.size // 100_000
            f = freqs_hz[::stride]
            p = psd_db[::stride]
            mh = self._maxhold[::stride]
        else:
            f, p, mh = freqs_hz, psd_db, self._maxhold

        # Mask the no-coverage sentinel (-200) so the curve is gap-free.
        mask = p > -190.0
        self.live_curve.setData(f[mask], p[mask])
        self.maxhold_curve.setData(f[mask], mh[mask])

    def set_peaks(self, peaks: Iterable[Peak]) -> None:
        pts = [{"pos": (p.frequency_hz, p.level_db)} for p in peaks]
        self.peak_scatter.setData(pts)

    def autorange_y(self, psd_db: np.ndarray) -> None:
        valid = psd_db[psd_db > -190]
        if valid.size == 0:
            return
        lo = float(np.percentile(valid, 1)) - 5
        hi = float(np.percentile(valid, 99.9)) + 10
        self.plot.setYRange(lo, hi)
