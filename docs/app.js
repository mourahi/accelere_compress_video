import { FFmpeg } from "./vendor/ffmpeg/index.js";

const CORE_MIRRORS = [
  "https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/esm",
  "https://unpkg.com/@ffmpeg/core@0.12.10/dist/esm",
];

const SPEED_LABELS = {
  1: "1×",
  1.25: "1,25×",
  1.5: "1,5×",
  2: "2×",
  3: "3×",
  4: "4×",
};

let ffmpeg = new FFmpeg();
let ffmpegLoadPromise = null;
let activeConversion = null;
let encodeWorker = null;
let keepAliveAbort = null;
let keepAliveAudio = null;
let keepAliveOsc = null;
const files = [];
const trims = [];
let selected = 0;
let objectUrl = "";
let resultBlob = null;
let busy = false;
let cancelFlag = false;
let fastEncodeOk = false;
let progressLocked = false;

const els = {
  add: document.getElementById("btn-add"),
  clear: document.getElementById("btn-clear"),
  start: document.getElementById("btn-start"),
  cancel: document.getElementById("btn-cancel"),
  remove: document.getElementById("btn-remove"),
  download: document.getElementById("btn-download"),
  copy: document.getElementById("btn-copy"),
  input: document.getElementById("file-input"),
  target: document.getElementById("target-mb"),
  real: document.getElementById("real-size"),
  mute: document.getElementById("mute"),
  speed: document.getElementById("speed"),
  list: document.getElementById("file-list"),
  meta: document.getElementById("meta"),
  summary: document.getElementById("summary"),
  outName: document.getElementById("out-name"),
  preview: document.getElementById("preview"),
  empty: document.getElementById("preview-empty"),
  play: document.getElementById("btn-play"),
  seek: document.getElementById("seek"),
  time: document.getElementById("time"),
  bar: document.getElementById("bar"),
  status: document.getElementById("status"),
  compat: document.getElementById("compat"),
  markInBtn: document.getElementById("btn-in"),
  markOutBtn: document.getElementById("btn-out"),
  resetTrim: document.getElementById("btn-reset-trim"),
  frame: document.getElementById("btn-frame"),
  trimLabel: document.getElementById("trim-label"),
  trimSpan: document.getElementById("trim-span"),
  markIn: document.getElementById("mark-in"),
  markOut: document.getElementById("mark-out"),
};

