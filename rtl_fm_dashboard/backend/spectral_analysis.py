from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import signal


EPS = 1e-20


@dataclass
class SpectralAnalyzerConfig:
    sample_rate: float = 250_000.0
    center_freq: float = 105.7e6
    nperseg: int = 2048
    noverlap: int | None = None
    waterfall_rows: int = 64
    cfar_train_cells: int = 24
    cfar_guard_cells: int = 3
    cfar_threshold_db: float = 8.0
    min_signal_bins: int = 3
    merge_gap_bins: int = 2
    persistence_window: int = 5
    min_persistence: int = 1
    dc_notch_hz: float = 0.0

    def sanitize(self) -> None:
        self.sample_rate = float(max(self.sample_rate, 1.0))
        self.center_freq = float(self.center_freq)
        self.nperseg = int(max(self.nperseg, 64))
        self.waterfall_rows = int(max(self.waterfall_rows, 1))
        self.cfar_train_cells = int(max(self.cfar_train_cells, 2))
        self.cfar_guard_cells = int(max(self.cfar_guard_cells, 0))
        self.cfar_threshold_db = float(self.cfar_threshold_db)
        self.min_signal_bins = int(max(self.min_signal_bins, 1))
        self.merge_gap_bins = int(max(self.merge_gap_bins, 0))
        self.persistence_window = int(max(self.persistence_window, 1))
        self.min_persistence = int(max(self.min_persistence, 1))
        self.dc_notch_hz = float(max(self.dc_notch_hz, 0.0))


