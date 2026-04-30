#!/usr/bin/env python3
"""
RTL Sweep Pro — entry point.

Usage::

    python main.py                # auto-detect RTL-SDR, fall back to mock
    python main.py --mock         # force the mock backend
    python main.py --debug        # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import sys

from PyQt6.QtWidgets import QApplication

from rtl_sweep_pro.gui.main_window import MainWindow
from rtl_sweep_pro.utils.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="RTL Sweep Pro")
    parser.add_argument("--mock", action="store_true",
                        help="Force the synthetic SDR backend.")
    parser.add_argument("--debug", action="store_true",
                        help="Enable DEBUG-level logging.")
    args = parser.parse_args()

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    app = QApplication(sys.argv)
    app.setApplicationName("RTL Sweep Pro")
    app.setStyle("Fusion")

    win = MainWindow(prefer_mock=args.mock)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