function formatSize(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
  return `${(bytes / 1024 ** 3).toFixed(2)} Go`;
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function currentFile() {
  return files[selected] || null;
}

function sourceDuration() {
  const duration = els.preview.duration;
  return Number.isFinite(duration) && duration > 0 ? duration : 0;
}

function currentTrim() {
  const duration = sourceDuration();
  const stored = trims[selected] || { start: 0, end: 0 };
  const start = Math.max(0, stored.start || 0);
  const end = stored.end > start ? stored.end : duration || start;
  return { start, end: duration ? Math.min(end, duration) : end };
}

function clipDuration() {
  const duration = sourceDuration();
  const trim = currentTrim();
  if (!duration) return 0;
  return Math.max(0.05, trim.end - trim.start);
}

function refreshTrimLabel() {
  if (els.trimLabel) {
    const duration = sourceDuration();
    if (!duration) {
      els.trimLabel.textContent = "Toute la vidéo";
    } else {
      const trim = currentTrim();
      els.trimLabel.textContent =
        trim.start <= 0.05 && duration - trim.end <= 0.05
          ? "Toute la vidéo"
          : `Coupe ${formatTime(trim.start)} → ${formatTime(trim.end)}`;
    }
  }
  updateTrimMarks();
}

function speedValue() {
  return Number(els.speed.value) || 1;
}

function targetMb() {
  const value = Number(String(els.target.value).replace(",", "."));
  return value > 0 ? value : null;
}

function atempoFilter(speed) {
  if (Math.abs(speed - 1) < 0.001) return "";
  const parts = [];
  let remaining = Math.max(0.25, Math.min(8, speed));
  while (remaining > 2.0001) {
    parts.push("atempo=2.0");
    remaining /= 2;
  }
  while (remaining < 0.4999) {
    parts.push("atempo=0.5");
    remaining /= 0.5;
  }
  parts.push(`atempo=${remaining.toFixed(4)}`);
  return parts.join(",");
}

function setProgress(pct, message, kind) {
  if (progressLocked && kind !== "err" && pct < 1) return;
  if (kind === "ok") {
    pct = 1;
    progressLocked = true;
  } else if (kind === "err") {
    progressLocked = false;
  } else if (pct <= 0) {
    progressLocked = false;
  }
  els.bar.style.width = `${Math.max(0, Math.min(100, pct * 100))}%`;
  if (message) els.status.textContent = message;
  els.status.classList.remove("ok", "err");
  if (kind) els.status.classList.add(kind);
}

function updateTrimMarks() {
  const duration = sourceDuration();
  if (!els.trimSpan || !els.markIn || !els.markOut) return;
  if (!duration) {
    els.trimSpan.style.display = "none";
    els.markIn.hidden = true;
    els.markOut.hidden = true;
    return;
  }
  const trim = currentTrim();
  const full = trim.start <= 0.05 && duration - trim.end <= 0.05;
  if (full) {
    els.trimSpan.style.display = "none";
    els.markIn.hidden = true;
    els.markOut.hidden = true;
    return;
  }
  const left = (trim.start / duration) * 100;
  const right = (trim.end / duration) * 100;
  els.trimSpan.style.display = "block";
  els.trimSpan.style.left = `${left}%`;
  els.trimSpan.style.width = `${Math.max(0.5, right - left)}%`;
  els.markIn.hidden = false;
  els.markOut.hidden = false;
  els.markIn.style.left = `${left}%`;
  els.markOut.style.left = `${right}%`;
}

function refreshSummary() {
  const file = currentFile();
  const target = targetMb();
  const speed = speedValue();
  if (!file) {
    els.real.textContent = "—";
    els.meta.textContent = "";
    els.summary.textContent = "";
    return;
  }
  els.real.textContent = formatSize(file.size);
  const duration = els.preview.duration;
  const dim =
    els.preview.videoWidth && els.preview.videoHeight
      ? `${els.preview.videoWidth}×${els.preview.videoHeight}  ·  `
      : "";
  els.meta.textContent = `${dim}${formatTime(duration)}`;
  if (!target) {
    els.summary.textContent = "Indiquez une taille en Mo.";
    return;
  }
  const outDuration = clipDuration() / speed;
  const mute = els.mute.checked ? "sans audio" : "avec audio";
  const trim = currentTrim();
  const cut =
    duration && (trim.start > 0.05 || duration - trim.end > 0.05) ? "  ·  coupe" : "";
  els.summary.textContent = `Export ≈ ${target} Mo  ·  ${SPEED_LABELS[speed] || speed + "×"}  ·  ${formatTime(outDuration)}  ·  ${mute}${cut}`;
  refreshTrimLabel();
  if (!els.outName.value.trim() || els.outName.dataset.auto !== "no") {
    const stem = file.name.replace(/\.[^.]+$/, "");
    const speedBit = speed !== 1 ? `_${String(speed).replace(".", "p")}x` : "";
    const cutBit =
      duration && (trim.start > 0.05 || duration - trim.end > 0.05) ? "_coupe" : "";
    els.outName.value = `${stem}${speedBit}${cutBit}_${Math.round(target)}mo.mp4`;
    els.outName.dataset.auto = "yes";
  }
}

function showPreview(file) {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = URL.createObjectURL(file);
  els.preview.src = objectUrl;
  els.preview.classList.add("visible");
  els.empty.classList.add("hidden");
}

function hidePreview() {
  els.preview.pause();
  els.preview.removeAttribute("src");
  els.preview.classList.remove("visible");
  els.empty.classList.remove("hidden");
  els.play.textContent = "▶";
  els.seek.value = 0;
  els.time.textContent = "0:00 / 0:00";
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = "";
  refreshTrimLabel();
}

function syncList() {
  els.list.innerHTML = "";
  if (!files.length) {
    els.list.disabled = true;
    els.list.innerHTML = "<option>Aucune vidéo</option>";
    hidePreview();
    refreshTrimLabel();
    refreshSummary();
    return;
  }
  els.list.disabled = false;
  files.forEach((file, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = file.name;
    if (index === selected) option.selected = true;
    els.list.appendChild(option);
  });
  showPreview(files[selected]);
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name || "video.mp4";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function fetchFile(file) {
  return new Uint8Array(await file.arrayBuffer());
}

async function toBlobURL(url, mimeType, onProgress) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Téléchargement échoué (${response.status})`);
  const total = Number(response.headers.get("Content-Length")) || 0;
  if (!response.body || !onProgress) {
    return URL.createObjectURL(new Blob([await response.arrayBuffer()], { type: mimeType }));
  }
  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (total) onProgress(received / total);
  }
  const data = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    data.set(chunk, offset);
    offset += chunk.length;
  }
  return URL.createObjectURL(new Blob([data], { type: mimeType }));
}

async function blobFromMirrors(fileName, mimeType, onProgress) {
  let lastError;
  for (const base of CORE_MIRRORS) {
    try {
      return await toBlobURL(`${base}/${fileName}`, mimeType, onProgress);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Impossible de télécharger FFmpeg.");
}

function attachFfmpegEvents() {
  ffmpeg.on("log", ({ message }) => {
    if (message && /error|failed/i.test(message)) console.warn(message);
  });
  ffmpeg.on("progress", ({ progress }) => {
    if (busy) setProgress(0.12 + Math.max(0, Math.min(progress, 1)) * 0.86, "Encodage FFmpeg…");
  });
}

function webCodecsReady() {
  return fastEncodeOk;
}

function browserLabel() {
  const ua = navigator.userAgent;
  if (/Edg\//.test(ua)) return "Edge";
  if (/OPR\//.test(ua) || /Opera/.test(ua)) return "Opera";
  if (/Chrome\//.test(ua) && !/Edg\//.test(ua)) return "Chrome";
  if (/Firefox\//.test(ua)) return "Firefox";
  if (/Safari\//.test(ua) && !/Chrome\//.test(ua)) return "Safari";
  return "Ce navigateur";
}

function setCompat(message, kind) {
  if (!els.compat) return;
  els.compat.textContent = message;
  els.compat.classList.remove("ok", "warn", "err");
  if (kind) els.compat.classList.add(kind);
}

function wasmSupported() {
  try {
    return typeof WebAssembly === "object" && typeof WebAssembly.instantiate === "function";
  } catch {
    return false;
  }
}

async function h264WebCodecsSupported() {
  if (typeof VideoEncoder !== "function" || typeof VideoEncoder.isConfigSupported !== "function") {
    return false;
  }
  if (typeof VideoDecoder !== "function") return false;
  const codecs = ["avc1.42001E", "avc1.4D401E", "avc1.640028"];
  for (const codec of codecs) {
    try {
      const result = await VideoEncoder.isConfigSupported({
        codec,
        width: 640,
        height: 360,
        bitrate: 500000,
        framerate: 30,
      });
      if (result?.supported) return true;
    } catch {
      /* codec suivant */
    }
  }
  return false;
}

async function startKeepAlive() {
  stopKeepAlive();
  keepAliveAbort = new AbortController();
  if (navigator.locks?.request) {
    navigator.locks
      .request("compress-accelere-encode", { mode: "exclusive", signal: keepAliveAbort.signal }, () => {
        return new Promise((resolve) => {
          keepAliveAbort.signal.addEventListener("abort", () => resolve(), { once: true });
        });
      })
      .catch(() => {});
  }
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    keepAliveAudio = new AudioCtx();
    const osc = keepAliveAudio.createOscillator();
    const gain = keepAliveAudio.createGain();
    osc.frequency.value = 20;
    gain.gain.value = 0.00003;
    osc.connect(gain);
    gain.connect(keepAliveAudio.destination);
    osc.start();
    keepAliveOsc = osc;
    if (keepAliveAudio.state === "suspended") await keepAliveAudio.resume();
  } catch {
    /* ignore */
  }
}

function stopKeepAlive() {
  try {
    keepAliveAbort?.abort();
  } catch {
    /* ignore */
  }
  keepAliveAbort = null;
  try {
    keepAliveOsc?.stop();
  } catch {
    /* ignore */
  }
  keepAliveOsc = null;
  if (keepAliveAudio) {
    keepAliveAudio.close().catch(() => {});
    keepAliveAudio = null;
  }
}

function stopEncodeWorker() {
  if (!encodeWorker) return;
  try {
    encodeWorker.postMessage({ type: "cancel" });
  } catch {
    /* ignore */
  }
  try {
    encodeWorker.terminate();
  } catch {
    /* ignore */
  }
  encodeWorker = null;
}

async function loadFfmpeg({ background = false } = {}) {
  if (ffmpeg.loaded) return;
  if (ffmpegLoadPromise) return ffmpegLoadPromise;
  ffmpegLoadPromise = (async () => {
    if (!background) {
      els.start.disabled = true;
      setProgress(0.04, "Téléchargement de FFmpeg…");
    }
    attachFfmpegEvents();
    const coreURL = await blobFromMirrors("ffmpeg-core.js", "text/javascript");
    if (!background) setProgress(0.08, "Téléchargement du moteur (environ 25 Mo)…");
    const wasmURL = await blobFromMirrors("ffmpeg-core.wasm", "application/wasm", (ratio) => {
      if (!background && !busy) {
        setProgress(0.08 + ratio * 0.82, `Téléchargement de FFmpeg… ${Math.round(ratio * 100)} %`);
      }
    });
    if (!background) setProgress(0.92, "Initialisation de FFmpeg…");
    await ffmpeg.load({ coreURL, wasmURL });
    if (!background && !busy) {
      setProgress(0, "Prêt. Ajoutez une vidéo.");
      els.start.disabled = false;
    }
  })().catch((error) => {
    ffmpegLoadPromise = null;
    throw error;
  });
  return ffmpegLoadPromise;
}

function suggestedName() {
  return els.outName.value.trim() || "video.mp4";
}

function toVideoBlob(data) {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  return new Blob([bytes], { type: "video/mp4" });
}

function parseDuration(messages) {
  const match = messages.join("\n").match(/Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/);
  if (!match) return null;
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

async function probeDuration(inputName) {
  const lines = [];
  const onLog = ({ message }) => {
    if (message) lines.push(message);
  };
  ffmpeg.on("log", onLog);
  try {
    await ffmpeg.exec(["-hide_banner", "-i", inputName]);
  } catch {
    /* pas de fichier de sortie : normal */
  }
  ffmpeg.off("log", onLog);
  return parseDuration(lines);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function targetBitrates(targetBytes, duration, mute) {
  const totalKbps = Math.max(80, Math.round((targetBytes * 8 * 0.92) / Math.max(0.5, duration) / 1000));
  const audioKbps = mute ? 0 : Math.min(96, Math.max(48, Math.round(totalKbps * 0.08)));
  return { videoKbps: Math.max(80, totalKbps - audioKbps), audioKbps };
}

function estimateCrf(probeCrf, probeBytes, targetBytes) {
  if (probeBytes <= 0) return 23;
  const ratio = clamp(targetBytes / probeBytes, 0.12, 10);
  return Math.round(clamp(probeCrf - 6 * Math.log2(ratio), 12, 32));
}

function buildArgs(inputName, { crf, videoKbps, audioKbps, speed, mute, preset, trimStart, trimDuration }) {
  const args = ["-y"];
  if (trimStart > 0.05) args.push("-ss", String(trimStart));
  if (trimDuration > 0) args.push("-t", String(trimDuration));
  args.push("-i", inputName);
  if (Math.abs(speed - 1) >= 0.01) args.push("-vf", `setpts=PTS/${speed}`);
  args.push(
    "-c:v",
    "libx264",
    "-preset",
    preset || "veryfast",
    "-pix_fmt",
    "yuv420p",
    "-x264-params",
    "aq-mode=3",
  );
  if (crf != null) args.push("-crf", String(crf));
  if (videoKbps) {
    args.push("-maxrate", `${videoKbps}k`, "-bufsize", `${videoKbps * 2}k`);
  }
  args.push("-movflags", "+faststart");
  if (mute) {
    args.push("-an");
  } else {
    const audioFilter = atempoFilter(speed);
    if (audioFilter) args.push("-af", audioFilter);
    args.push("-c:a", "aac", "-b:a", `${audioKbps}k`);
  }
  args.push("output.mp4");
  return args;
}

async function encodeToBlob(args) {
  const code = await ffmpeg.exec(args);
  if (cancelFlag) throw new Error("Annulé");
  if (code !== 0 && code !== true && code !== undefined) {
    throw new Error("L'encodage a échoué.");
  }
  return toVideoBlob(await ffmpeg.readFile("output.mp4"));
}

async function encodePass(inputName, options, label) {
  if (cancelFlag) throw new Error("Annulé");
  try {
    await ffmpeg.deleteFile("output.mp4");
  } catch {
    /* ignore */
  }
  setProgress(options.progress || 0.2, label);
  return encodeToBlob(buildArgs(inputName, options));
}

async function encodeWithFfmpeg(file, { speed, mute, targetBytes, sourceDuration, trimStart, trimEnd }) {
  const inputName = "input" + (file.name.match(/\.[^.]+$/)?.[0] || ".mp4");
  await ffmpeg.writeFile(inputName, await fetchFile(file));
  if (cancelFlag) throw new Error("Annulé");
  setProgress(0.08, "Analyse de la vidéo…");
  const probed = await probeDuration(inputName);
  const duration =
    probed > 0 ? probed : sourceDuration > 0 ? sourceDuration : 1;
  const start = Math.max(0, trimStart || 0);
  const end = trimEnd > start ? Math.min(trimEnd, duration) : duration;
  const trimDuration = Math.max(0.05, end - start);
  const outDuration = trimDuration / speed;
  const { videoKbps, audioKbps } = targetBitrates(targetBytes, outDuration, mute);
  const trim = { trimStart: start, trimDuration };

  let blob = await encodePass(
    inputName,
    { crf: 22, videoKbps, audioKbps, speed, mute, preset: "veryfast", progress: 0.12, ...trim },
    "Analyse qualité…",
  );
  let crf = estimateCrf(22, blob.size, targetBytes);
  const probeOk = blob.size <= targetBytes * 1.06 && blob.size >= targetBytes * 0.82;
  if (!probeOk) {
    blob = await encodePass(
      inputName,
      { crf, videoKbps, audioKbps, speed, mute, preset: "veryfast", progress: 0.42, ...trim },
      "Encodage H.264…",
    );
    if (blob.size > targetBytes * 1.1 && crf < 32) {
      crf = Math.min(32, crf + 2);
      blob = await encodePass(
        inputName,
        { crf, videoKbps, audioKbps, speed, mute, preset: "veryfast", progress: 0.78, ...trim },
        "Ajustement : un peu plus compact…",
      );
    } else if (blob.size < targetBytes * 0.82 && crf > 12) {
      crf = Math.max(12, crf - 2);
      blob = await encodePass(
        inputName,
        { crf, videoKbps, audioKbps, speed, mute, preset: "veryfast", progress: 0.78, ...trim },
        "Ajustement : meilleure qualité…",
      );
    }
  }
  try {
    await ffmpeg.deleteFile(inputName);
    await ffmpeg.deleteFile("output.mp4");
  } catch {
    /* ignore */
  }
  return blob;
}

function encodeWithWebCodecsWorker(file, { mute, videoBps, audioBps, progressBase, trim }) {
  return new Promise((resolve, reject) => {
    const worker = new Worker("./encode-worker.js", { type: "module" });
    encodeWorker = worker;
    worker.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === "progress") {
        if (encodeWorker !== worker) return;
        setProgress(progressBase + Math.max(0, Math.min(data.progress, 1)) * 0.88, "Encodage accéléré…");
      } else if (data.type === "done") {
        encodeWorker = null;
        worker.terminate();
        resolve(new Blob([data.buffer], { type: "video/mp4" }));
      } else if (data.type === "error") {
        encodeWorker = null;
        worker.terminate();
        reject(new Error(data.message || "L'encodage accéléré a échoué."));
      }
    };
    worker.onerror = (event) => {
      encodeWorker = null;
      try {
        worker.terminate();
      } catch {
        /* ignore */
      }
      reject(new Error(event.message || "Le worker d'encodage a échoué."));
    };
    worker.postMessage({ type: "encode", file, mute, videoBps, audioBps, trim });
  });
}

async function encodeWithWebCodecs(file, { mute, targetBytes, duration, trim }) {
  const { videoKbps, audioKbps } = targetBitrates(targetBytes, duration, mute);
  let bitrateScale = 1;

  for (let pass = 1; pass <= 2; pass += 1) {
    if (cancelFlag) throw new Error("Annulé");
    const videoBps = Math.max(80_000, Math.round(videoKbps * 1000 * bitrateScale));
    const audioBps = audioKbps * 1000;
    const progressBase = pass === 1 ? 0.1 : 0.55;
    let blob;
    try {
      blob = await encodeWithWebCodecsWorker(file, {
        mute,
        videoBps,
        audioBps,
        progressBase,
        trim,
      });
    } catch (error) {
      if (cancelFlag) throw new Error("Annulé");
      console.warn("Worker indisponible, encodage dans l'onglet.", error);
      blob = await encodeWithWebCodecsMain(file, { mute, videoBps, audioBps, progressBase, trim });
    }
    if (blob.size <= targetBytes * 1.08 || pass === 2) return blob;
    bitrateScale *= (targetBytes * 0.94) / blob.size;
    setProgress(0.52, "Ajustement de la taille…");
  }
  throw new Error("L'encodage accéléré a échoué.");
}

async function encodeWithWebCodecsMain(file, { mute, videoBps, audioBps, progressBase, trim }) {
  const {
    Input,
    Output,
    Conversion,
    ALL_FORMATS,
    BlobSource,
    BufferTarget,
    Mp4OutputFormat,
  } = await import("https://cdn.jsdelivr.net/npm/mediabunny@1.55.1/+esm");

  const input = new Input({
    source: new BlobSource(file),
    formats: ALL_FORMATS,
  });
  const output = new Output({
    format: new Mp4OutputFormat({ fastStart: "in-memory" }),
    target: new BufferTarget(),
  });
  const options = {
    input,
    output,
    video: {
      codec: "avc",
      bitrate: videoBps,
      hardwareAcceleration: "prefer-hardware",
      forceTranscode: true,
    },
    audio: mute
      ? { discard: true }
      : { codec: "aac", bitrate: audioBps, forceTranscode: true },
  };
  if (trim && (trim.start > 0.05 || trim.end > trim.start)) {
    options.trim = { start: trim.start, end: trim.end };
  }
  const conversion = await Conversion.init(options);
  if (!conversion.isValid) {
    const reason = (conversion.discardedTracks || [])
      .map((track) => track.reason || track.message || "")
      .filter(Boolean)
      .join(" · ");
    throw new Error(reason || "WebCodecs ne peut pas encoder cette vidéo.");
  }
  activeConversion = conversion;
  conversion.onProgress = (progress) => {
    if (busy) setProgress(progressBase + Math.max(0, Math.min(progress, 1)) * 0.88, "Encodage accéléré…");
  };
  try {
    await conversion.execute();
  } finally {
    activeConversion = null;
  }
  const buffer = output.target.buffer;
  if (!buffer || !buffer.byteLength) throw new Error("Fichier de sortie vide.");
  return new Blob([buffer], { type: "video/mp4" });
}

async function startJob() {
  const file = currentFile();
  const target = targetMb();
  if (!file) {
    alert("Ajoutez au moins une vidéo.");
    return;
  }
  if (!target) {
    alert("Indiquez une taille souhaitée en Mo.");
    return;
  }

  const speed = speedValue();
  const mute = els.mute.checked;
  const targetBytes = target * 1024 * 1024;
  const previewDuration = els.preview.duration;
  const fullDuration =
    Number.isFinite(previewDuration) && previewDuration > 0 ? previewDuration : 1;
  const trim = currentTrim();
  const trimEnd = trim.end > trim.start ? trim.end : fullDuration;
  const sourceDuration = Math.max(0.05, trimEnd - trim.start);
  const canUseWebCodecs = webCodecsReady() && Math.abs(speed - 1) < 0.01;

  busy = true;
  cancelFlag = false;
  progressLocked = false;
  resultBlob = null;
  els.start.disabled = true;
  els.cancel.disabled = false;
  setProgress(0.04, "Lecture du fichier… Vous pouvez changer d'onglet.");
  await startKeepAlive();

  try {
    if (canUseWebCodecs) {
      try {
        resultBlob = await encodeWithWebCodecs(file, {
          mute,
          targetBytes,
          duration: sourceDuration / speed,
          trim: { start: trim.start, end: trimEnd },
        });
      } catch (error) {
        if (cancelFlag || /annul/i.test(error?.message || "")) throw error;
        console.warn("WebCodecs indisponible, passage par FFmpeg.", error);
        setProgress(0.08, "Passage par FFmpeg…");
        await loadFfmpeg();
        resultBlob = await encodeWithFfmpeg(file, {
          speed,
          mute,
          targetBytes,
          sourceDuration: fullDuration,
          trimStart: trim.start,
          trimEnd: trimEnd,
        });
      }
    } else {
      await loadFfmpeg();
      resultBlob = await encodeWithFfmpeg(file, {
        speed,
        mute,
        targetBytes,
        sourceDuration: fullDuration,
        trimStart: trim.start,
        trimEnd: trimEnd,
      });
    }

    const name = suggestedName();
    const size = formatSize(resultBlob.size);
    const over = resultBlob.size > targetBytes * 1.12;
    setProgress(1, `Compression réussie · ${name} · ${size}`, over ? "err" : "ok");
    downloadBlob(resultBlob, name);
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 40)));
    alert(
      over
        ? `Compression terminée, mais la taille reste au-dessus de l'objectif.\n\nFichier : ${name}\nTaille : ${size}\nObjectif : ${target} Mo`
        : `Compression réussie.\n\nFichier : ${name}\nTaille : ${size}\nObjectif : ${target} Mo`,
    );
  } catch (error) {
    setProgress(0, error.message || "Échec", "err");
  } finally {
    stopEncodeWorker();
    stopKeepAlive();
    busy = false;
    els.start.disabled = !(ffmpeg.loaded || webCodecsReady());
    els.cancel.disabled = true;
  }
}

