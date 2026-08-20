import { FFmpeg } from "https://unpkg.com/@ffmpeg/ffmpeg@0.12.10/dist/esm/index.js";
import { fetchFile, toBlobURL } from "https://unpkg.com/@ffmpeg/util@0.12.1/dist/esm/index.js";

const SPEED_LABELS = {
  1: "1×",
  1.25: "1,25×",
  1.5: "1,5×",
  2: "2×",
  3: "3×",
  4: "4×",
};

let ffmpeg = new FFmpeg();
const files = [];
let selected = 0;
let objectUrl = "";
let resultBlob = null;
let busy = false;
let cancelFlag = false;

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

function setProgress(pct, message) {
  els.bar.style.width = `${Math.max(0, Math.min(100, pct * 100))}%`;
  if (message) els.status.textContent = message;
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
  const outDuration = Number.isFinite(duration) ? duration / speed : 0;
  const mute = els.mute.checked ? "sans audio" : "avec audio";
  els.summary.textContent = `Export ≈ ${target} Mo  ·  ${SPEED_LABELS[speed] || speed + "×"}  ·  ${formatTime(outDuration)}  ·  ${mute}`;
  if (!els.outName.value.trim() || els.outName.dataset.auto !== "no") {
    const stem = file.name.replace(/\.[^.]+$/, "");
    const speedBit = speed !== 1 ? `_${String(speed).replace(".", "p")}x` : "";
    els.outName.value = `${stem}${speedBit}_${Math.round(target)}mo.mp4`;
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
}

function syncList() {
  els.list.innerHTML = "";
  if (!files.length) {
    els.list.disabled = true;
    els.list.innerHTML = "<option>Aucune vidéo</option>";
    hidePreview();
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

async function loadFfmpeg() {
  const base = "https://unpkg.com/@ffmpeg/core@0.12.6/dist/esm";
  ffmpeg.on("log", ({ message }) => {
    if (message && /error|failed/i.test(message)) console.warn(message);
  });
  ffmpeg.on("progress", ({ progress }) => {
    if (busy) setProgress(0.15 + Math.max(0, Math.min(progress, 1)) * 0.8, "Encodage…");
  });
  setProgress(0.08, "Téléchargement de FFmpeg (une seule fois)…");
  await ffmpeg.load({
    coreURL: await toBlobURL(`${base}/ffmpeg-core.js`, "text/javascript"),
    wasmURL: await toBlobURL(`${base}/ffmpeg-core.wasm`, "application/wasm"),
  });
  setProgress(0, "Prêt. Ajoutez une vidéo.");
}

function suggestedName() {
  return els.outName.value.trim() || "video.mp4";
}

function buildArgs(inputName, videoKbps, audioKbps, speed, mute) {
  const args = ["-y", "-i", inputName];
  if (Math.abs(speed - 1) >= 0.01) args.push("-vf", `setpts=PTS/${speed}`);
  args.push("-c:v", "mpeg4", "-b:v", `${videoKbps}k`, "-pix_fmt", "yuv420p");
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
  if (!ffmpeg.loaded) {
    alert("FFmpeg n'est pas encore prêt.");
    return;
  }

  const speed = speedValue();
  const duration = els.preview.duration;
  const outDuration = Number.isFinite(duration) && duration > 0 ? duration / speed : 1;
  const mute = els.mute.checked;
  const audioKbps = mute ? 0 : 64;
  const totalKbps = Math.max(80, Math.round((target * 1024 * 8) / outDuration));
  const videoKbps = Math.max(48, totalKbps - audioKbps);
  const inputName = "input" + (file.name.match(/\.[^.]+$/)?.[0] || ".mp4");

  busy = true;
  cancelFlag = false;
  resultBlob = null;
  els.start.disabled = true;
  els.cancel.disabled = false;
  setProgress(0.04, "Lecture du fichier…");

  try {
    await ffmpeg.writeFile(inputName, await fetchFile(file));
    if (cancelFlag) throw new Error("Annulé");
    setProgress(0.12, "Encodage…");
    const code = await ffmpeg.exec(buildArgs(inputName, videoKbps, audioKbps, speed, mute));
    if (cancelFlag) throw new Error("Annulé");
    if (code !== 0 && code !== true && code !== undefined) {
      throw new Error("L'encodage a échoué.");
    }
    const data = await ffmpeg.readFile("output.mp4");
    resultBlob = new Blob([data.buffer], { type: "video/mp4" });
    setProgress(1, `Terminé · ${formatSize(resultBlob.size)}`);
    downloadBlob(resultBlob, suggestedName());
    try {
      await ffmpeg.deleteFile(inputName);
      await ffmpeg.deleteFile("output.mp4");
    } catch {
      /* ignore */
    }
  } catch (error) {
    setProgress(0, error.message || "Échec");
  } finally {
    busy = false;
    els.start.disabled = false;
    els.cancel.disabled = true;
  }
}

els.add.addEventListener("click", () => els.input.click());
els.input.addEventListener("change", () => {
  for (const file of els.input.files || []) files.push(file);
  selected = files.length ? files.length - 1 : 0;
  els.input.value = "";
  els.outName.dataset.auto = "yes";
  syncList();
});
els.clear.addEventListener("click", () => {
  files.length = 0;
  selected = 0;
  resultBlob = null;
  syncList();
});
els.remove.addEventListener("click", () => {
  if (!files.length) return;
  files.splice(selected, 1);
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
  try {
    ffmpeg.terminate();
  } catch {
    /* ignore */
  }
  ffmpeg = new FFmpeg();
  setProgress(0, "Annulation…");
  loadFfmpeg().catch((error) => setProgress(0, error.message));
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
els.play.addEventListener("click", () => {
  if (!els.preview.src) return;
  if (els.preview.paused) {
    els.preview.playbackRate = speedValue();
    els.preview.play();
    els.play.textContent = "❚❚";
  } else {
    els.preview.pause();
    els.play.textContent = "▶";
  }
});
els.preview.addEventListener("loadedmetadata", refreshSummary);
els.preview.addEventListener("timeupdate", () => {
  const duration = els.preview.duration || 1;
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

loadFfmpeg().catch((error) => {
  setProgress(0, "Impossible de charger FFmpeg. Vérifiez Internet.");
  console.error(error);
});
