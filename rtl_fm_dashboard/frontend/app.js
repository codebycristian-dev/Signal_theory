const API_BASE = "http://127.0.0.1:8000";
const WS_URL = "ws://127.0.0.1:8000/ws/psd";

const $ = (id) => document.getElementById(id);

const state = {
  ws: null,
  lastPsd: null,
  connected: false,
};

const VALIDATION_LIMITS = {
  // Rango práctico típico del RTL-SDR con tuner R820T/R820T2.
  // El dashboard está orientado a FM comercial, por eso se advierte fuera de 87.5-108 MHz,
  // pero no se bloquea mientras la frecuencia siga dentro del rango físico del SDR.
  freqMhz: { min: 24.0, max: 1766.0, fmMin: 87.5, fmMax: 108.0 },
  gainDb: { min: 0.0, max: 49.6 },
  sampleRate: { min: 225000, max: 2400000 },
  rfCutoffKhz: { min: 20.0, maxAbsolute: 250.0 },
  volume: { min: 0.0, max: 2.0 },
};

function parseNumberFlexible(raw) {
  if (raw === null || raw === undefined) return NaN;
  const normalized = String(raw).trim().replace(",", ".");
  if (normalized === "") return NaN;
  return Number(normalized);
}

function formatInputDecimal(value, digits = 1) {
  if (!Number.isFinite(value)) return "";
  return Number(value).toFixed(digits).replace(/\.0+$/, "");
}

function setInputState(id, severity = "ok") {
  const el = $(id);
  if (!el) return;
  el.classList.toggle("input-error", severity === "error");
  el.classList.toggle("input-warning", severity === "warning");
}

function setValidationBox(errors, warnings) {
  const box = $("validation-box");
  if (!box) return;

  box.classList.remove("ok", "warn", "error");

  if (errors.length > 0) {
    box.classList.add("error");
    box.innerHTML = `<strong>Corrige antes de iniciar:</strong><br>${errors.map((e) => `• ${e}`).join("<br>")}`;
    return;
  }

  if (warnings.length > 0) {
    box.classList.add("warn");
    box.innerHTML = `<strong>Advertencia:</strong><br>${warnings.map((w) => `• ${w}`).join("<br>")}`;
    return;
  }

  box.classList.add("ok");
  box.textContent = "Parámetros dentro de rango.";
}

function validateConfigUi({ updatePanel = true } = {}) {
  const errors = [];
  const warnings = [];

  const freqMhz = parseNumberFlexible($("freq-mhz").value);
  const gain = parseNumberFlexible($("gain").value);
  const sampleRateRaw = parseNumberFlexible($("sample-rate").value);
  const sampleRate = Math.round(sampleRateRaw);
  const rfCutoffKhz = parseNumberFlexible($("rf-cutoff-khz").value);
  const volume = parseNumberFlexible($("volume").value);

  const inputSeverity = {
    "freq-mhz": "ok",
    gain: "ok",
    "sample-rate": "ok",
    "rf-cutoff-khz": "ok",
    volume: "ok",
  };

  const { freqMhz: f, gainDb: g, sampleRate: sr, rfCutoffKhz: rf, volume: vol } = VALIDATION_LIMITS;

  if (!Number.isFinite(freqMhz)) {
    errors.push("La frecuencia central debe ser un número. Puedes usar punto o coma decimal, por ejemplo 105.7 o 105,7.");
    inputSeverity["freq-mhz"] = "error";
  } else if (freqMhz < f.min || freqMhz > f.max) {
    errors.push(`La frecuencia central debe estar entre ${f.min} MHz y ${f.max} MHz.`);
    inputSeverity["freq-mhz"] = "error";
  } else if (freqMhz < f.fmMin || freqMhz > f.fmMax) {
    warnings.push(`Estás fuera de la banda FM comercial (${f.fmMin} a ${f.fmMax} MHz). El SDR puede sintonizar, pero este demodulador está pensado para WBFM.`);
    inputSeverity["freq-mhz"] = "warning";
  }

  if (!Number.isFinite(gain)) {
    errors.push("La ganancia debe ser un número en dB.");
    inputSeverity.gain = "error";
  } else if (gain < g.min || gain > g.max) {
    errors.push(`La ganancia RTL debe estar entre ${g.min} dB y ${g.max} dB.`);
    inputSeverity.gain = "error";
  }

  if (!Number.isFinite(sampleRateRaw)) {
    errors.push("El sample rate RF debe ser un número entero en muestras por segundo.");
    inputSeverity["sample-rate"] = "error";
  } else if (!Number.isInteger(sampleRateRaw)) {
    warnings.push(`El sample rate se redondeará a ${sampleRate} S/s.`);
    inputSeverity["sample-rate"] = "warning";
  }

  if (Number.isFinite(sampleRateRaw)) {
    if (sampleRate < sr.min || sampleRate > sr.max) {
      errors.push(`El sample rate RF debe estar entre ${sr.min} y ${sr.max} S/s para esta configuración.`);
      inputSeverity["sample-rate"] = "error";
    }
  }

  const dynamicRfMaxKhz = Number.isFinite(sampleRate)
    ? Math.min(rf.maxAbsolute, 0.45 * sampleRate / 1000.0)
    : rf.maxAbsolute;

  if (!Number.isFinite(rfCutoffKhz)) {
    errors.push("El corte RF LPF debe ser un número en kHz.");
    inputSeverity["rf-cutoff-khz"] = "error";
  } else if (rfCutoffKhz < rf.min) {
    errors.push(`El corte RF LPF debe ser mayor o igual que ${rf.min} kHz.`);
    inputSeverity["rf-cutoff-khz"] = "error";
  } else if (rfCutoffKhz > dynamicRfMaxKhz) {
    errors.push(`El corte RF LPF no puede superar ${dynamicRfMaxKhz.toFixed(1)} kHz con Fs = ${sampleRate} S/s. Debe quedar por debajo de Nyquist.`);
    inputSeverity["rf-cutoff-khz"] = "error";
  } else if (rfCutoffKhz < 60 || rfCutoffKhz > 120) {
    warnings.push("Para FM comercial suele funcionar mejor un corte RF entre 60 kHz y 120 kHz.");
    inputSeverity["rf-cutoff-khz"] = inputSeverity["rf-cutoff-khz"] === "error" ? "error" : "warning";
  }

  if (!Number.isFinite(volume)) {
    errors.push("El volumen debe ser un número válido.");
    inputSeverity.volume = "error";
  } else if (volume < vol.min || volume > vol.max) {
    errors.push(`El volumen debe estar entre ${vol.min} y ${vol.max}.`);
    inputSeverity.volume = "error";
  }

  if (updatePanel) {
    Object.entries(inputSeverity).forEach(([id, severity]) => setInputState(id, severity));
    setValidationBox(errors, warnings);

    const hasErrors = errors.length > 0;
    $("start-btn").disabled = hasErrors;
    $("apply-btn").disabled = hasErrors;
  }

  return {
    ok: errors.length === 0,
    errors,
    warnings,
    values: {
      center_freq: freqMhz * 1e6,
      gain,
      sample_rate: sampleRate,
      rf_lpf_cutoff: rfCutoffKhz * 1e3,
      volume,
    },
  };
}