els.add.addEventListener("click", () => els.input.click());
els.input.addEventListener("change", () => {
  for (const file of els.input.files || []) {
    files.push(file);
    trims.push({ start: 0, end: 0 });
  }
  selected = files.length ? files.length - 1 : 0;
  els.input.value = "";
  els.outName.dataset.auto = "yes";
  syncList();
});
els.clear.addEventListener("click", () => {
  files.length = 0;
  trims.length = 0;
  selected = 0;
  resultBlob = null;
  syncList();
});
els.remove.addEventListener("click", () => {
  if (!files.length) return;
  files.splice(selected, 1);
  trims.splice(selected, 1);
  selected = Math.max(0, files.length - 1);
  resultBlob = null;
  syncList();
});
els.list.addEventListener("change", () => {
  selected = Number(els.list.value) || 0;
  els.outName.dataset.auto = "yes";
  syncList();
});
els.start.addEventListener("click", () => {
  if (!busy) startJob();
});
els.cancel.addEventListener("click", () => {
  cancelFlag = true;
  stopEncodeWorker();
  if (activeConversion) {
    Promise.resolve(activeConversion.cancel()).catch(() => {});
    activeConversion = null;
  }
  stopKeepAlive();
  try {
    ffmpeg.terminate();
  } catch {
    /* ignore */
  }
  ffmpeg = new FFmpeg();
  ffmpegLoadPromise = null;
  setProgress(0, "Annulation…");
  if (webCodecsReady()) {
    els.start.disabled = false;
    setProgress(0, "Annulé. Prêt.");
    loadFfmpeg({ background: true }).catch((error) => console.warn(error));
  } else {
    els.start.disabled = true;
    loadFfmpeg().catch((error) => {
      els.start.disabled = true;
      setProgress(0, error.message || "Impossible de recharger FFmpeg.");
    });
  }
});
els.download.addEventListener("click", () => {
  if (resultBlob) downloadBlob(resultBlob, suggestedName());
  else alert("Lancez d'abord un export. Le navigateur téléchargera ensuite le fichier.");
});
els.copy.addEventListener("click", async () => {
  if (!resultBlob) {
    alert("Lancez d'abord un export.");
    return;
  }
  downloadBlob(resultBlob, suggestedName());
});
els.target.addEventListener("input", refreshSummary);
els.speed.addEventListener("change", refreshSummary);
els.mute.addEventListener("change", refreshSummary);
els.outName.addEventListener("input", () => {
  els.outName.dataset.auto = "no";
});
function setTrim(start, end) {
  const duration = sourceDuration();
  trims[selected] = {
    start: Math.max(0, start),
    end: duration ? Math.min(end, duration) : end,
  };
  refreshTrimLabel();
  refreshSummary();
}

