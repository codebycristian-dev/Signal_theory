"""
rtl_sweep_pro.config
====================

Strongly-typed sweep configuration with self-validation.

The :class:`SweepConfig` dataclass collects every parameter exposed in the
GUI / CLI and enforces the engineering constraints discussed in the README
(in particular ``step_frequency < usable_bandwidth``).

All frequencies are stored in **Hz** and all times in **seconds** (SI units)
so that the rest of the code base never has to do unit conversions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Valid window names accepted by scipy.signal.get_window.
VALID_WINDOWS = (
    "hann",
    "hamming",
    "blackman",
    "blackmanharris",
    "flattop",
    "kaiser",
    "boxcar",
    "nuttall",
)

# Stitching modes for overlap regions.
STITCH_MODES = ("crossfade", "mean", "max", "min")

# RTL-SDR hardware envelope (R820T / R820T2 tuner range).
RTL_FMIN_HZ = 24e6
RTL_FMAX_HZ = 1.766e9
RTL_FS_MIN = 225_000
RTL_FS_MAX = 3_200_000


# --------------------------------------------------------------------------- #
# Dataclass
# --------------------------------------------------------------------------- #

@dataclass
class SweepConfig:
    """All configurable parameters of a sweep.

    Times are in seconds; frequencies in Hz; sizes in samples; gain in dB.
    """

    # ----- frequency plan ------------------------------------------------ #
    f_start: float = 88e6
    f_stop: float = 108e6
    step_frequency: float = 1.6e6        # must be < usable_bandwidth
    sample_rate: float = 2.4e6
    bandwidth_useful_fraction: float = 0.75   # 0 < x ≤ 1

    # ----- tuner ---------------------------------------------------------- #
    gain_db: float = 30.0                # manual gain
    agc_enabled: bool = False
    freq_correction_ppm: int = 0
    settle_time_s: float = 0.020         # PLL/AGC settle
    discard_samples: int = 8192          # transient discard after retune

    # ----- DSP ------------------------------------------------------------ #
    fft_size: int = 8192
    n_averages: int = 16                 # blocks averaged per fc
    overlap_fraction: float = 0.5        # 0 ≤ x < 1
    window: str = "blackmanharris"
    integration_time_s: float = 0.0      # 0 ⇒ derived from n_averages

    # ----- detection ------------------------------------------------------ #
    peak_threshold_db: float = -70.0
    peak_min_prominence_db: float = 6.0
    peak_min_distance_hz: float = 5e3

    # ----- calibration ---------------------------------------------------- #
    calibration_offset_db: float = 0.0   # added to dBFS to get dBm

    # ----- run mode ------------------------------------------------------- #
    continuous: bool = False
    stitch_mode: str = "crossfade"

    # ----- I/O ------------------------------------------------------------ #
    export_dir: str = "./sweeps"
    session_name: str = "session"

    # ----- diagnostics ---------------------------------------------------- #
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Derived quantities
    # ------------------------------------------------------------------ #
    @property
    def usable_bandwidth(self) -> float:
        return self.bandwidth_useful_fraction * self.sample_rate

    @property
    def rbw_hz(self) -> float:
        """Resolution bandwidth (uncorrected by NENBW)."""
        return self.sample_rate / self.fft_size

    @property
    def n_steps(self) -> int:
        if self.step_frequency <= 0:
            return 0
        return int(round((self.f_stop - self.f_start) / self.step_frequency)) + 1

    @property
    def samples_per_capture(self) -> int:
        """Capture length needed for ``n_averages`` overlapped FFT blocks."""
        hop = max(1, int(self.fft_size * (1.0 - self.overlap_fraction)))
        return self.fft_size + hop * (self.n_averages - 1)

    @property
    def integration_time_effective_s(self) -> float:
        """Real integration time per fc (capture only, ignores settle)."""
        if self.integration_time_s > 0:
            return self.integration_time_s
        return self.samples_per_capture / self.sample_rate

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> list[str]:
        """Return a list of error messages. Empty list ⇒ valid."""
        errs: list[str] = []

        # Frequencies
        if not (RTL_FMIN_HZ <= self.f_start < self.f_stop <= RTL_FMAX_HZ):
            errs.append(
                f"f_start/f_stop must lie in [{RTL_FMIN_HZ/1e6:.1f},"
                f" {RTL_FMAX_HZ/1e6:.1f}] MHz and f_start < f_stop."
            )

        # Sample rate
        if not (RTL_FS_MIN <= self.sample_rate <= RTL_FS_MAX):
            errs.append(
                f"sample_rate must be in [{RTL_FS_MIN}, {RTL_FS_MAX}] Hz."
            )

        # Useful fraction
        if not (0.1 < self.bandwidth_useful_fraction <= 1.0):
            errs.append("bandwidth_useful_fraction must be in (0.1, 1.0].")

        # Step vs usable bandwidth — the central engineering rule
        if self.step_frequency <= 0:
            errs.append("step_frequency must be > 0.")
        elif self.step_frequency >= self.usable_bandwidth:
            errs.append(
                f"step_frequency ({self.step_frequency/1e3:.1f} kHz) must be"
                f" strictly less than usable_bandwidth"
                f" ({self.usable_bandwidth/1e3:.1f} kHz)."
            )

        # FFT
        if self.fft_size < 64 or (self.fft_size & (self.fft_size - 1)):
            errs.append("fft_size must be a power of two and ≥ 64.")
        if not (1 <= self.n_averages <= 4096):
            errs.append("n_averages must be in [1, 4096].")
        if not (0.0 <= self.overlap_fraction < 1.0):
            errs.append("overlap_fraction must be in [0, 1).")
        if self.window not in VALID_WINDOWS:
            errs.append(f"window must be one of {VALID_WINDOWS}.")

        # Tuner timings
        if self.settle_time_s < 0 or self.settle_time_s > 1.0:
            errs.append("settle_time_s must be in [0, 1] s.")
        if self.discard_samples < 0 or self.discard_samples > 1_048_576:
            errs.append("discard_samples must be in [0, 1_048_576].")

        # Gain
        if not (-10.0 <= self.gain_db <= 60.0):
            errs.append("gain_db must be in [-10, 60] dB.")

        # Stitching
        if self.stitch_mode not in STITCH_MODES:
            errs.append(f"stitch_mode must be one of {STITCH_MODES}.")

        return errs

    def warnings(self) -> list[str]:
        """Soft warnings that don't invalidate the config."""
        warns: list[str] = []
        if self.step_frequency > 0.9 * self.usable_bandwidth:
            warns.append(
                "step_frequency > 0.9·usable_bandwidth — low overlap; consider"
                " a smaller step for smoother stitching."
            )
        if self.discard_samples < 4096:
            warns.append(
                "discard_samples < 4096 — tuner transients may leak into"
                " the measurement on some R820T tuners."
            )
        if self.settle_time_s < 0.005:
            warns.append("settle_time_s < 5 ms — PLL may not be settled.")
        if self.fft_size > 65536:
            warns.append("fft_size very large — RAM and latency increase.")
        return warns

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SweepConfig":
        # Drop unknown keys silently to remain forward-compatible
        valid_keys = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**clean)

    @classmethod
    def from_json(cls, path: str | Path) -> "SweepConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))