class SpectralAnalyzer:
    """
    Welch + CA/median-CFAR + waterfall + 1-D clustering for RTL-SDR IQ blocks.

    Power estimates are relative to the digital IQ stream. They are useful for
    comparing signals and tracking changes, but they are not calibrated dBm
    unless the receiver chain is calibrated externally.
    """

    def __init__(self, cfg: SpectralAnalyzerConfig):
        self.cfg = cfg
        self.cfg.sanitize()

        self.freqs_hz: np.ndarray | None = None
        self.waterfall_db: list[np.ndarray] = []
        self.mask_waterfall: list[np.ndarray] = []
        self.avg_total_power_linear: float | None = None

    def reset(self) -> None:
        self.freqs_hz = None
        self.waterfall_db.clear()
        self.mask_waterfall.clear()
        self.avg_total_power_linear = None

    def process(
        self,
        iq: np.ndarray,
        max_points: int | None = None,
        include_threshold: bool = False,
        include_waterfall: bool = False,
    ) -> dict[str, Any]:
        iq = np.asarray(iq, dtype=np.complex64)

        if iq.size == 0:
            return self._empty_payload()

        freqs_hz, psd_linear, psd_db = self._welch_psd(iq)

        if self.freqs_hz is None or len(self.freqs_hz) != len(freqs_hz):
            self.waterfall_db.clear()
            self.mask_waterfall.clear()

        self.freqs_hz = freqs_hz
        df_hz = self._bin_width_hz(freqs_hz)

        threshold_db, noise_floor_db = cfar_threshold_db(
            psd_db,
            train_cells=self.cfg.cfar_train_cells,
            guard_cells=self.cfg.cfar_guard_cells,
            threshold_offset_db=self.cfg.cfar_threshold_db,
        )

        detected = psd_db >= threshold_db
        detected &= np.isfinite(psd_db)

        if self.cfg.dc_notch_hz > 0.0:
            dc_mask = np.abs(freqs_hz - self.cfg.center_freq) <= (self.cfg.dc_notch_hz / 2.0)
            detected[dc_mask] = False

        self._push_waterfall(psd_db, detected)

        persistent_mask = self._persistent_mask(detected)
        groups = group_detected_bins(
            persistent_mask,
            min_bins=self.cfg.min_signal_bins,
            merge_gap_bins=self.cfg.merge_gap_bins,
        )

        avg_psd_linear = self._average_waterfall_linear()
        signals = self._estimate_signals(
            groups=groups,
            freqs_hz=freqs_hz,
            psd_linear=psd_linear,
            avg_psd_linear=avg_psd_linear,
            psd_db=psd_db,
            noise_floor_db=noise_floor_db,
            df_hz=df_hz,
        )

        total_power_linear = float(np.sum(psd_linear) * df_hz)
        self.avg_total_power_linear = update_ewma_power(self.avg_total_power_linear, total_power_linear)

        freq_view, psd_view = downsample_xy(freqs_hz / 1e6, psd_db, max_points)

        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "freq_mhz": freq_view.astype(np.float64).tolist(),
            "psd_db": psd_view.astype(np.float32).tolist(),
            "metrics": self._metrics(freqs_hz, psd_db, signals),
            "signals": signals,
            "power": {
                "units": "dBFS_relative",
                "instant_total_power_dbfs": db10(total_power_linear),
                "avg_total_power_dbfs": db10(self.avg_total_power_linear or 0.0),
            },
            "analysis": {
                "method": "welch_cfar_waterfall_clustering",
                "sample_rate": self.cfg.sample_rate,
                "center_freq": self.cfg.center_freq,
                "nperseg": min(self.cfg.nperseg, int(iq.size)),
                "waterfall_rows": len(self.waterfall_db),
                "cfar_train_cells": self.cfg.cfar_train_cells,
                "cfar_guard_cells": self.cfg.cfar_guard_cells,
                "cfar_threshold_db": self.cfg.cfar_threshold_db,
                "min_signal_bins": self.cfg.min_signal_bins,
                "merge_gap_bins": self.cfg.merge_gap_bins,
                "persistence_window": self.cfg.persistence_window,
                "min_persistence": self.cfg.min_persistence,
                "df_hz": df_hz,
            },
        }

        if include_threshold:
            _, threshold_view = downsample_xy(freqs_hz / 1e6, threshold_db, max_points)
            _, noise_view = downsample_xy(freqs_hz / 1e6, noise_floor_db, max_points)
            payload["cfar"] = {
                "threshold_db": threshold_view.astype(np.float32).tolist(),
                "noise_floor_db": noise_view.astype(np.float32).tolist(),
            }

        if include_waterfall:
            payload["waterfall"] = {
                "rows": [row.astype(np.float32).tolist() for row in self.waterfall_db],
                "row_count": len(self.waterfall_db),
            }

        return payload

    def _welch_psd(self, iq: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        nperseg = min(self.cfg.nperseg, int(iq.size))
        noverlap = self.cfg.noverlap

        if noverlap is None:
            noverlap = nperseg // 2

        noverlap = int(max(0, min(noverlap, nperseg - 1)))

        freqs, psd = signal.welch(
            iq,
            fs=self.cfg.sample_rate,
            window="hann",
            nperseg=nperseg,
            noverlap=noverlap,
            return_onesided=False,
            scaling="density",
        )

        freqs_hz = np.fft.fftshift(freqs) + self.cfg.center_freq
        psd_linear = np.maximum(np.real(np.fft.fftshift(psd)), EPS)
        psd_db = 10.0 * np.log10(psd_linear)

        return freqs_hz.astype(np.float64), psd_linear.astype(np.float64), psd_db.astype(np.float64)

    def _push_waterfall(self, psd_db: np.ndarray, detected: np.ndarray) -> None:
        self.waterfall_db.append(psd_db.copy())
        self.mask_waterfall.append(detected.copy())

        if len(self.waterfall_db) > self.cfg.waterfall_rows:
            self.waterfall_db.pop(0)
            self.mask_waterfall.pop(0)

    def _persistent_mask(self, current_mask: np.ndarray) -> np.ndarray:
        if self.cfg.min_persistence <= 1:
            return current_mask

        recent = self.mask_waterfall[-self.cfg.persistence_window :]

        if not recent:
            return current_mask

        votes = np.sum(np.stack(recent, axis=0), axis=0)
        needed = min(self.cfg.min_persistence, len(recent))
        return current_mask & (votes >= needed)

    def _average_waterfall_linear(self) -> np.ndarray:
        if not self.waterfall_db:
            return np.zeros(0, dtype=np.float64)

        rows_db = np.stack(self.waterfall_db, axis=0)
        return np.mean(10.0 ** (rows_db / 10.0), axis=0)

    def _estimate_signals(
        self,
        groups: list[tuple[int, int]],
        freqs_hz: np.ndarray,
        psd_linear: np.ndarray,
        avg_psd_linear: np.ndarray,
        psd_db: np.ndarray,
        noise_floor_db: np.ndarray,
        df_hz: float,
    ) -> list[dict[str, float | int | None]]:
        estimates: list[dict[str, float | int | None]] = []

        for signal_id, (left, right) in enumerate(groups, start=1):
            sl = slice(left, right + 1)
            f = freqs_hz[sl]
            p = np.maximum(psd_linear[sl], 0.0)
            p_avg = np.maximum(avg_psd_linear[sl], 0.0) if avg_psd_linear.size else p

            power_linear = float(np.sum(p) * df_hz)
            avg_power_linear = float(np.sum(p_avg) * df_hz)
            weight_sum = float(np.sum(p))

            if weight_sum > 0.0:
                center_freq_hz = float(np.sum(f * p) / weight_sum)
            else:
                center_freq_hz = float((freqs_hz[left] + freqs_hz[right]) / 2.0)

            peak_local = int(np.argmax(psd_db[sl]))
            peak_idx = left + peak_local
            edge_low_hz = float(freqs_hz[left] - df_hz / 2.0)
            edge_high_hz = float(freqs_hz[right] + df_hz / 2.0)

            active_ratio = self._active_ratio(left, right)
            occupied_bw_hz = occupied_bandwidth_hz(f, p, df_hz, fraction=0.99)
            local_noise_db = float(np.median(noise_floor_db[sl]))
            peak_db = float(psd_db[peak_idx])

            estimates.append(
                {
                    "id": signal_id,
                    "start_freq_hz": edge_low_hz,
                    "stop_freq_hz": edge_high_hz,
                    "center_freq_hz": center_freq_hz,
                    "peak_freq_hz": float(freqs_hz[peak_idx]),
                    "bandwidth_hz": max(0.0, edge_high_hz - edge_low_hz),
                    "occupied_bw_99_hz": occupied_bw_hz,
                    "instant_power_dbfs": db10(power_linear),
                    "avg_power_dbfs": db10(avg_power_linear),
                    "peak_psd_dbfs_per_hz": peak_db,
                    "snr_db": peak_db - local_noise_db,
                    "bin_count": int(right - left + 1),
                    "active_ratio": active_ratio,
                }
            )

        estimates.sort(key=lambda item: item["instant_power_dbfs"] or -300.0, reverse=True)

        for signal_id, item in enumerate(estimates, start=1):
            item["id"] = signal_id

        return estimates

    def _active_ratio(self, left: int, right: int) -> float:
        recent = self.mask_waterfall[-self.cfg.persistence_window :]

        if not recent:
            return 0.0

        window = np.stack([row[left : right + 1] for row in recent], axis=0)
        return float(np.mean(window))

    def _metrics(
        self,
        freqs_hz: np.ndarray,
        psd_db: np.ndarray,
        signals: list[dict[str, float | int | None]],
    ) -> dict[str, float | int | None]:
        base = estimate_psd_metrics(freqs_hz, psd_db, self.cfg.center_freq)
        base["signal_count"] = len(signals)

        if signals:
            strongest = signals[0]
            base.update(
                {
                    "strongest_center_mhz": mhz(strongest.get("center_freq_hz")),
                    "strongest_peak_mhz": mhz(strongest.get("peak_freq_hz")),
                    "strongest_bw_khz": khz(strongest.get("bandwidth_hz")),
                    "strongest_occupied_bw_99_khz": khz(strongest.get("occupied_bw_99_hz")),
                    "strongest_power_dbfs": finite_or_none(strongest.get("instant_power_dbfs")),
                    "strongest_avg_power_dbfs": finite_or_none(strongest.get("avg_power_dbfs")),
                    "strongest_snr_db": finite_or_none(strongest.get("snr_db")),
                }
            )
        else:
            base.update(
                {
                    "strongest_center_mhz": None,
                    "strongest_peak_mhz": None,
                    "strongest_bw_khz": None,
                    "strongest_occupied_bw_99_khz": None,
                    "strongest_power_dbfs": None,
                    "strongest_avg_power_dbfs": None,
                    "strongest_snr_db": None,
                }
            )

        return base

    def _bin_width_hz(self, freqs_hz: np.ndarray) -> float:
        if len(freqs_hz) < 2:
            return self.cfg.sample_rate

        return float(abs(freqs_hz[1] - freqs_hz[0]))

    def _empty_payload(self) -> dict[str, Any]:
        return {
            "timestamp": time.time(),
            "freq_mhz": [],
            "psd_db": [],
            "metrics": estimate_psd_metrics(np.array([]), np.array([]), self.cfg.center_freq),
            "signals": [],
            "power": {
                "units": "dBFS_relative",
                "instant_total_power_dbfs": None,
                "avg_total_power_dbfs": None,
            },
            "analysis": {
                "method": "welch_cfar_waterfall_clustering",
                "sample_rate": self.cfg.sample_rate,
                "center_freq": self.cfg.center_freq,
            },
        }


def cfar_threshold_db(
    psd_db: np.ndarray,
    train_cells: int,
    guard_cells: int,
    threshold_offset_db: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Robust CA-CFAR in dB using the median of training cells as local noise.
    """

    psd_db = np.asarray(psd_db, dtype=np.float64)
    n = len(psd_db)

    if n == 0:
        empty = np.zeros(0, dtype=np.float64)
        return empty, empty

    finite = np.isfinite(psd_db)
    safe_psd = np.where(finite, psd_db, np.nan)
    global_noise = float(np.nanmedian(safe_psd)) if np.any(finite) else -140.0

    noise = np.full(n, global_noise, dtype=np.float64)
    min_training = max(3, train_cells // 2)

    for idx in range(n):
        left0 = max(0, idx - guard_cells - train_cells)
        left1 = max(0, idx - guard_cells)
        right0 = min(n, idx + guard_cells + 1)
        right1 = min(n, idx + guard_cells + 1 + train_cells)

        training = np.concatenate((safe_psd[left0:left1], safe_psd[right0:right1]))
        training = training[np.isfinite(training)]

        if training.size >= min_training:
            noise[idx] = float(np.median(training))

    return noise + threshold_offset_db, noise


def group_detected_bins(
    mask: np.ndarray,
    min_bins: int,
    merge_gap_bins: int,
) -> list[tuple[int, int]]:
    detected = np.flatnonzero(mask)

    if detected.size == 0:
        return []

    groups: list[tuple[int, int]] = []
    left = int(detected[0])
    prev = int(detected[0])

    for raw_idx in detected[1:]:
        idx = int(raw_idx)

        if idx - prev <= merge_gap_bins + 1:
            prev = idx
            continue

        if prev - left + 1 >= min_bins:
            groups.append((left, prev))

        left = idx
        prev = idx

    if prev - left + 1 >= min_bins:
        groups.append((left, prev))

    return groups


def occupied_bandwidth_hz(
    freqs_hz: np.ndarray,
    power_linear: np.ndarray,
    df_hz: float,
    fraction: float = 0.99,
) -> float:
    if freqs_hz.size == 0 or power_linear.size == 0:
        return 0.0

    power = np.maximum(np.asarray(power_linear, dtype=np.float64), 0.0)
    total = float(np.sum(power))

    if total <= 0.0:
        return 0.0

    fraction = float(np.clip(fraction, 0.01, 0.999))
    tail = (1.0 - fraction) / 2.0
    cumulative = np.cumsum(power) / total

    left_idx = int(np.searchsorted(cumulative, tail, side="left"))
    right_idx = int(np.searchsorted(cumulative, 1.0 - tail, side="left"))
    left_idx = max(0, min(left_idx, len(freqs_hz) - 1))
    right_idx = max(left_idx, min(right_idx, len(freqs_hz) - 1))

    return float((freqs_hz[right_idx] - freqs_hz[left_idx]) + df_hz)


def estimate_psd_metrics(
    freqs_hz: np.ndarray,
    psd_db: np.ndarray,
    center_freq: float,
) -> dict[str, float | None]:
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


def update_ewma_power(previous: float | None, current: float, alpha: float = 0.15) -> float:
    if previous is None:
        return current

    return alpha * current + (1.0 - alpha) * previous


def downsample_xy(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int | None,
) -> tuple[np.ndarray, np.ndarray]:
    if max_points is None or max_points <= 0 or len(x) <= max_points:
        return x, y

    idx = np.linspace(0, len(x) - 1, int(max_points)).astype(np.int64)
    return x[idx], y[idx]


def db10(value: float) -> float | None:
    if value is None or not np.isfinite(value) or value <= 0.0:
        return None

    return float(10.0 * np.log10(value + EPS))


def finite_or_none(value: object) -> float | None:
    if value is None:
        return None

    value_float = float(value)
    return value_float if np.isfinite(value_float) else None


def mhz(value: object) -> float | None:
    value_float = finite_or_none(value)
    return None if value_float is None else value_float / 1e6


def khz(value: object) -> float | None:
    value_float = finite_or_none(value)
    return None if value_float is None else value_float / 1e3
