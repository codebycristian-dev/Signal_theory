from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TextIO

import numpy as np

try:
    from .spectral_analysis import SpectralAnalyzer, SpectralAnalyzerConfig
except ImportError:  # Allows: python backend/spectral_scan.py
    from spectral_analysis import SpectralAnalyzer, SpectralAnalyzerConfig


def parse_si_float(value: str) -> float:
    text = str(value).strip().lower().replace(",", ".")
    multipliers = {
        "ghz": 1e9,
        "mhz": 1e6,
        "khz": 1e3,
        "hz": 1.0,
        "g": 1e9,
        "m": 1e6,
        "k": 1e3,
    }

    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * multiplier

    return float(text)


def parse_gain(value: str) -> float | str:
    text = str(value).strip().lower()

    if text == "auto":
        return "auto"

    return float(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Spectral localization for RTL-SDR/Nooelec Smart using "
            "Welch + CFAR + waterfall + clustering."
        )
    )
    parser.add_argument("--center-freq", type=parse_si_float, default=105.7e6, help="Tuning frequency, e.g. 105.7M.")
    parser.add_argument("--sample-rate", type=parse_si_float, default=250e3, help="RF sample rate, e.g. 250k.")
    parser.add_argument("--gain", type=parse_gain, default=35.0, help="RTL gain in dB or 'auto'.")
    parser.add_argument("--block-size", type=int, default=262_144, help="IQ samples per analysis frame.")
    parser.add_argument("--duration", type=float, default=0.0, help="Seconds to run. 0 means run until Ctrl+C.")
    parser.add_argument("--frames", type=int, default=0, help="Number of frames to process. 0 means unlimited.")
    parser.add_argument("--nperseg", type=int, default=2048, help="Welch segment size.")
    parser.add_argument("--waterfall-rows", type=int, default=64, help="Rows kept for rolling averages.")
    parser.add_argument("--cfar-train", type=int, default=24, help="Training cells per side for CFAR.")
    parser.add_argument("--cfar-guard", type=int, default=3, help="Guard cells per side for CFAR.")
    parser.add_argument("--cfar-threshold-db", type=float, default=8.0, help="Detection offset above local noise.")
    parser.add_argument("--min-bins", type=int, default=3, help="Minimum contiguous bins for a signal.")
    parser.add_argument("--merge-gap-bins", type=int, default=2, help="Merge clusters separated by this many bins.")
    parser.add_argument("--persistence-window", type=int, default=5, help="Waterfall rows used for persistence voting.")
    parser.add_argument("--min-persistence", type=int, default=1, help="Minimum votes in persistence window.")
    parser.add_argument("--dc-notch-hz", type=parse_si_float, default=0.0, help="Ignore detections around tuned center.")
    parser.add_argument("--max-points", type=int, default=900, help="Maximum PSD points included when full output is enabled.")
    parser.add_argument("--top-n", type=int, default=8, help="Maximum signals shown per frame.")
    parser.add_argument("--jsonl", type=Path, default=None, help="Optional JSONL output file.")
    parser.add_argument("--waterfall-npz", type=Path, default=None, help="Save final waterfall to an NPZ file.")
    parser.add_argument("--iq-npy", type=Path, default=None, help="Read complex IQ samples from a .npy file instead of RTL-SDR.")
    parser.add_argument("--full", action="store_true", help="Include downsampled PSD and CFAR threshold in JSON output.")
    return parser


def summarize_frame(result: dict, top_n: int, full: bool) -> dict:
    signals = result.get("signals", [])[: max(0, top_n)]
    compact_signals = []

    for item in signals:
        compact_signals.append(
            {
                "id": item.get("id"),
                "start_mhz": to_mhz(item.get("start_freq_hz")),
                "stop_mhz": to_mhz(item.get("stop_freq_hz")),
                "center_mhz": to_mhz(item.get("center_freq_hz")),
                "peak_mhz": to_mhz(item.get("peak_freq_hz")),
                "bandwidth_khz": to_khz(item.get("bandwidth_hz")),
                "occupied_bw_99_khz": to_khz(item.get("occupied_bw_99_hz")),
                "instant_power_dbfs": round_or_none(item.get("instant_power_dbfs")),
                "avg_power_dbfs": round_or_none(item.get("avg_power_dbfs")),
                "snr_db": round_or_none(item.get("snr_db")),
                "active_ratio": round_or_none(item.get("active_ratio"), digits=3),
            }
        )

    summary = {
        "timestamp": result.get("timestamp"),
        "signal_count": result.get("metrics", {}).get("signal_count", 0),
        "power": result.get("power", {}),
        "strongest": {
            "center_mhz": result.get("metrics", {}).get("strongest_center_mhz"),
            "bandwidth_khz": result.get("metrics", {}).get("strongest_bw_khz"),
            "instant_power_dbfs": result.get("metrics", {}).get("strongest_power_dbfs"),
            "avg_power_dbfs": result.get("metrics", {}).get("strongest_avg_power_dbfs"),
            "snr_db": result.get("metrics", {}).get("strongest_snr_db"),
        },
        "signals": compact_signals,
    }

    if full:
        summary["freq_mhz"] = result.get("freq_mhz", [])
        summary["psd_db"] = result.get("psd_db", [])
        summary["cfar"] = result.get("cfar", {})

    return summary