els.markInBtn?.addEventListener("click", () => {
  if (!sourceDuration()) return;
  const trim = currentTrim();
  const start = els.preview.currentTime;
  setTrim(start, Math.max(start + 0.1, trim.end || sourceDuration()));
});
els.markOutBtn?.addEventListener("click", () => {
  if (!sourceDuration()) return;
  const trim = currentTrim();
  const end = els.preview.currentTime;
  setTrim(Math.min(trim.start, Math.max(0, end - 0.1)), end);
});
els.resetTrim?.addEventListener("click", () => {
  const duration = sourceDuration();
  setTrim(0, duration || 0);
});
els.frame?.addEventListener("click", () => {
  const video = els.preview;
  if (!video.videoWidth) {
    alert("Ajoutez une vidéo et allez à l'image voulue.");
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => {
    if (!blob) return;
    const file = currentFile();
    const stem = file ? file.name.replace(/\.[^.]+$/, "") : "image";
    downloadBlob(blob, `${stem}_${formatTime(video.currentTime).replace(/:/g, "-")}.jpg`);
  }, "image/jpeg", 0.92);
});
els.play.addEventListener("click", () => {
  if (!els.preview.src) return;
  if (els.preview.paused) {
    const trim = currentTrim();
    if (els.preview.currentTime < trim.start || els.preview.currentTime >= trim.end - 0.05) {
      els.preview.currentTime = trim.start;
    }
    els.preview.playbackRate = speedValue();
    els.preview.play();
    els.play.textContent = "❚❚";
  } else {
    els.preview.pause();
    els.play.textContent = "▶";
  }
});
els.preview.addEventListener("loadedmetadata", () => {
  const stored = trims[selected];
  if (stored && !(stored.end > stored.start)) {
    stored.end = sourceDuration();
  }
  refreshSummary();
  refreshTrimLabel();
});
els.preview.addEventListener("timeupdate", () => {
  const duration = els.preview.duration || 1;
  const trim = currentTrim();
  if (!els.preview.paused && els.preview.currentTime >= trim.end - 0.05 && trim.end < duration - 0.02) {
    els.preview.pause();
    els.play.textContent = "▶";
  }
  els.seek.value = String(els.preview.currentTime / duration);
  els.time.textContent = `${formatTime(els.preview.currentTime)} / ${formatTime(duration)}`;
});
els.preview.addEventListener("ended", () => {
  els.play.textContent = "▶";
});
els.seek.addEventListener("input", () => {
  if (!els.preview.duration) return;
  els.preview.currentTime = Number(els.seek.value) * els.preview.duration;
});

