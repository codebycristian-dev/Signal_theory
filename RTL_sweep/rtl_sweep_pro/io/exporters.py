"""
rtl_sweep_pro.io.exporters
==========================

Persistence of :class:`SweepResult` objects in five complementary formats:

============== =========================================================
Format         Purpose
============== =========================================================
``CSV``        Flat ``frequency_hz,psd_db,coverage`` — universal.
``HDF5``       Compressed scientific archive with full segment data and
               attributes (provenance, config, peaks).
``JSON``       Manifest / metadata only — config + peaks + summary.
``SigMF``      Standard SDR metadata sidecar (``.sigmf-meta``) +
               raw IQ binary (``.sigmf-data``) when available.
``PNG``        Final spectrum plot rendered with Matplotlib.
============== =========================================================

All exporters create the destination directory if missing and return the
list of written file paths.
"""

from __future__ import annotations

import csv
import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np

from ..core.sweep_engine import SweepResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stem(result: SweepResult) -> str:
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(result.started_unix))
    return f"{result.config.session_name}_{ts}"


def _peaks_payload(result: SweepResult) -> list[dict]:
    return [asdict(p) for p in result.peaks]


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

def export_csv(result: SweepResult, out_dir: str | Path) -> Path:
    out = _ensure_dir(out_dir) / f"{_stem(result)}_spectrum.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frequency_hz", "psd_db", "coverage"])
        for fr, p, c in zip(result.freqs_hz, result.psd_db, result.coverage_count):
            w.writerow([f"{fr:.3f}", f"{p:.4f}", int(c)])

    # Also a peaks CSV for convenience
    peaks_path = _ensure_dir(out_dir) / f"{_stem(result)}_peaks.csv"
    with peaks_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frequency_hz", "level_db", "prominence_db", "bin_index"])
        for p in result.peaks:
            w.writerow([f"{p.frequency_hz:.3f}", f"{p.level_db:.3f}",
                        f"{p.prominence_db:.3f}", p.bin_index])
    logger.info("CSV written: %s + %s", out, peaks_path)
    return out


# --------------------------------------------------------------------------- #
# HDF5  (uses h5py — included in requirements)
# --------------------------------------------------------------------------- #

def export_hdf5(result: SweepResult, out_dir: str | Path) -> Path:
    import h5py  # local import keeps module importable without h5py

    out = _ensure_dir(out_dir) / f"{_stem(result)}.h5"
    with h5py.File(out, "w") as f:
        # Top-level provenance
        f.attrs["software"] = "RTL Sweep Pro 1.0"
        f.attrs["sdr_is_mock"] = bool(result.sdr_is_mock)
        f.attrs["started_unix"] = float(result.started_unix)
        f.attrs["finished_unix"] = float(result.finished_unix)
        f.attrs["rbw_hz"] = float(result.rbw_hz)
        f.attrs["nenbw_bins"] = float(result.nenbw_bins)

        # Config as a JSON string attribute (preserves nested types)
        f.attrs["config_json"] = json.dumps(result.config.to_dict())

        # Spectrum group
        g = f.create_group("spectrum")
        g.create_dataset("frequency_hz", data=result.freqs_hz, compression="gzip")
        g.create_dataset("psd_db", data=result.psd_db, compression="gzip")
        g.create_dataset("coverage", data=result.coverage_count, compression="gzip")

        # Per-segment data
        seg_g = f.create_group("segments")
        for i, s in enumerate(result.segments):
            sg = seg_g.create_group(f"seg_{i:05d}")
            sg.attrs["fc_hz"] = float(s.fc_hz)
            sg.attrs["timestamp_unix"] = float(s.timestamp_unix)
            sg.attrs["rbw_hz"] = float(s.rbw_hz)
            sg.attrs["nenbw_bins"] = float(s.nenbw_bins)
            sg.attrs["n_blocks_used"] = int(s.n_blocks_used)
            sg.create_dataset("frequency_hz", data=s.freqs_hz, compression="gzip")
            sg.create_dataset("psd_db", data=s.psd_db, compression="gzip")

        # Peaks
        if result.peaks:
            pk = f.create_group("peaks")
            arr = np.array(
                [(p.frequency_hz, p.level_db, p.prominence_db, p.bin_index)
                 for p in result.peaks],
                dtype=[("frequency_hz", "f8"), ("level_db", "f8"),
                       ("prominence_db", "f8"), ("bin_index", "i8")],
            )
            pk.create_dataset("table", data=arr)
    logger.info("HDF5 written: %s", out)
    return out


# --------------------------------------------------------------------------- #
# JSON metadata sidecar
# --------------------------------------------------------------------------- #

def export_json(result: SweepResult, out_dir: str | Path) -> Path:
    out = _ensure_dir(out_dir) / f"{_stem(result)}_metadata.json"
    payload = {
        "software": "RTL Sweep Pro 1.0",
        "sdr_is_mock": result.sdr_is_mock,
        "started_unix": result.started_unix,
        "finished_unix": result.finished_unix,
        "duration_s": result.finished_unix - result.started_unix,
        "rbw_hz": result.rbw_hz,
        "nenbw_bins": result.nenbw_bins,
        "n_bins": int(result.freqs_hz.size),
        "n_segments": int(len(result.segments)),
        "f_start_hz": float(result.freqs_hz[0]),
        "f_stop_hz": float(result.freqs_hz[-1]),
        "config": result.config.to_dict(),
        "peaks": _peaks_payload(result),
    }
    out.write_text(json.dumps(payload, indent=2))
    logger.info("JSON metadata written: %s", out)
    return out


