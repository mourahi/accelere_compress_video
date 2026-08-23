import {
  Input,
  Output,
  Conversion,
  ALL_FORMATS,
  BlobSource,
  BufferTarget,
  Mp4OutputFormat,
} from "https://cdn.jsdelivr.net/npm/mediabunny@1.55.1/+esm";

let conversion = null;

self.onmessage = async (event) => {
  const data = event.data || {};
  if (data.type === "cancel") {
    try {
      await conversion?.cancel();
    } catch {
      /* ignore */
    }
    conversion = null;
    return;
  }
  if (data.type !== "encode") return;

  const { file, mute, videoBps, audioBps, trim } = data;
  try {
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
    conversion = await Conversion.init(options);
    if (!conversion.isValid) {
      const reason = (conversion.discardedTracks || [])
        .map((track) => track.reason || track.message || "")
        .filter(Boolean)
        .join(" · ");
      throw new Error(reason || "WebCodecs ne peut pas encoder cette vidéo.");
    }
    conversion.onProgress = (progress) => {
      self.postMessage({ type: "progress", progress });
    };
    await conversion.execute();
    const buffer = output.target.buffer;
    if (!buffer || !buffer.byteLength) throw new Error("Fichier de sortie vide.");
    self.postMessage({ type: "done", buffer }, [buffer]);
  } catch (error) {
    self.postMessage({
      type: "error",
      message: error?.message || String(error),
    });
  } finally {
    conversion = null;
  }
};
