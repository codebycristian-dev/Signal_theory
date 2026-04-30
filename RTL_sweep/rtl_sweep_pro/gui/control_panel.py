"""
rtl_sweep_pro.gui.control_panel
===============================

Left-side control panel — every :class:`SweepConfig` parameter is exposed
as a labelled spinbox / combobox. The widget converts user input to a
fresh :class:`SweepConfig` on demand and validates it before each sweep.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import STITCH_MODES, VALID_WINDOWS, SweepConfig


def _spin(
    minimum: float, maximum: float, value: float,
    step: float = 1.0, decimals: int = 3, suffix: str = "",
) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def _ispin(minimum: int, maximum: int, value: int, step: int = 1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(minimum, maximum)
    s.setSingleStep(step)
    s.setValue(value)
    s.setKeyboardTracking(False)
    return s


class ControlPanel(QWidget):
    """Holds every editable parameter and emits ``configChanged``."""

    configChanged = pyqtSignal(object)        # SweepConfig
    sweepStartRequested = pyqtSignal(object)  # SweepConfig
    sweepStopRequested = pyqtSignal()
    sweepPauseToggled = pyqtSignal(bool)
    exportRequested = pyqtSignal()
    validateRequested = pyqtSignal(object)    # SweepConfig

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._wire_signals()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- Frequency plan ------------------------------------------- #
        freq_grp = QGroupBox("Frequency plan")
        freq_form = QFormLayout(freq_grp)
        self.f_start = _spin(24.0, 1766.0, 88.0, 0.1, 3, " MHz")
        self.f_stop = _spin(24.0, 1766.0, 108.0, 0.1, 3, " MHz")
        self.step = _spin(0.001, 100.0, 1.6, 0.05, 3, " MHz")
        self.fs = _spin(0.225, 3.2, 2.4, 0.05, 3, " MS/s")
        self.bw_frac = _spin(0.10, 1.0, 0.75, 0.05, 2)
        freq_form.addRow("Start:", self.f_start)
        freq_form.addRow("Stop:", self.f_stop)
        freq_form.addRow("Step:", self.step)
        freq_form.addRow("Sample rate:", self.fs)
        freq_form.addRow("Useful BW fraction:", self.bw_frac)
        root.addWidget(freq_grp)

        # --- Tuner ---------------------------------------------------- #
        tuner_grp = QGroupBox("Tuner")
        tuner_form = QFormLayout(tuner_grp)
        self.gain = _spin(-10.0, 60.0, 30.0, 0.5, 1, " dB")
        self.agc = QCheckBox("Enable AGC")
        self.ppm = _ispin(-200, 200, 0)
        self.settle = _spin(0.0, 1000.0, 20.0, 1.0, 1, " ms")
        self.discard = _ispin(0, 1_048_576, 8192, 1024)
        tuner_form.addRow("Gain:", self.gain)
        tuner_form.addRow("", self.agc)
        tuner_form.addRow("Freq. correction:", self.ppm)
        tuner_form.addRow("Settle time:", self.settle)
        tuner_form.addRow("Discard samples:", self.discard)
        root.addWidget(tuner_grp)

        # --- DSP ------------------------------------------------------ #
        dsp_grp = QGroupBox("DSP")
        dsp_form = QFormLayout(dsp_grp)
        self.fft_size = _ispin(64, 1_048_576, 8192, 64)
        self.n_avg = _ispin(1, 4096, 16, 1)
        self.overlap = _spin(0.0, 0.95, 0.5, 0.05, 2)
        self.window = QComboBox()
        self.window.addItems(VALID_WINDOWS)
        self.window.setCurrentText("blackmanharris")
        self.cal_offset = _spin(-200.0, 200.0, 0.0, 0.5, 2, " dB")
        dsp_form.addRow("FFT size:", self.fft_size)
        dsp_form.addRow("Averages per fc:", self.n_avg)
        dsp_form.addRow("Overlap:", self.overlap)
        dsp_form.addRow("Window:", self.window)
        dsp_form.addRow("Cal. offset:", self.cal_offset)
        root.addWidget(dsp_grp)

        # --- Detection / run mode ------------------------------------- #
        det_grp = QGroupBox("Detection & run mode")
        det_form = QFormLayout(det_grp)
        self.thr = _spin(-200.0, 50.0, -70.0, 1.0, 1, " dB")
        self.prom = _spin(0.0, 100.0, 6.0, 0.5, 1, " dB")
        self.dist = _spin(0.0, 1000.0, 5.0, 1.0, 1, " kHz")
        self.continuous = QCheckBox("Continuous sweep")
        self.stitch = QComboBox()
        self.stitch.addItems(STITCH_MODES)
        det_form.addRow("Peak threshold:", self.thr)
        det_form.addRow("Min prominence:", self.prom)
        det_form.addRow("Min peak distance:", self.dist)
        det_form.addRow("", self.continuous)
        det_form.addRow("Stitch mode:", self.stitch)
        root.addWidget(det_grp)

        # --- Output --------------------------------------------------- #
        io_grp = QGroupBox("Output")
        io_form = QFormLayout(io_grp)
        self.export_dir = QLineEdit("./sweeps")
        self.session_name = QLineEdit("session")
        io_form.addRow("Export directory:", self.export_dir)
        io_form.addRow("Session name:", self.session_name)
        root.addWidget(io_grp)

        # --- Buttons -------------------------------------------------- #
        btn_row = QHBoxLayout()
        self.btn_validate = QPushButton("Validate")
        self.btn_start = QPushButton("Start sweep")
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setCheckable(True)
        self.btn_stop = QPushButton("Stop")
        self.btn_export = QPushButton("Export…")
        for b in (self.btn_validate, self.btn_start, self.btn_pause,
                  self.btn_stop, self.btn_export):
            btn_row.addWidget(b)
        root.addLayout(btn_row)

        # --- Read-out (RBW etc.) ------------------------------------- #
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        self.summary.setFrameShape(QFrame.Shape.Panel)
        self.summary.setFrameShadow(QFrame.Shadow.Sunken)
        self.summary.setMinimumHeight(64)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.summary)

        root.addStretch(1)
        self._refresh_summary()

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #
    def _wire_signals(self) -> None:
        for w in (self.f_start, self.f_stop, self.step, self.fs, self.bw_frac,
                  self.gain, self.settle, self.cal_offset, self.overlap,
                  self.thr, self.prom, self.dist):
            w.valueChanged.connect(self._refresh_summary)
        for w in (self.fft_size, self.n_avg, self.discard, self.ppm):
            w.valueChanged.connect(self._refresh_summary)
        self.window.currentTextChanged.connect(self._refresh_summary)
        self.stitch.currentTextChanged.connect(self._refresh_summary)

        self.btn_validate.clicked.connect(
            lambda: self.validateRequested.emit(self.config())
        )
        self.btn_start.clicked.connect(
            lambda: self.sweepStartRequested.emit(self.config())
        )
        self.btn_stop.clicked.connect(self.sweepStopRequested.emit)
        self.btn_pause.toggled.connect(self.sweepPauseToggled.emit)
        self.btn_export.clicked.connect(self.exportRequested.emit)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def config(self) -> SweepConfig:
        return SweepConfig(
            f_start=self.f_start.value() * 1e6,
            f_stop=self.f_stop.value() * 1e6,
            step_frequency=self.step.value() * 1e6,
            sample_rate=self.fs.value() * 1e6,
            bandwidth_useful_fraction=self.bw_frac.value(),
            gain_db=self.gain.value(),
            agc_enabled=self.agc.isChecked(),
            freq_correction_ppm=int(self.ppm.value()),
            settle_time_s=self.settle.value() / 1e3,
            discard_samples=int(self.discard.value()),
            fft_size=int(self.fft_size.value()),
            n_averages=int(self.n_avg.value()),
            overlap_fraction=self.overlap.value(),
            window=self.window.currentText(),
            calibration_offset_db=self.cal_offset.value(),
            peak_threshold_db=self.thr.value(),
            peak_min_prominence_db=self.prom.value(),
            peak_min_distance_hz=self.dist.value() * 1e3,
            continuous=self.continuous.isChecked(),
            stitch_mode=self.stitch.currentText(),
            export_dir=self.export_dir.text() or "./sweeps",
            session_name=self.session_name.text() or "session",
        )

    def set_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        if not running:
            self.btn_pause.setChecked(False)

    # ------------------------------------------------------------------ #
    # Live summary
    # ------------------------------------------------------------------ #
    def _refresh_summary(self, *_) -> None:
        cfg = self.config()
        rbw = cfg.rbw_hz
        usable = cfg.usable_bandwidth
        margin = (usable - cfg.step_frequency) / usable if usable > 0 else 0.0
        n_steps = cfg.n_steps
        eff_int = cfg.integration_time_effective_s

        ok = not cfg.validate()
        warn = cfg.warnings()
        color = "#0a8a0a" if ok and not warn else ("#a86a00" if ok else "#a40000")

        msg = (
            f"<div style='color:{color}; font-family:monospace; font-size:11px;'>"
            f"<b>Steps:</b> {n_steps} &nbsp; "
            f"<b>RBW:</b> {rbw:.1f} Hz &nbsp; "
            f"<b>Usable BW:</b> {usable/1e3:.1f} kHz<br>"
            f"<b>Step / Usable:</b> {cfg.step_frequency/usable:.2f} "
            f"(margin {margin*100:.0f}%) &nbsp; "
            f"<b>Int. time:</b> {eff_int*1000:.1f} ms/fc<br>"
            f"<b>Capture:</b> {cfg.samples_per_capture} samples "
            f"= {cfg.samples_per_capture/cfg.sample_rate*1000:.1f} ms"
            f"</div>"
        )
        if cfg.validate():
            msg += "<div style='color:#a40000;'><b>Errors:</b> " + \
                "; ".join(cfg.validate()) + "</div>"
        if warn:
            msg += "<div style='color:#a86a00;'><b>Warnings:</b> " + \
                "; ".join(warn) + "</div>"
        self.summary.setText(msg)
        self.configChanged.emit(cfg)