# --------------------------------------------------------------------------- #
# SigMF  (standard SDR sidecar metadata)
# --------------------------------------------------------------------------- #

def export_sigmf(result: SweepResult, out_dir: str | Path) -> Path:
    """
    RTL Sweep Pro does not retain raw IQ for the entire sweep (would be
    multi-GB for a wide span). The SigMF artifact therefore documents the
    *processed spectrum* using a custom SigMF-like extension while keeping
    the standard ``global``/``captures`` keys filled with the per-segment
    metadata. The file is suitable for tools that consume SigMF metadata.
    """
    out = _ensure_dir(out_dir) / f"{_stem(result)}.sigmf-meta"
    cfg = result.config

    captures = []
    for i, s in enumerate(result.segments):
        captures.append({
            "core:sample_start": i,    # nominal index per segment
            "core:frequency": s.fc_hz,
            "core:datetime": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(s.timestamp_unix),
            ),
            "rtlsweep:rbw_hz": s.rbw_hz,
            "rtlsweep:nenbw_bins": s.nenbw_bins,
            "rtlsweep:n_blocks_used": s.n_blocks_used,
        })

    payload = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": cfg.sample_rate,
            "core:hw": "RTL-SDR (R820T/R820T2)" if not result.sdr_is_mock else "Mock SDR",
            "core:version": "1.2.0",
            "core:description":
                "RTL Sweep Pro processed spectrum sweep "
                f"({cfg.f_start/1e6:.3f}–{cfg.f_stop/1e6:.3f} MHz, "
                f"RBW={result.rbw_hz:.1f} Hz).",
            "core:author": "rtl_sweep_pro",
            "core:recorder": "rtl_sweep_pro",
            "core:uuid": str(uuid.uuid4()),
            "rtlsweep:gain_db": cfg.gain_db,
            "rtlsweep:fft_size": cfg.fft_size,
            "rtlsweep:n_averages": cfg.n_averages,
            "rtlsweep:window": cfg.window,
            "rtlsweep:overlap_fraction": cfg.overlap_fraction,
            "rtlsweep:bandwidth_useful_fraction": cfg.bandwidth_useful_fraction,
            "rtlsweep:settle_time_s": cfg.settle_time_s,
            "rtlsweep:discard_samples": cfg.discard_samples,
            "rtlsweep:calibration_offset_db": cfg.calibration_offset_db,
        },
        "captures": captures,
        "annotations": [
            {
                "core:sample_start": p.bin_index,
                "core:freq_lower_edge": p.frequency_hz - result.rbw_hz / 2,
                "core:freq_upper_edge": p.frequency_hz + result.rbw_hz / 2,
                "core:label": f"peak_{i}",
                "rtlsweep:level_db": p.level_db,
                "rtlsweep:prominence_db": p.prominence_db,
            }
            for i, p in enumerate(result.peaks)
        ],
    }
    out.write_text(json.dumps(payload, indent=2))
    logger.info("SigMF metadata written: %s", out)
    return out


# --------------------------------------------------------------------------- #
# PNG plot of the final spectrum
# --------------------------------------------------------------------------- #

def export_png(
    result: SweepResult,
    out_dir: str | Path,
    annotate_peaks: bool = True,
) -> Path:
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    out = _ensure_dir(out_dir) / f"{_stem(result)}_spectrum.png"

    fig, ax = plt.subplots(figsize=(14, 5), dpi=150)
    ax.plot(result.freqs_hz / 1e6, result.psd_db, linewidth=0.7, color="#1f77b4")
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("PSD (dB / Hz, calibrated by user offset)")
    ax.set_title(
        f"RTL Sweep Pro — {result.config.session_name}   "
        f"RBW = {result.rbw_hz:.1f} Hz   "
        f"window = {result.config.window}   "
        f"avgs = {result.config.n_averages}"
    )
    ax.grid(True, which="both", linestyle=":", alpha=0.5)

    if annotate_peaks and result.peaks:
        for p in result.peaks:
            ax.plot(p.frequency_hz / 1e6, p.level_db, "rv", markersize=5)
            ax.annotate(
                f"{p.frequency_hz/1e6:.3f} MHz\n{p.level_db:.1f} dB",
                xy=(p.frequency_hz / 1e6, p.level_db),
                xytext=(0, 8), textcoords="offset points",
                fontsize=7, ha="center", color="darkred",
            )

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    logger.info("PNG written: %s", out)
    return out


# --------------------------------------------------------------------------- #
# All-in-one
# --------------------------------------------------------------------------- #

def export_all(result: SweepResult, out_dir: str | Path) -> list[Path]:
    paths: list[Path] = []
    paths.append(export_csv(result, out_dir))
    try:
        paths.append(export_hdf5(result, out_dir))
    except Exception as e:
        logger.warning("HDF5 export failed: %s", e)
    paths.append(export_json(result, out_dir))
    paths.append(export_sigmf(result, out_dir))
    try:
        paths.append(export_png(result, out_dir))
    except Exception as e:
        logger.warning("PNG export failed: %s", e)
    return paths