if (!window.isSecureContext && !/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) {
  els.start.disabled = true;
  setProgress(0, "Ouvrez la page en HTTPS (ou en local).");
  setCompat("L'encodage est bloqué en file://. Utilisez GitHub Pages ou localhost.", "err");
} else if (!wasmSupported()) {
  els.start.disabled = true;
  setProgress(0, "Navigateur trop ancien.");
  setCompat("WebAssembly est requis. Utilisez Chrome, Edge, Firefox ou Safari récents.", "err");
} else {
  h264WebCodecsSupported()
    .then((ok) => {
      fastEncodeOk = ok;
      const name = browserLabel();
      if (ok) {
        els.start.disabled = false;
        setProgress(0, "Prêt (encodage accéléré). Ajoutez une vidéo.");
        setCompat(`${name} : compatible. Encodage accéléré (WebCodecs), FFmpeg en secours.`, "ok");
        loadFfmpeg({ background: true }).catch((error) => console.warn(error));
      } else {
        setCompat(`${name} : encodage accéléré indisponible. Chargement de FFmpeg Wasm…`, "warn");
        loadFfmpeg()
          .then(() => {
            if (!busy) {
              setCompat(`${name} : compatible via FFmpeg Wasm (plus lent).`, "ok");
            }
          })
          .catch((error) => {
            els.start.disabled = true;
            setProgress(0, "Impossible de charger FFmpeg. Vérifiez Internet et rechargez la page.");
            setCompat(`${name} : échec du chargement de FFmpeg.`, "err");
            console.error(error);
          });
      }
    })
    .catch((error) => {
      console.error(error);
      setCompat(`${browserLabel()} : test WebCodecs impossible. Chargement de FFmpeg…`, "warn");
      loadFfmpeg().catch((loadError) => {
        els.start.disabled = true;
        setProgress(0, "Impossible de charger FFmpeg.");
        setCompat("Navigateur incompatible ou hors ligne.", "err");
        console.error(loadError);
      });
    });
}
