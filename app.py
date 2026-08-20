"""Application Windows pour compresser et accélérer des vidéos MP4."""

from __future__ import annotations

import io
import os
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from engine import (
    FFmpegTools,
    PreviewStream,
    ProcessSettings,
    VideoInfo,
    ensure_ffmpeg,
    extract_preview_frame,
    format_duration,
    format_size,
    output_duration,
    probe_video,
    process_video,
)

VIDEO_TYPES = [
    ("Vidéos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv"),
    ("MP4", "*.mp4"),
    ("Tous les fichiers", "*.*"),
]

SPEED_CHOICES = {
    "1×": 1.0,
    "1,25×": 1.25,
    "1,5×": 1.5,
    "2×": 2.0,
    "3×": 3.0,
    "4×": 4.0,
}

ACCENT = "#2dd4bf"
ACCENT_HOVER = "#14b8a6"
BG = "#0f1419"
CARD = "#1a222c"
CARD_ALT = "#151c24"
TEXT = "#e8eef4"
MUTED = "#93a4b5"
PREVIEW_W = 520
PREVIEW_H = 292


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Compress & Accélère")
        self.geometry("1080x600")
        self.minsize(980, 560)
        self.configure(fg_color=BG)

        self.tools: FFmpegTools | None = None
        self.videos: list[VideoInfo] = []
        self.selected = 0
        self.busy = False
        self.cancel_flag = False
        self.playing = False
        self.preview_time = 0.0
        self._seek_job: str | None = None
        self._poll_job: str | None = None
        self._preview_ctk: ctk.CTkImage | None = None
        self.stream = PreviewStream()

        self.target_text = tk.StringVar(value="9")
        self.real_size_text = tk.StringVar(value="—")
        self.speed_label = tk.StringVar(value="1×")
        self.output_dir = tk.StringVar(value="")
        self.remove_audio = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Ajoutez une vidéo pour commencer.")
        self.file_choice = tk.StringVar(value="")
        self.meta_text = tk.StringVar(value="")
        self.last_outputs: list[Path] = []

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._prepare_ffmpeg)

    def _speed(self) -> float:
        return SPEED_CHOICES.get(self.speed_label.get(), 1.0)

    def _target_mb(self) -> float | None:
        raw = self.target_text.get().strip().replace(",", ".")
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=16, pady=(12, 14))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        top_btns = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10)
        top_btns.grid(row=0, column=0, sticky="w", pady=(0, 8))
        ctk.CTkButton(
            top_btns,
            text="Ajouter",
            command=self.add_videos,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#042f2e",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=32,
            width=96,
            corner_radius=8,
        ).pack(side="left", padx=(8, 4), pady=6)
        ctk.CTkButton(
            top_btns,
            text="Vider",
            command=self.clear_videos,
            fg_color=CARD_ALT,
            hover_color="#243040",
            text_color=TEXT,
            height=32,
            width=70,
            corner_radius=8,
        ).pack(side="left", padx=(0, 8), pady=6)

        self._build_details(body)
        self._build_preview(body)

    def _build_details(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, width=320, height=500)
        card.grid(row=1, column=0, sticky="new", padx=(0, 12))
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="Paramètres",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 10))

        ctk.CTkLabel(card, text="Taille souhaitée", text_color=MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=16
        )
        size_row = ctk.CTkFrame(card, fg_color="transparent")
        size_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 12))
        size_row.grid_columnconfigure(2, weight=1)
        self.size_entry = ctk.CTkEntry(
            size_row,
            textvariable=self.target_text,
            width=72,
            height=34,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.size_entry.grid(row=0, column=0)
        self.size_entry.bind("<KeyRelease>", lambda _e: self._refresh_meta())
        ctk.CTkLabel(size_row, text="Mo", text_color=TEXT, font=ctk.CTkFont(size=14)).grid(
            row=0, column=1, padx=(8, 10)
        )
        ctk.CTkLabel(size_row, text="Taille réelle", text_color=MUTED).grid(
            row=0, column=2, sticky="e", padx=(0, 6)
        )
        ctk.CTkLabel(
            size_row,
            textvariable=self.real_size_text,
            text_color=ACCENT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=3, sticky="e")

        ctk.CTkLabel(card, text="Supprimer l'audio", text_color=MUTED, anchor="w").grid(
            row=3, column=0, sticky="w", padx=16, pady=(8, 0)
        )
        ctk.CTkLabel(card, text="Vitesse", text_color=MUTED, anchor="w").grid(
            row=3, column=1, sticky="w", padx=(8, 16), pady=(8, 0)
        )
        ctk.CTkCheckBox(
            card,
            text="",
            variable=self.remove_audio,
            command=self._refresh_meta,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            checkmark_color="#042f2e",
            width=28,
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(6, 12))
        self.speed_menu = ctk.CTkOptionMenu(
            card,
            values=list(SPEED_CHOICES),
            variable=self.speed_label,
            command=lambda _v: self._on_speed_change(),
            fg_color=CARD_ALT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=CARD,
            height=34,
            width=120,
        )
        self.speed_menu.grid(row=4, column=1, sticky="w", padx=(8, 16), pady=(6, 12))

        self.file_menu = ctk.CTkOptionMenu(
            card,
            values=["Aucune vidéo"],
            variable=self.file_choice,
            command=self._on_file_choice,
            fg_color=CARD_ALT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=CARD,
            height=28,
        )
        self.file_menu.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 2))
        ctk.CTkLabel(
            card,
            textvariable=self.meta_text,
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).grid(row=6, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        action = ctk.CTkFrame(card, fg_color="transparent")
        action.grid(row=7, column=0, columnspan=2, sticky="w", padx=16, pady=(2, 8))
        self.start_btn = ctk.CTkButton(
            action,
            text="Lancer",
            width=82,
            height=32,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#042f2e",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.start_job,
        )
        self.start_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            action,
            text="Annuler",
            width=78,
            height=32,
            fg_color=CARD_ALT,
            hover_color="#243040",
            command=self.cancel_job,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            action,
            text="Retirer cette vidéo",
            command=self._remove_selected,
            fg_color=CARD_ALT,
            hover_color="#243040",
            height=32,
            width=128,
        ).pack(side="left", padx=(6, 0))

        self.summary_label = ctk.CTkLabel(
            card,
            text="",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.summary_label.grid(row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))

        out = ctk.CTkFrame(card, fg_color="transparent")
        out.grid(row=9, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 14))
        out.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(
            out,
            textvariable=self.output_dir,
            placeholder_text="Dossier de sauvegarde",
            height=32,
        )
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            out,
            text="Dossier",
            width=78,
            height=32,
            fg_color=CARD_ALT,
            hover_color="#243040",
            command=self.choose_output_dir,
        ).grid(row=0, column=1)
        ctk.CTkButton(
            out,
            text="Copie",
            width=70,
            height=32,
            fg_color=CARD_ALT,
            hover_color="#243040",
            command=self.copy_output,
        ).grid(row=0, column=2, padx=(6, 0))

    def _build_preview(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        card.grid(row=0, column=1, rowspan=2, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        self.preview_label = ctk.CTkLabel(
            card,
            text="Aperçu vidéo",
            text_color=MUTED,
            fg_color="#0b1016",
            corner_radius=10,
            width=PREVIEW_W,
            height=PREVIEW_H,
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))

        transport = ctk.CTkFrame(card, fg_color="transparent")
        transport.grid(row=1, column=0, sticky="ew", padx=12)
        transport.grid_columnconfigure(1, weight=1)
        self.play_btn = ctk.CTkButton(
            transport,
            text="▶",
            width=44,
            height=36,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#042f2e",
            font=ctk.CTkFont(size=16),
            command=self.toggle_play,
        )
        self.play_btn.grid(row=0, column=0, padx=(0, 8))
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_slider = ctk.CTkSlider(
            transport,
            from_=0,
            to=1,
            variable=self.seek_var,
            command=self._on_seek,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            height=16,
        )
        self.seek_slider.grid(row=0, column=1, sticky="ew")
        self.time_label = ctk.CTkLabel(
            transport, text="0:00 / 0:00", text_color=MUTED, width=90, font=ctk.CTkFont(size=12)
        )
        self.time_label.grid(row=0, column=2, padx=(8, 0))

        self.progress = ctk.CTkProgressBar(card, progress_color=ACCENT, fg_color=CARD_ALT, height=10)
        self.progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(16, 6))
        self.progress.set(0)
        ctk.CTkLabel(
            card, textvariable=self.status_text, text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w"
        ).grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 14))


    def _current_video(self) -> VideoInfo | None:
        if not self.videos:
            return None
        self.selected = max(0, min(self.selected, len(self.videos) - 1))
        return self.videos[self.selected]

    def _show_image(self, jpeg: bytes) -> None:
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        src_w, src_h = image.size
        scale = min(PREVIEW_W / src_w, PREVIEW_H / src_h)
        size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
        self._preview_ctk = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self.preview_label.configure(image=self._preview_ctk, text="")

    def _clear_preview(self) -> None:
        self.pause_preview()
        self._preview_ctk = None
        self.preview_label.configure(image=None, text="Aperçu vidéo")
        self.seek_var.set(0)
        self.seek_slider.configure(to=1)
        self.time_label.configure(text="0:00 / 0:00")

    def _load_still(self, time_sec: float | None = None) -> None:
        info = self._current_video()
        if not info or not self.tools:
            return
        duration = max(info.duration, 0.1)
        if time_sec is None:
            time_sec = min(duration * 0.08, duration)
        self.preview_time = max(0.0, min(time_sec, duration))
        self.seek_slider.configure(to=duration)
        self.seek_var.set(self.preview_time)
        self.time_label.configure(
            text=f"{format_duration(self.preview_time)} / {format_duration(duration)}"
        )
        tools = self.tools
        path = info.path
        stamp = self.preview_time

        def work() -> None:
            jpeg = extract_preview_frame(tools, path, stamp, width=PREVIEW_W)
            if jpeg:
                self.after(0, lambda: self._show_image(jpeg))

        threading.Thread(target=work, daemon=True).start()

    def toggle_play(self) -> None:
        if self.playing:
            self.pause_preview()
            return
        self.play_preview()

    def play_preview(self) -> None:
        info = self._current_video()
        if not info or not self.tools or self.busy:
            return
        duration = max(info.duration, 0.1)
        if self.preview_time >= duration - 0.15:
            self.preview_time = 0.0
        self.playing = True
        self.play_btn.configure(text="❚❚")
        self.stream.start(
            self.tools,
            info.path,
            self.preview_time,
            width=PREVIEW_W,
            speed=self._speed(),
        )
        self._last_tick = time.monotonic()
        self._poll_preview()

    def pause_preview(self) -> None:
        self.playing = False
        self.play_btn.configure(text="▶")
        self.stream.stop()
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None

    def _poll_preview(self) -> None:
        if not self.playing:
            return
        info = self._current_video()
        if not info:
            self.pause_preview()
            return
        frame = self.stream.take_frame()
        if frame:
            self._show_image(frame)
        now = time.monotonic()
        elapsed = now - getattr(self, "_last_tick", now)
        self._last_tick = now
        speed = max(0.25, self._speed())
        self.preview_time = min(info.duration, self.preview_time + elapsed * speed)
        self.seek_var.set(self.preview_time)
        self.time_label.configure(
            text=f"{format_duration(self.preview_time)} / {format_duration(info.duration)}"
        )
        if self.preview_time >= info.duration - 0.05:
            self.pause_preview()
            return
        self._poll_job = self.after(40, self._poll_preview)

    def _on_seek(self, value: str | float) -> None:
        if self.playing:
            return
        self.preview_time = float(value)
        info = self._current_video()
        if info:
            self.time_label.configure(
                text=f"{format_duration(self.preview_time)} / {format_duration(info.duration)}"
            )
        if self._seek_job:
            self.after_cancel(self._seek_job)
        self._seek_job = self.after(120, lambda: self._load_still(self.preview_time))

    def _on_speed_change(self) -> None:
        self._refresh_meta()
        if self.playing:
            self.play_preview()

    def _prepare_ffmpeg(self) -> None:
        self.status_text.set("Préparation de FFmpeg…")
        self.start_btn.configure(state="disabled")

        def work() -> None:
            try:
                tools = ensure_ffmpeg(self._thread_progress)
                self.after(0, lambda: self._ffmpeg_ready(tools))
            except Exception as exc:
                self.after(0, lambda: self._ffmpeg_failed(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _thread_progress(self, pct: float, message: str) -> None:
        self.after(0, lambda: self._set_progress(pct, message))

    def _ffmpeg_ready(self, tools: FFmpegTools) -> None:
        self.tools = tools
        self.start_btn.configure(state="normal")
        self.status_text.set("Prêt. Ajoutez une vidéo.")
        self.progress.set(0)

    def _ffmpeg_failed(self, error: str) -> None:
        self.status_text.set(error)
        messagebox.showerror("FFmpeg", error)

    def add_videos(self) -> None:
        if not self.tools:
            messagebox.showinfo("Patience", "FFmpeg n'est pas encore prêt.")
            return
        paths = filedialog.askopenfilenames(title="Choisir des vidéos", filetypes=VIDEO_TYPES)
        if not paths:
            return
        errors: list[str] = []
        added = 0
        for raw in paths:
            path = Path(raw)
            if any(video.path == path for video in self.videos):
                continue
            try:
                self.videos.append(probe_video(self.tools, path))
                added += 1
            except Exception as exc:
                errors.append(f"{path.name} : {exc}")
        if added:
            self.selected = len(self.videos) - added
            current = self._current_video()
            if current and not self.output_dir.get():
                self.output_dir.set(str(current.path.parent))
            self._sync_file_menu()
            self._refresh_meta()
            self._load_still()
        if errors:
            messagebox.showwarning("Certaines vidéos ont été ignorées", "\n".join(errors))

    def clear_videos(self) -> None:
        self.videos.clear()
        self.selected = 0
        self._sync_file_menu()
        self._clear_preview()
        self._refresh_meta()

    def _sync_file_menu(self) -> None:
        if not self.videos:
            self.file_menu.configure(values=["Aucune vidéo"])
            self.file_choice.set("Aucune vidéo")
            self.real_size_text.set("—")
            self.meta_text.set("")
            return
        names = [video.path.name for video in self.videos]
        self.file_menu.configure(values=names)
        current = self._current_video()
        if current:
            self.file_choice.set(current.path.name)

    def _on_file_choice(self, name: str) -> None:
        for index, info in enumerate(self.videos):
            if info.path.name == name:
                self.selected = index
                break
        self.pause_preview()
        self.preview_time = 0.0
        self._sync_file_menu()
        self._refresh_meta()
        self._load_still()

    def _remove_selected(self) -> None:
        if not self.videos:
            return
        del self.videos[self.selected]
        self.selected = min(self.selected, max(0, len(self.videos) - 1))
        self._sync_file_menu()
        self._refresh_meta()
        if self.videos:
            self._load_still()
        else:
            self._clear_preview()

    def choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="Dossier de sauvegarde")
        if folder:
            self.output_dir.set(folder)

    def copy_output(self) -> None:
        paths = [path for path in self.last_outputs if path.exists()]
        if not paths:
            info = self._current_video()
            if info and info.path.exists():
                paths = [info.path]
        if not paths:
            folder = self.output_dir.get().strip()
            if folder:
                self.clipboard_clear()
                self.clipboard_append(folder)
                self.status_text.set("Chemin du dossier copié.")
                return
            messagebox.showinfo("Copie", "Aucune vidéo à copier. Exportez d'abord, ou ajoutez une vidéo.")
            return
        quoted = ", ".join("'" + str(path).replace("'", "''") + "'" for path in paths)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                f"Set-Clipboard -LiteralPath {quoted}",
            ],
            capture_output=True,
            creationflags=creationflags,
        )
        if proc.returncode != 0:
            self.clipboard_clear()
            self.clipboard_append(str(paths[-1]))
            self.status_text.set("Chemin copié.")
            return
        self.status_text.set("Vidéo copiée. Collez-la dans un dossier (Ctrl+V).")

    def _current_settings(self) -> ProcessSettings | None:
        target = self._target_mb()
        if target is None:
            return None
        folder = self.output_dir.get().strip()
        return ProcessSettings(
            mode="size",
            target_mb=target,
            speed=self._speed(),
            resolution="Conserver",
            remove_audio=bool(self.remove_audio.get()),
            output_dir=Path(folder) if folder else None,
        )

    def _refresh_meta(self) -> None:
        info = self._current_video()
        if not info:
            self.real_size_text.set("—")
            self.summary_label.configure(text="")
            return
        self.real_size_text.set(format_size(info.size_bytes))
        self.meta_text.set(
            f"{info.width}×{info.height}  ·  {format_duration(info.duration)}"
        )
        settings = self._current_settings()
        if not settings:
            self.summary_label.configure(text="Indiquez une taille en Mo.")
            return
        duration = output_duration(info, settings.speed)
        mute = "sans audio" if settings.remove_audio else "avec audio"
        self.summary_label.configure(
            text=f"Export ≈ {settings.target_mb:g} Mo  ·  {settings.speed:g}×  ·  {format_duration(duration)}  ·  {mute}"
        )

    def _set_progress(self, pct: float, message: str) -> None:
        self.progress.set(max(0.0, min(1.0, pct)))
        self.status_text.set(message)

    def cancel_job(self) -> None:
        self.cancel_flag = True
        self.status_text.set("Annulation…")

    def start_job(self) -> None:
        if self.busy:
            return
        if not self.tools:
            messagebox.showerror("FFmpeg", "FFmpeg n'est pas prêt.")
            return
        if not self.videos:
            messagebox.showinfo("Vidéos", "Ajoutez au moins une vidéo.")
            return
        settings = self._current_settings()
        if not settings or settings.target_mb < 1:
            messagebox.showwarning("Taille", "Indiquez une taille souhaitée en Mo.")
            return

        self.pause_preview()
        self.busy = True
        self.cancel_flag = False
        self._last_ui_update = 0.0
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        snapshot = list(self.videos)
        threading.Thread(target=self._run_job, args=(snapshot, settings), daemon=True).start()

    def _run_job(self, videos: list[VideoInfo], settings: ProcessSettings) -> None:
        results = []
        total = len(videos)
        for index, info in enumerate(videos):
            if self.cancel_flag:
                break

            def on_progress(pct: float, message: str, idx=index) -> None:
                overall = (idx + pct) / total
                now = time.monotonic()
                if pct < 0.99 and now - self._last_ui_update < 0.2:
                    return
                self._last_ui_update = now
                self.after(0, lambda o=overall, m=message: self._set_progress(o, m))

            result = process_video(
                self.tools,
                info,
                settings,
                progress=on_progress,
                should_cancel=lambda: self.cancel_flag,
            )
            results.append(result)

        self.after(0, lambda: self._job_done(results))

    def _job_done(self, results: list) -> None:
        self.busy = False
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        ok = [item for item in results if item.ok and item.output]
        failed = [item for item in results if not item.ok]
        if ok:
            last = ok[-1].output
            self.last_outputs = [item.output for item in ok if item.output]
            self._set_progress(1.0, f"{len(ok)} vidéo(s) exportée(s).")
            details = "\n".join(
                f"{item.output.name}  ({format_size(item.output_size)})" for item in ok
            )
            extra = ""
            if failed:
                extra = "\n\nÉchecs :\n" + "\n".join(
                    f"{item.source.name} : {item.error}" for item in failed
                )
            if messagebox.askyesno("Terminé", f"{details}{extra}\n\nOuvrir le dossier de sauvegarde ?"):
                os.startfile(str(last.parent))
        elif failed:
            self._set_progress(0, "Échec ou annulation.")
            messagebox.showerror(
                "Traitement",
                "\n".join(f"{item.source.name} : {item.error}" for item in failed),
            )
        else:
            self._set_progress(0, "Annulé.")

    def _on_close(self) -> None:
        self.pause_preview()
        self.destroy()


def main() -> None:
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