def run_from_sdr(args: argparse.Namespace, analyzer: SpectralAnalyzer, output: TextIO | None) -> None:
    try:
        from rtlsdr import RtlSdr
    except Exception as exc:
        raise SystemExit(
            "Could not import pyrtlsdr. Install backend requirements and RTL-SDR drivers first: "
            f"{exc}"
        ) from exc

    sdr = RtlSdr()

    try:
        sdr.sample_rate = args.sample_rate
        sdr.center_freq = args.center_freq
        sdr.gain = args.gain

        start = time.monotonic()
        frames = 0

        while should_continue(start, frames, args.duration, args.frames):
            iq = sdr.read_samples(args.block_size).astype(np.complex64)
            handle_frame(iq, analyzer, args, output)
            frames += 1

    finally:
        sdr.close()


def run_from_npy(args: argparse.Namespace, analyzer: SpectralAnalyzer, output: TextIO | None) -> None:
    iq = np.load(args.iq_npy)

    if np.iscomplexobj(iq):
        iq = iq.astype(np.complex64, copy=False).ravel()
    elif iq.ndim >= 2 and iq.shape[-1] == 2:
        iq = (iq[..., 0] + 1j * iq[..., 1]).astype(np.complex64).ravel()
    else:
        raise SystemExit("--iq-npy must contain complex samples or an I/Q array with shape (..., 2).")

    if iq.size == 0:
        raise SystemExit("--iq-npy file is empty.")

    start = time.monotonic()
    frames = 0
    offset = 0

    while should_continue(start, frames, args.duration, args.frames) and offset < iq.size:
        chunk = iq[offset : offset + args.block_size]
        offset += args.block_size

        if chunk.size < max(64, min(args.nperseg, args.block_size)):
            break

        handle_frame(chunk, analyzer, args, output)
        frames += 1


def handle_frame(
    iq: np.ndarray,
    analyzer: SpectralAnalyzer,
    args: argparse.Namespace,
    output: TextIO | None,
) -> None:
    result = analyzer.process(
        iq,
        max_points=args.max_points,
        include_threshold=args.full,
        include_waterfall=False,
    )
    summary = summarize_frame(result, top_n=args.top_n, full=args.full)
    line = json.dumps(summary, ensure_ascii=True, separators=(",", ":"))

    print(line, flush=True)

    if output is not None:
        output.write(line + "\n")
        output.flush()


def should_continue(start: float, frames: int, duration: float, max_frames: int) -> bool:
    if max_frames > 0 and frames >= max_frames:
        return False

    if duration > 0.0 and time.monotonic() - start >= duration:
        return False

    return True


def save_waterfall_npz(path: Path, analyzer: SpectralAnalyzer) -> None:
    if analyzer.freqs_hz is None or not analyzer.waterfall_db:
        return

    np.savez_compressed(
        path,
        freqs_hz=analyzer.freqs_hz,
        waterfall_db=np.stack(analyzer.waterfall_db, axis=0),
    )


def to_mhz(value: object) -> float | None:
    value_float = round_or_none(value, digits=6)
    return None if value_float is None else round(value_float / 1e6, 6)


def to_khz(value: object) -> float | None:
    value_float = round_or_none(value, digits=3)
    return None if value_float is None else round(value_float / 1e3, 3)


def round_or_none(value: object, digits: int = 2) -> float | None:
    if value is None:
        return None

    value_float = float(value)

    if not np.isfinite(value_float):
        return None

    return round(value_float, digits)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = SpectralAnalyzerConfig(
        sample_rate=args.sample_rate,
        center_freq=args.center_freq,
        nperseg=args.nperseg,
        waterfall_rows=args.waterfall_rows,
        cfar_train_cells=args.cfar_train,
        cfar_guard_cells=args.cfar_guard,
        cfar_threshold_db=args.cfar_threshold_db,
        min_signal_bins=args.min_bins,
        merge_gap_bins=args.merge_gap_bins,
        persistence_window=args.persistence_window,
        min_persistence=args.min_persistence,
        dc_notch_hz=args.dc_notch_hz,
    )
    analyzer = SpectralAnalyzer(cfg)
    output = args.jsonl.open("a", encoding="utf-8") if args.jsonl else None

    try:
        if args.iq_npy:
            run_from_npy(args, analyzer, output)
        else:
            run_from_sdr(args, analyzer, output)
    except KeyboardInterrupt:
        return 130
    finally:
        if args.waterfall_npz:
            save_waterfall_npz(args.waterfall_npz, analyzer)

        if output is not None:
            output.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
