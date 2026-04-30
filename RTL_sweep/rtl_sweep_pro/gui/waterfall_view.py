"""
rtl_sweep_pro.gui.waterfall_view
================================

Rolling waterfall (spectrogram) view. Each completed sweep pass adds one
row at the bottom; older rows scroll up and eventually fall off.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class WaterfallView(QWidget):
    """Rolling spectrogram. Independent from the live spectrum view."""

    DEFAULT_HEIGHT = 200          # rows
    DEFAULT_VRANGE = (-120, -30)  # dB

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget(background="#101418")
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "Pass # (newest at top)")
        self.plot.showGrid(x=True, y=False, alpha=0.2)

        self.image = pg.ImageItem()
        self.plot.addItem(self.image)

        self._buf: Optional[np.ndarray] = None
        self._freqs: Optional[np.ndarray] = None
        self._pass = 0

        # Colormap (viridis-ish)
        self._lut = self._make_lut()
        self.image.setLookupTable(self._lut)
        self.image.setLevels(self.DEFAULT_VRANGE)

        layout.addWidget(self.plot)

    @staticmethod
    def _make_lut(n: int = 256) -> np.ndarray:
        cmap = pg.colormap.get("viridis")
        if cmap is None:
            # Fallback: grayscale
            return np.stack([np.linspace(0, 255, n)] * 3, axis=-1).astype(np.uint8)
        return cmap.getLookupTable(0.0, 1.0, n)

    def reset(self) -> None:
        self._buf = None
        self._freqs = None
        self._pass = 0
        self.image.clear()

    def add_pass(self, freqs_hz: np.ndarray, psd_db: np.ndarray) -> None:
        if freqs_hz.size == 0:
            return
        psd = np.where(psd_db > -190, psd_db, np.nan)

        if self._buf is None or self._buf.shape[1] != psd.size:
            self._buf = np.full((self.DEFAULT_HEIGHT, psd.size), np.nan, np.float32)
            self._freqs = freqs_hz
            self.image.setRect(
                QRectF(
                    float(freqs_hz[0]), 0.0,
                    float(freqs_hz[-1] - freqs_hz[0]),
                    float(self.DEFAULT_HEIGHT),
                )
            )

        # Roll up and insert at the top
        self._buf = np.roll(self._buf, -1, axis=0)
        self._buf[-1, :] = psd.astype(np.float32)
        self._pass += 1

        display = np.where(np.isnan(self._buf), self.DEFAULT_VRANGE[0], self._buf)
        # Transpose so axis 0 (rows) is Y in the image item
        self.image.setImage(
            display.T, autoLevels=False, levels=self.DEFAULT_VRANGE,
        )
