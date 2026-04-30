"""
rtl_sweep_pro.core.sweep_engine
===============================

The sweep engine — the central orchestrator.

Responsibilities
----------------

1. Walk the frequency plan (``f_start → f_stop`` in steps of
   ``step_frequency``).
2. For each center frequency:
     * tune the SDR
     * sleep ``settle_time_s``
     * read & **discard** ``discard_samples``
     * read ``samples_per_capture`` samples
     * compute Welch-style averaged PSD
     * keep only the bins within ``±usable_bandwidth/2`` of ``fc``
     * stitch them into the global spectrum array
3. Detect peaks at the end of each pass.
4. Emit Qt signals so the GUI (or any consumer) can plot incrementally
   and the engine never blocks the UI thread.

The engine is a :class:`QObject` with a :class:`SweepThread` companion that
moves it onto its own thread. Stop / pause are cooperative (checked between
steps).

This file is import-safe even if PyQt6 is not installed at module load time:
the ``QObject``/``pyqtSignal`` imports happen at module level (PyQt6 is a
hard dependency anyway) but the engine logic stays well-isolated.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..config import SweepConfig
from .calibration import apply_offset
from .dsp import SegmentResult, welch_psd_complex
from .peak_detector import Peak, detect_peaks
from .sdr_controller import SDRBase, open_sdr

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# State enumeration (string for easy logging / GUI display)
# --------------------------------------------------------------------------- #

class SweepState:
    IDLE = "idle"
    TUNING = "tuning"
    SETTLING = "settling"
    DISCARDING = "discarding"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    STITCHING = "stitching"
    DONE = "done"
    STOPPED = "stopped"
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Sweep result aggregate
# --------------------------------------------------------------------------- #

@dataclass
class SweepResult:
    """Final unified spectrum + provenance metadata for one full pass."""
    config: SweepConfig
    freqs_hz: np.ndarray
    psd_db: np.ndarray
    coverage_count: np.ndarray   # how many segments contributed to each bin
    rbw_hz: float
    nenbw_bins: float
    started_unix: float
    finished_unix: float
    sdr_is_mock: bool
    peaks: list[Peak] = field(default_factory=list)
    segments: list[SegmentResult] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class SweepEngine(QObject):
    """Slot-driven sweep engine. Designed to be ``moveToThread``-ed."""

    # ---- Qt signals ---------------------------------------------------- #
    state_changed       = pyqtSignal(str)                         # SweepState string
    progress            = pyqtSignal(int, int, float)             # step, total, fc_hz
    segment_ready       = pyqtSignal(object)                      # SegmentResult
    sweep_partial       = pyqtSignal(object, object, object)      # freqs, psd, coverage
    sweep_finished      = pyqtSignal(object)                      # SweepResult
    error               = pyqtSignal(str)
    log_message         = pyqtSignal(str)                         # short status string

    def __init__(self, prefer_mock: bool = False) -> None:
        super().__init__()
        self._stop = False
        self._pause = False
        self._prefer_mock = prefer_mock
        self._sdr: Optional[SDRBase] = None

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self._stop = True

    def request_pause(self, paused: bool) -> None:
        self._pause = bool(paused)

    # ------------------------------------------------------------------ #
    # Main entry point — runs on the worker thread
    # ------------------------------------------------------------------ #
    def run(self, cfg: SweepConfig) -> None:
        """Execute one (or several, if continuous) full sweeps."""
        try:
            errs = cfg.validate()
            if errs:
                msg = "Invalid configuration:\n  - " + "\n  - ".join(errs)
                self.error.emit(msg)
                self.state_changed.emit(SweepState.ERROR)
                return

            self._stop = False
            self._sdr = open_sdr(prefer_mock=self._prefer_mock)
            self._sdr.set_sample_rate(cfg.sample_rate)
            self._sdr.set_freq_correction(cfg.freq_correction_ppm)
            self._sdr.set_agc(cfg.agc_enabled)
            self._sdr.set_gain(cfg.gain_db)

            self.log_message.emit(
                f"SDR opened ({'MOCK' if self._sdr.is_mock else 'real RTL-SDR'}); "
                f"fs={cfg.sample_rate/1e6:.3f} MS/s; gain={cfg.gain_db:.1f} dB"
            )

            pass_index = 0
            while not self._stop:
                pass_index += 1
                self.log_message.emit(f"Starting pass #{pass_index}")
                result = self._run_one_pass(cfg)
                if self._stop:
                    self.state_changed.emit(SweepState.STOPPED)
                    self.sweep_finished.emit(result)
                    return
                self.sweep_finished.emit(result)
                if not cfg.continuous:
                    break
                self.log_message.emit("Continuous mode — restarting pass.")
            self.state_changed.emit(SweepState.DONE)

        except Exception as e:
            logger.exception("Sweep engine crashed.")
            self.error.emit(f"{type(e).__name__}: {e}")
            self.state_changed.emit(SweepState.ERROR)
        finally:
            if self._sdr is not None:
                try:
                    self._sdr.close()
                except Exception:
                    pass
                self._sdr = None

    # ------------------------------------------------------------------ #
    # One full sweep pass
    # ------------------------------------------------------------------ #
    def _run_one_pass(self, cfg: SweepConfig) -> SweepResult:
        assert self._sdr is not None
        started = time.time()

        # ----- build the global frequency grid --------------------- #
        # Bin spacing of one segment after FFT
        bin_hz = cfg.sample_rate / cfg.fft_size
        # Span end inclusive
        n_bins_total = int(np.ceil((cfg.f_stop - cfg.f_start) / bin_hz)) + 1
        global_freqs = cfg.f_start + np.arange(n_bins_total) * bin_hz

        # In *linear* power for accumulation, with a coverage counter so the
        # final result is a proper average (and so that crossfades work).
        global_psd_lin = np.zeros(n_bins_total, dtype=np.float64)
        coverage = np.zeros(n_bins_total, dtype=np.int32)
        global_psd_max_lin = np.full(n_bins_total, -np.inf, dtype=np.float64)
        global_psd_min_lin = np.full(n_bins_total, np.inf, dtype=np.float64)

        # Crossfade weighting: triangular weight per segment
        weights_accum = np.zeros(n_bins_total, dtype=np.float64)
        weighted_psd_lin = np.zeros(n_bins_total, dtype=np.float64)

        # ----- frequency plan ------------------------------------- #
        fcs = np.arange(
            cfg.f_start + cfg.usable_bandwidth / 2,
            cfg.f_stop - cfg.usable_bandwidth / 2 + cfg.step_frequency,
            cfg.step_frequency,
        )
        # Clip to keep within RTL band envelope
        from ..config import RTL_FMAX_HZ, RTL_FMIN_HZ  # local import
        fcs = fcs[(fcs >= RTL_FMIN_HZ) & (fcs <= RTL_FMAX_HZ)]
        if fcs.size == 0:
            raise RuntimeError("Empty frequency plan — check span vs bandwidth.")

        total = int(fcs.size)
        segments: list[SegmentResult] = []
        rbw_last = 0.0
        nenbw_last = 0.0

        # ----- main loop ------------------------------------------ #
        for k, fc in enumerate(fcs):
            if self._stop:
                break
            while self._pause and not self._stop:
                time.sleep(0.05)

            # 1. tune
            self.state_changed.emit(SweepState.TUNING)
            self._sdr.set_center_freq(float(fc))

            # 2. settle
            self.state_changed.emit(SweepState.SETTLING)
            if cfg.settle_time_s > 0:
                time.sleep(cfg.settle_time_s)

            # 3. discard transient
            self.state_changed.emit(SweepState.DISCARDING)
            if cfg.discard_samples > 0:
                self._sdr.read_samples(cfg.discard_samples)

            # 4. capture
            self.state_changed.emit(SweepState.CAPTURING)
            iq = self._sdr.read_samples(cfg.samples_per_capture)

            # 5–8. DSP
            self.state_changed.emit(SweepState.PROCESSING)
            base_freqs, psd_db_seg, meta = welch_psd_complex(
                iq=iq,
                fs=cfg.sample_rate,
                fft_size=cfg.fft_size,
                n_averages=cfg.n_averages,
                overlap_fraction=cfg.overlap_fraction,
                window_name=cfg.window,
            )
            rbw_last = meta["rbw_hz"]
            nenbw_last = meta["nenbw_bins"]

            # Apply user calibration offset
            psd_db_seg = apply_offset(psd_db_seg, cfg.calibration_offset_db)

            abs_freqs = base_freqs + fc
            seg = SegmentResult(
                fc_hz=float(fc),
                freqs_hz=abs_freqs,
                psd_db=psd_db_seg,
                rbw_hz=meta["rbw_hz"],
                nenbw_bins=meta["nenbw_bins"],
                n_blocks_used=meta["n_blocks_used"],
                timestamp_unix=time.time(),
            )
            segments.append(seg)
            self.segment_ready.emit(seg)

            # 9–10. Stitch into global grid
            self.state_changed.emit(SweepState.STITCHING)
            self._stitch(
                cfg=cfg,
                fc=float(fc),
                abs_freqs=abs_freqs,
                psd_db_seg=psd_db_seg,
                global_freqs=global_freqs,
                global_psd_lin=global_psd_lin,
                global_psd_max_lin=global_psd_max_lin,
                global_psd_min_lin=global_psd_min_lin,
                weighted_psd_lin=weighted_psd_lin,
                weights_accum=weights_accum,
                coverage=coverage,
            )

            self.progress.emit(k + 1, total, float(fc))

            # Emit a partial for live plotting (in dB)
            with np.errstate(divide="ignore"):
                partial_db = self._consolidate(
                    cfg.stitch_mode,
                    global_psd_lin,
                    coverage,
                    weighted_psd_lin,
                    weights_accum,
                    global_psd_max_lin,
                    global_psd_min_lin,
                )
            self.sweep_partial.emit(global_freqs, partial_db, coverage)

        # ----- finalization --------------------------------------- #
        psd_db = self._consolidate(
            cfg.stitch_mode,
            global_psd_lin,
            coverage,
            weighted_psd_lin,
            weights_accum,
            global_psd_max_lin,
            global_psd_min_lin,
        )

        peaks = detect_peaks(
            global_freqs,
            psd_db,
            threshold_db=cfg.peak_threshold_db,
            min_prominence_db=cfg.peak_min_prominence_db,
            min_distance_hz=cfg.peak_min_distance_hz,
        )

        finished = time.time()
        self.log_message.emit(
            f"Pass complete in {finished - started:.1f} s; "
            f"RBW = {rbw_last:.1f} Hz; peaks = {len(peaks)}"
        )

        return SweepResult(
            config=cfg,
            freqs_hz=global_freqs,
            psd_db=psd_db,
            coverage_count=coverage,
            rbw_hz=rbw_last,
            nenbw_bins=nenbw_last,
            started_unix=started,
            finished_unix=finished,
            sdr_is_mock=self._sdr.is_mock,
            peaks=peaks,
            segments=segments,
        )

    # ------------------------------------------------------------------ #
    # Stitching helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stitch(
        cfg: SweepConfig,
        fc: float,
        abs_freqs: np.ndarray,
        psd_db_seg: np.ndarray,
        global_freqs: np.ndarray,
        global_psd_lin: np.ndarray,
        global_psd_max_lin: np.ndarray,
        global_psd_min_lin: np.ndarray,
        weighted_psd_lin: np.ndarray,
        weights_accum: np.ndarray,
        coverage: np.ndarray,
    ) -> None:
        """Insert one segment's PSD into the global accumulators.

        Only bins inside the *usable bandwidth* contribute. A triangular
        weighting (1 at fc, 0 at the usable edges) is used for crossfade.
        """
        half_bw = cfg.usable_bandwidth / 2
        # Mask of useful bins inside the segment
        keep = (abs_freqs >= fc - half_bw) & (abs_freqs <= fc + half_bw)
        f_keep = abs_freqs[keep]
        p_keep_db = psd_db_seg[keep]
        if f_keep.size == 0:
            return

        # Map to global indices
        bin_hz = global_freqs[1] - global_freqs[0]
        idx = np.round((f_keep - global_freqs[0]) / bin_hz).astype(np.int64)
        ok = (idx >= 0) & (idx < global_freqs.size)
        idx = idx[ok]
        p_keep_db = p_keep_db[ok]
        f_keep = f_keep[ok]

        p_keep_lin = np.power(10.0, p_keep_db / 10.0)

        # Mean accumulator
        global_psd_lin[idx] += p_keep_lin
        coverage[idx] += 1

        # Max-/min-hold accumulators (in linear power)
        np.maximum.at(global_psd_max_lin, idx, p_keep_lin)
        np.minimum.at(global_psd_min_lin, idx, p_keep_lin)

        # Crossfade: triangular weight in [0, 1], 1 at fc, 0 at edges
        w = 1.0 - np.abs(f_keep - fc) / half_bw
        w = np.clip(w, 0.0, 1.0)
        # Avoid zero weights everywhere — add a small floor
        w = np.maximum(w, 1e-3)
        weighted_psd_lin[idx] += p_keep_lin * w
        weights_accum[idx] += w

    @staticmethod
    def _consolidate(
        mode: str,
        mean_lin: np.ndarray,
        coverage: np.ndarray,
        weighted_lin: np.ndarray,
        weights_accum: np.ndarray,
        max_lin: np.ndarray,
        min_lin: np.ndarray,
    ) -> np.ndarray:
        """Convert accumulators into a single dB array per ``mode``."""
        out = np.full(mean_lin.size, -200.0, dtype=np.float64)
        valid = coverage > 0

        if mode == "mean":
            out[valid] = 10.0 * np.log10(mean_lin[valid] / coverage[valid] + 1e-30)
        elif mode == "max":
            v = max_lin > -np.inf
            out[v] = 10.0 * np.log10(max_lin[v] + 1e-30)
        elif mode == "min":
            v = min_lin < np.inf
            out[v] = 10.0 * np.log10(min_lin[v] + 1e-30)
        else:  # "crossfade"
            v = weights_accum > 0
            out[v] = 10.0 * np.log10(weighted_lin[v] / weights_accum[v] + 1e-30)
        return out


# --------------------------------------------------------------------------- #
# QThread wrapper
# --------------------------------------------------------------------------- #

class SweepThread(QThread):
    """Convenience :class:`QThread` that owns a :class:`SweepEngine`."""

    def __init__(self, cfg: SweepConfig, prefer_mock: bool = False) -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = SweepEngine(prefer_mock=prefer_mock)
        self.engine.moveToThread(self)

    def run(self) -> None:  # noqa: D401 — Qt override
        self.engine.run(self.cfg)
