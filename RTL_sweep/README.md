# RTL Sweep Pro

**Professional progressive spectrum sweep software for RTL-SDR devices.**

RTL Sweep Pro is engineered for *measurement quality* rather than visual speed.
It performs a frequency-by-frequency, slow, controlled sweep with proper tuner
settling, transient discard, windowed FFT averaging, calibration, and
traceable data export (CSV, HDF5, JSON metadata, SigMF, PNG).

It is intended for RF engineers, spectrum monitoring, weak signal detection,
EMC pre-compliance, scientific measurement, and any application where a fast
"realtime FFT" is **not** good enough.

---

## 1. Theory of Operation

### 1.1 Why "frequency by frequency"?

A single FFT only resolves the instantaneous bandwidth of the SDR
(`sample_rate`, ~2.4 MHz on RTL-SDR). To measure a wider span (for example,
24 MHz – 1.7 GHz) the receiver must be retuned through a **sequence of
center frequencies**. After every retune the device is *not* in a clean
measurement state: the PLL is settling, the AGC/DC-offset corrector is
re-converging, the USB pipeline still contains samples from the previous
frequency, and the tuner can output transients.

RTL Sweep Pro models this explicitly. For every center frequency `fc` it runs:

```
tune(fc)
   → settle_delay        (configurable, e.g. 5–50 ms)
   → discard N samples   (configurable, e.g. 4096–65536)
   → capture M samples
   → split into FFT blocks with overlap
   → apply window (Hann / Hamming / Blackman-Harris / Flat-top / Kaiser)
   → FFT, |X|², scale by window NENBW & coherent gain  → PSD
   → average K blocks (Welch-style)
   → keep only the usable bandwidth around fc
   → write segment to global spectrum
   → advance to fc + step
```

### 1.2 Step frequency criterion

The step is **not** arbitrary. It must satisfy:

```
step_frequency  <  usable_bandwidth
usable_bandwidth = bandwidth_useful_fraction · sample_rate
```

`bandwidth_useful_fraction` defaults to `0.75` (the classical guideline that
discards the spectral edges where the analog and digital filter rolloff,
DC spike, and image artefacts dominate). With `sample_rate = 2.4 MS/s` and
`fraction = 0.75` the usable bandwidth per tune is `1.8 MHz` and a safe step is
≤ `1.6 MHz` (some overlap), not `2.4 MHz`.

The configuration validator rejects any `step ≥ usable_bandwidth` and warns
when `step > 0.9 · usable_bandwidth` (insufficient overlap).

### 1.3 Calibrated PSD

Power for bin `k` of an FFT of length `N` with window `w[n]` is:

```
P_dBFS[k] = 10·log10( |X[k]|² / (N · ∑w[n]²) ) - 10·log10(fs / N · NENBW_correction)
```

Internally the engine computes:

* **Coherent gain** `CG = (1/N)·∑w[n]`
* **NENBW** `= N · ∑w[n]² / (∑w[n])²` (in bins)
* **Resolution bandwidth** `RBW = NENBW · fs / N` (Hz)

PSD is reported in **dB(W/Hz)** referenced to ADC full-scale, then offset by
the user-supplied `calibration_offset_db` to obtain dBm. The offset can be
derived from a known signal generator measurement.

### 1.4 Welch averaging

Per center frequency, samples are split into overlapping FFT blocks
(default 50 % overlap) and the *power* spectra (not voltages) are averaged.
This trades temporal resolution for variance reduction:

```
σ²(P_avg) ≈ σ²(P) / K_eff       where K_eff depends on overlap & window
```

The number of averages `K`, FFT size `N_FFT`, overlap, and capture length
together determine the integration time per step.

### 1.5 Spectrum stitching

For each tune `fc_i`, only the bins within
`[fc_i - usable_bw/2 , fc_i + usable_bw/2]` are kept. Adjacent segments are
stitched on the global frequency grid. In overlap regions the engine uses a
**linear cross-fade** (configurable: `mean`, `max-hold`, `min-hold`) to avoid
discontinuities ("step ladder" artefacts) without losing peak signals.

---

## 2. Installation

```bash
# 1. System package for librtlsdr (Linux example)
sudo apt install librtlsdr0 librtlsdr-dev rtl-sdr

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run
python main.py
```

On Windows install the Zadig driver for the RTL-SDR and ensure
`librtlsdr.dll` is on `PATH`. On macOS use `brew install librtlsdr`.

If no RTL-SDR is detected the application starts in **Mock SDR** mode
which generates a realistic synthetic spectrum (broadband noise floor +
configurable carriers + a wandering weak signal). This lets you exercise the
full GUI, sweep engine and exporters without hardware.

---

## 3. Quick start

1. Launch `python main.py`.
2. Set `Start = 88 MHz`, `Stop = 108 MHz` (FM band).
3. Sample rate `2.4 MS/s`, FFT size `8192`, averages `16`,
   window `blackmanharris`, settle `20 ms`, discard `8192` samples.
4. Click **Validate** (the status bar reports RBW and step margin).
5. Click **Start sweep**.
6. Use **Export…** to save CSV + HDF5 + JSON + SigMF + PNG snapshots.

---

## 4. Project layout

```
rtl_sweep_pro/
├── main.py                          # GUI entry point
├── requirements.txt
├── rtl_sweep_pro/
│   ├── config.py                    # SweepConfig dataclass + validation
│   ├── core/
│   │   ├── sdr_controller.py        # RTL-SDR abstraction (real + mock)
│   │   ├── dsp.py                   # Windows, FFT, Welch averaging, PSD
│   │   ├── calibration.py           # dBFS → dBm conversion
│   │   ├── peak_detector.py         # Threshold + prominence peak search
│   │   └── sweep_engine.py          # State machine, threaded
│   ├── io/
│   │   ├── exporters.py             # CSV, HDF5, JSON, SigMF, PNG
│   │   └── session.py               # Session manifest
│   ├── gui/
│   │   ├── main_window.py
│   │   ├── control_panel.py
│   │   ├── spectrum_view.py
│   │   └── waterfall_view.py
│   └── utils/logging_setup.py
├── tests/test_dsp.py
└── examples/example_config.json
```

---

## 5. License

MIT. See `LICENSE`.
