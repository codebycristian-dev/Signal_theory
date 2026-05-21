from dataclasses import dataclass, asdict
from fractions import Fraction


@dataclass
class SDRConfig:
    # Radio
    center_freq: float = 105.7e6
    sample_rate: int = 250_000
    gain: float = 35.0
    rf_block_size: int = 32_768

    # Audio
    audio_rate: int = 48_000
    audio_block_size: int = 4_096
    volume: float = 0.70
    audio_gain: float = 1.50

    # DSP
    rf_lpf_cutoff: float = 100e3
    audio_lpf_cutoff: float = 15e3
    deemphasis_tau: float = 75e-6

    # PSD
    psd_nperseg: int = 1024
    psd_update_hz: float = 3.0
    psd_max_points: int = 900

    # Queues
    iq_queue_max: int = 16
    audio_queue_max: int = 125

    def as_dict(self) -> dict:
        return asdict(self)

    def resample_ratio(self) -> tuple[int, int]:
        """
        Devuelve up/down para resample_poly.
        Para 250 kHz -> 48 kHz: 24/125.
        """
        ratio = Fraction(self.audio_rate, self.sample_rate).limit_denominator(1000)
        return ratio.numerator, ratio.denominator

    def sanitize(self) -> None:
        self.center_freq = float(self.center_freq)
        self.sample_rate = int(self.sample_rate)
        self.gain = float(self.gain)
        self.audio_rate = int(self.audio_rate)
        self.rf_block_size = int(self.rf_block_size)
        self.audio_block_size = int(self.audio_block_size)
        self.volume = max(0.0, min(float(self.volume), 2.0))
        self.audio_gain = max(0.05, min(float(self.audio_gain), 20.0))
        self.psd_update_hz = max(0.5, min(float(self.psd_update_hz), 30.0))
        self.psd_nperseg = int(max(256, min(self.psd_nperseg, self.rf_block_size)))
        self.psd_max_points = int(max(200, min(self.psd_max_points, 3000)))
