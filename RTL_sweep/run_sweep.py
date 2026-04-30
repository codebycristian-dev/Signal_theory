#!/usr/bin/env python3
"""
Headless / batch sweep runner.

Loads a config JSON, runs a single sweep, exports all formats, exits.

Usage::

    python run_sweep.py --config examples/example_config.json --out ./sweeps
    python run_sweep.py --config examples/example_config.json --mock
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from rtl_sweep_pro.config import SweepConfig
from rtl_sweep_pro.core.sweep_engine import SweepEngine, SweepResult
from rtl_sweep_pro.io.exporters import export_all
from rtl_sweep_pro.utils.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to a JSON config file.")
    parser.add_argument("--out", default=None, help="Override export directory.")
    parser.add_argument("--mock", action="store_true",
                        help="Force the synthetic SDR backend.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    cfg = SweepConfig.from_json(args.config)
    if args.out:
        cfg.export_dir = args.out

    errs = cfg.validate()
    if errs:
        print("Invalid configuration:", file=sys.stderr)
        for e in errs:
            print("  -", e, file=sys.stderr)
        return 2

    # Headless usage: instantiate the engine on the main thread without Qt.
    # We need to keep Qt event loop out, so we monkey-bypass signals and
    # execute the run() synchronously. The engine still works because all
    # signal emissions are non-blocking when no slot is connected (PyQt
    # tolerates emit-without-connect in the absence of an event loop, but
    # we still need a QCoreApplication for QObject construction).
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    captured: dict[str, SweepResult] = {}

    eng = SweepEngine(prefer_mock=args.mock)
    eng.sweep_finished.connect(lambda r: captured.setdefault("result", r))
    eng.log_message.connect(lambda m: print(m))
    eng.error.connect(lambda m: print("ERROR:", m, file=sys.stderr))

    eng.run(cfg)

    if "result" not in captured:
        print("Sweep produced no result.", file=sys.stderr)
        return 3

    paths = export_all(captured["result"], cfg.export_dir)
    print("\nWrote:")
    for p in paths:
        print("  ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
