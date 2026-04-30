"""
rtl_sweep_pro.gui.main_window
=============================

Top-level :class:`QMainWindow`. Owns the control panel, the spectrum view,
the waterfall view, the status bar, and the running :class:`SweepThread`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDockWidget, QFileDialog, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QSplitter, QStatusBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..config import SweepConfig
from ..core.peak_detector import Peak
from ..core.sweep_engine import SweepResult, SweepState, SweepThread
from ..io.exporters import export_all
from .control_panel import ControlPanel
from .spectrum_view import SpectrumView
from .waterfall_view import WaterfallView

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, prefer_mock: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("RTL Sweep Pro")
        self.resize(1400, 850)
        self._thread: Optional[SweepThread] = None
        self._prefer_mock = prefer_mock
        self._last_result: Optional[SweepResult] = None

        # ----- central widgets ---------------------------------------- #
        self.spectrum = SpectrumView()
        self.waterfall = WaterfallView()
        tabs = QTabWidget()
        tabs.addTab(self.spectrum, "Spectrum")
        tabs.addTab(self.waterfall, "Waterfall")

        # log view at the bottom
        self.log_view = QTextEdit(readOnly=True)
        self.log_view.setMaximumHeight(140)
        self.log_view.setStyleSheet(
            "QTextEdit { background:#0c0f12; color:#cccccc; "
            "font-family: monospace; font-size: 11px; }"
        )

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tabs)
        splitter.addWidget(self.log_view)
        splitter.setSizes([700, 140])
        right_lay.addWidget(splitter)
        self.setCentralWidget(right)

        # ----- left dock (control panel) ----------------------------- #
        self.control_panel = ControlPanel()
        dock = QDockWidget("Sweep configuration", self)
        dock.setWidget(self.control_panel)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        # ----- status bar -------------------------------------------- #
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self.lbl_state = QLabel("idle")
        self.lbl_state.setMinimumWidth(100)
        self.lbl_fc = QLabel("fc: —")
        self.lbl_fc.setMinimumWidth(180)
        self.lbl_rbw = QLabel("RBW: —")
        self.lbl_rbw.setMinimumWidth(140)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(280)
        sb.addWidget(self.lbl_state)
        sb.addWidget(self.lbl_fc)
        sb.addWidget(self.lbl_rbw)
        sb.addPermanentWidget(self.progress)

        # ----- menus -------------------------------------------------- #
        self._build_menus()

        # ----- wiring ------------------------------------------------- #
        self.control_panel.sweepStartRequested.connect(self.start_sweep)
        self.control_panel.sweepStopRequested.connect(self.stop_sweep)
        self.control_panel.sweepPauseToggled.connect(self.pause_sweep)
        self.control_panel.exportRequested.connect(self.export_last)
        self.control_panel.validateRequested.connect(self.validate_config)

        self._log("RTL Sweep Pro ready.")
        if prefer_mock:
            self._log("MOCK mode forced via CLI.")

    # ------------------------------------------------------------------ #
    # Menu
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        a_load = QAction("Load configuration…", self)
        a_save = QAction("Save configuration…", self)
        a_export = QAction("Export last sweep…", self)
        a_quit = QAction("Quit", self)

        a_load.triggered.connect(self.action_load_config)
        a_save.triggered.connect(self.action_save_config)
        a_export.triggered.connect(self.export_last)
        a_quit.triggered.connect(self.close)

        m_file.addAction(a_load)
        m_file.addAction(a_save)
        m_file.addSeparator()
        m_file.addAction(a_export)
        m_file.addSeparator()
        m_file.addAction(a_quit)

        m_help = self.menuBar().addMenu("&Help")
        a_about = QAction("About RTL Sweep Pro", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

    def _about(self) -> None:
        QMessageBox.information(
            self, "About RTL Sweep Pro",
            "<b>RTL Sweep Pro 1.0</b><br>"
            "Professional progressive spectrum sweep for RTL-SDR.<br><br>"
            "MIT License.",
        )

    # ------------------------------------------------------------------ #
    # Config persistence
    # ------------------------------------------------------------------ #
    def action_load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load configuration", "", "JSON (*.json)",
        )
        if not path:
            return
        try:
            cfg = SweepConfig.from_json(path)
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        self._apply_config_to_ui(cfg)
        self._log(f"Loaded configuration from {path}")

    def action_save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save configuration", "config.json", "JSON (*.json)",
        )
        if not path:
            return
        cfg = self.control_panel.config()
        cfg.to_json(path)
        self._log(f"Saved configuration to {path}")

    def _apply_config_to_ui(self, cfg: SweepConfig) -> None:
        cp = self.control_panel
        cp.f_start.setValue(cfg.f_start / 1e6)
        cp.f_stop.setValue(cfg.f_stop / 1e6)
        cp.step.setValue(cfg.step_frequency / 1e6)
        cp.fs.setValue(cfg.sample_rate / 1e6)
        cp.bw_frac.setValue(cfg.bandwidth_useful_fraction)
        cp.gain.setValue(cfg.gain_db)
        cp.agc.setChecked(cfg.agc_enabled)
        cp.ppm.setValue(cfg.freq_correction_ppm)
        cp.settle.setValue(cfg.settle_time_s * 1e3)
        cp.discard.setValue(cfg.discard_samples)
        cp.fft_size.setValue(cfg.fft_size)
        cp.n_avg.setValue(cfg.n_averages)
        cp.overlap.setValue(cfg.overlap_fraction)
        cp.window.setCurrentText(cfg.window)
        cp.cal_offset.setValue(cfg.calibration_offset_db)
        cp.thr.setValue(cfg.peak_threshold_db)
        cp.prom.setValue(cfg.peak_min_prominence_db)
        cp.dist.setValue(cfg.peak_min_distance_hz / 1e3)
        cp.continuous.setChecked(cfg.continuous)
        cp.stitch.setCurrentText(cfg.stitch_mode)
        cp.export_dir.setText(cfg.export_dir)
        cp.session_name.setText(cfg.session_name)

    # ------------------------------------------------------------------ #
    # Sweep control
    # ------------------------------------------------------------------ #
    def validate_config(self, cfg: SweepConfig) -> None:
        errs = cfg.validate()
        warns = cfg.warnings()
        if errs:
            QMessageBox.critical(
                self, "Configuration invalid",
                "Errors:\n  • " + "\n  • ".join(errs),
            )
            return
        msg = f"OK. RBW = {cfg.rbw_hz:.2f} Hz; {cfg.n_steps} steps."
        if warns:
            msg += "\n\nWarnings:\n  • " + "\n  • ".join(warns)
        QMessageBox.information(self, "Configuration valid", msg)

    def start_sweep(self, cfg: SweepConfig) -> None:
        if self._thread and self._thread.isRunning():
            self._log("Sweep already running.")
            return
        errs = cfg.validate()
        if errs:
            QMessageBox.critical(
                self, "Cannot start sweep",
                "Errors:\n  • " + "\n  • ".join(errs),
            )
            return
        self.spectrum.reset()
        self.progress.setRange(0, max(1, cfg.n_steps))
        self.progress.setValue(0)

        self._thread = SweepThread(cfg, prefer_mock=self._prefer_mock)
        eng = self._thread.engine
        eng.state_changed.connect(self._on_state)
        eng.progress.connect(self._on_progress)
        eng.sweep_partial.connect(self._on_partial)
        eng.sweep_finished.connect(self._on_finished)
        eng.error.connect(self._on_error)
        eng.log_message.connect(self._log)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self.control_panel.set_running(True)

    def stop_sweep(self) -> None:
        if self._thread and self._thread.isRunning():
            self._log("Stop requested.")
            self._thread.engine.request_stop()

    def pause_sweep(self, paused: bool) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.engine.request_pause(paused)
            self._log("Paused." if paused else "Resumed.")

    # ------------------------------------------------------------------ #
    # Engine signal handlers
    # ------------------------------------------------------------------ #
    def _on_state(self, state: str) -> None:
        self.lbl_state.setText(state)

    def _on_progress(self, step: int, total: int, fc_hz: float) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(step)
        self.lbl_fc.setText(f"fc: {fc_hz/1e6:.3f} MHz  ({step}/{total})")

    def _on_partial(self, freqs, psd, coverage) -> None:
        self.spectrum.update_spectrum(freqs, psd)

    def _on_finished(self, result: SweepResult) -> None:
        self._last_result = result
        self.lbl_rbw.setText(f"RBW: {result.rbw_hz:.1f} Hz")
        self.spectrum.update_spectrum(result.freqs_hz, result.psd_db)
        self.spectrum.set_peaks(result.peaks)
        self.spectrum.autorange_y(result.psd_db)
        self.waterfall.add_pass(result.freqs_hz, result.psd_db)
        self._log(
            f"Pass complete — {len(result.peaks)} peaks; "
            f"duration {result.finished_unix - result.started_unix:.1f} s."
        )

    def _on_error(self, msg: str) -> None:
        self._log(f"[ERROR] {msg}")
        QMessageBox.critical(self, "Sweep error", msg)

    def _on_thread_finished(self) -> None:
        self.control_panel.set_running(False)
        self._log("Worker thread finished.")
        self._thread = None

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def export_last(self) -> None:
        if self._last_result is None:
            QMessageBox.information(
                self, "Nothing to export",
                "Run a sweep first.",
            )
            return
        cfg = self._last_result.config
        out_dir = QFileDialog.getExistingDirectory(
            self, "Select export directory", cfg.export_dir,
        )
        if not out_dir:
            return
        try:
            paths = export_all(self._last_result, out_dir)
            self._log("Exported:\n  " + "\n  ".join(str(p) for p in paths))
            QMessageBox.information(
                self, "Export complete",
                f"Wrote {len(paths)} file(s) into:\n{out_dir}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            self._log(f"[ERROR] export: {e}")

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        self.log_view.append(msg)
        logger.info(msg)

    def closeEvent(self, ev) -> None:  # noqa: D401 — Qt override
        if self._thread and self._thread.isRunning():
            self._thread.engine.request_stop()
            self._thread.wait(2000)
        super().closeEvent(ev)