function normalizeDecimalInputs() {
  const freq = parseNumberFlexible($("freq-mhz").value);
  const gain = parseNumberFlexible($("gain").value);
  const rfCutoff = parseNumberFlexible($("rf-cutoff-khz").value);

  if (Number.isFinite(freq)) $("freq-mhz").value = formatInputDecimal(freq, 3);
  if (Number.isFinite(gain)) $("gain").value = formatInputDecimal(gain, 1);
  if (Number.isFinite(rfCutoff)) $("rf-cutoff-khz").value = formatInputDecimal(rfCutoff, 1);
}

function getConfigFromUi() {
  const validation = validateConfigUi();

  if (!validation.ok) {
    throw new Error(validation.errors.join("\n"));
  }

  return validation.values;
}

async function api(path, method = "GET", body = null) {
  const options = { method, headers: { "Content-Type": "application/json" } };

  if (body !== null) {
    options.body = JSON.stringify(body);
  }

  const res = await fetch(`${API_BASE}${path}`, options);

  if (!res.ok) {
    throw new Error(`${method} ${path} -> HTTP ${res.status}`);
  }

  return await res.json();
}

function setConnection(connected, text) {
  state.connected = connected;
  const pill = $("connection-pill");
  pill.classList.toggle("online", connected);
  pill.classList.toggle("offline", !connected);
  $("connection-text").textContent = text;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

function updateUiFromStatus(status) {
  $("running").textContent = status.running ? "Ejecutando" : "Detenido";

  $("audio-rms").textContent = formatNumber(status.levels?.audio_rms, 4);
  $("underflows").textContent = status.underflows ?? "—";

  const q = status.queues || {};
  $("queue-status").textContent =
    `IQ: ${q.iq ?? "—"}/${q.iq_max ?? "—"} · Audio: ${q.audio ?? "—"}/${q.audio_max ?? "—"}`;

  if (status.config) {
    $("chart-subtitle").textContent =
      `Fc ${(status.config.center_freq / 1e6).toFixed(3)} MHz · Fs ${(status.config.sample_rate / 1e3).toFixed(0)} kS/s`;
  }

  $("log").textContent = status.last_error || "Sin errores reportados.";
}

function updateUiFromPsd(psd) {
  if (!psd || !psd.metrics) return;

  const m = psd.metrics;
  $("peak").textContent = m.peak_mhz ? `${formatNumber(m.peak_mhz, 4)} MHz` : "—";
  $("offset").textContent = m.offset_khz !== null ? `${formatNumber(m.offset_khz, 1)} kHz` : "—";
  $("bw20").textContent = m.bw20_khz !== null ? `${formatNumber(m.bw20_khz, 1)} kHz` : "—";
}

function connectWs() {
  if (state.ws) {
    state.ws.close();
  }

  state.ws = new WebSocket(WS_URL);

  state.ws.onopen = () => {
    setConnection(true, "Backend conectado");
  };

  state.ws.onmessage = (event) => {
    const payload = JSON.parse(event.data);

    if (payload.status) {
      updateUiFromStatus(payload.status);
    }

    if (payload.data) {
      state.lastPsd = payload.data;
      updateUiFromPsd(payload.data);
      drawPsd(payload.data);
    }
  };

  state.ws.onerror = () => {
    setConnection(false, "Error de conexión");
  };

  state.ws.onclose = () => {
    setConnection(false, "Reconectando...");
    setTimeout(connectWs, 1200);
  };
}

function drawPsd(psd) {
  const canvas = $("psd-canvas");
  const ctx = canvas.getContext("2d");

  const width = canvas.width;
  const height = canvas.height;

  const freqs = psd.freq_mhz || [];
  const vals = psd.psd_db || [];

  ctx.clearRect(0, 0, width, height);

  const padL = 70;
  const padR = 24;
  const padT = 28;
  const padB = 54;

  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  // Fondo
  ctx.fillStyle = "rgba(3, 8, 18, 0.95)";
  ctx.fillRect(0, 0, width, height);

  if (freqs.length < 2 || vals.length < 2) {
    ctx.fillStyle = "#9aa7c7";
    ctx.font = "20px system-ui";
    ctx.fillText("Esperando PSD del backend...", padL, height / 2);
    return;
  }

  const xMin = Math.min(...freqs);
  const xMax = Math.max(...freqs);

  const sortedVals = [...vals].filter(Number.isFinite).sort((a, b) => a - b);
  const p = (q) => sortedVals[Math.floor((sortedVals.length - 1) * q)];

  let yMin = p(0.05);
  let yMax = p(0.995);

  if (!Number.isFinite(yMin) || !Number.isFinite(yMax) || yMax - yMin < 5) {
    yMin = -120;
    yMax = -30;
  }

  const xMap = (x) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  const yMap = (y) => padT + (1 - (y - yMin) / (yMax - yMin)) * plotH;

  // Grid
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "rgba(234,241,255,0.75)";
  ctx.font = "13px system-ui";

  for (let i = 0; i <= 8; i++) {
    const x = padL + (plotW * i) / 8;
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + plotH);
    ctx.stroke();

    const f = xMin + ((xMax - xMin) * i) / 8;
    ctx.fillText(f.toFixed(3), x - 22, height - 22);
  }

  for (let i = 0; i <= 6; i++) {
    const y = padT + (plotH * i) / 6;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + plotW, y);
    ctx.stroke();

    const db = yMax - ((yMax - yMin) * i) / 6;
    ctx.fillText(db.toFixed(1), 12, y + 4);
  }

  // Ejes
  ctx.strokeStyle = "rgba(255,255,255,0.22)";
  ctx.strokeRect(padL, padT, plotW, plotH);

  // Curva PSD con gradiente
  const grad = ctx.createLinearGradient(padL, padT, padL + plotW, padT);
  grad.addColorStop(0, "#66e3ff");
  grad.addColorStop(1, "#8b5cf6");

  ctx.strokeStyle = grad;
  ctx.lineWidth = 2.0;
  ctx.beginPath();

  for (let i = 0; i < freqs.length; i++) {
    const x = xMap(freqs[i]);
    const y = yMap(vals[i]);

    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }

  ctx.stroke();

  // Etiquetas
  ctx.fillStyle = "rgba(234,241,255,0.95)";
  ctx.font = "14px system-ui";
  ctx.fillText("PSD [dB/Hz]", 12, 22);
  ctx.fillText("Frecuencia [MHz]", width / 2 - 55, height - 8);
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    updateUiFromStatus(s);
  } catch (err) {
    setConnection(false, "Backend no disponible");
  }
}

async function submitRadioAction(path) {
  try {
    normalizeDecimalInputs();
    const validation = validateConfigUi();

    if (!validation.ok) {
      $("log").textContent = validation.errors.join("\n");
      return;
    }

    await api(path, "POST", validation.values);
    await refreshStatus();
  } catch (err) {
    $("log").textContent = err.message;
  }
}

$("start-btn").addEventListener("click", async () => {
  await submitRadioAction("/api/start");
});

$("stop-btn").addEventListener("click", async () => {
  try {
    await api("/api/stop", "POST");
    await refreshStatus();
  } catch (err) {
    $("log").textContent = err.message;
  }
});

$("apply-btn").addEventListener("click", async () => {
  await submitRadioAction("/api/config");
});

$("volume").addEventListener("input", () => {
  $("volume-value").textContent = Number($("volume").value).toFixed(2);
  validateConfigUi();
});

["freq-mhz", "gain", "sample-rate", "rf-cutoff-khz"].forEach((id) => {
  $(id).addEventListener("input", () => validateConfigUi());
  $(id).addEventListener("blur", () => {
    normalizeDecimalInputs();
    validateConfigUi();
  });
});

window.addEventListener("load", async () => {
  $("volume-value").textContent = Number($("volume").value).toFixed(2);
  validateConfigUi();
  connectWs();
  await refreshStatus();
});
