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

from dataclasses import replace

from engine import (
    ClipRange,
    EditSettings,
    FFmpegTools,
    PreviewStream,
    ProcessSettings,
    VideoInfo,
    copy_video,
    edited_output_duration,
    ensure_ffmpeg,
    extract_frame_to_path,
    extract_preview_frame,
    format_duration,
    format_size,
    probe_video,
    process_joined,
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

ROTATE_CHOICES = {"0°": 0, "90°": 90, "180°": 180, "270°": 270}
CROP_CHOICES = ("Aucun", "16:9", "9:16", "1:1", "4:3")
TEXT_POS_CHOICES = ("Bas", "Haut", "Centre")

ACCENT = "#5b8def"
ACCENT_HOVER = "#4a7ad6"
ACCENT_INK = "#f7f9ff"
BG = "#0b0d12"
CARD = "#151922"
CARD_ALT = "#0f131a"
HOVER = "#252b36"
LINE = "#2a3140"
TEXT = "#eef1f6"
MUTED = "#8b94a5"
MARK = "#f0c14b"
PREVIEW_BG = "#07080c"
PREVIEW_W = 640
PREVIEW_H = 360


def parse_time(text: str) -> float:
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        return 0.0
    try:
        if ":" in raw:
            parts = [float(part) for part in raw.split(":")]
            if len(parts) == 3:
                return max(0.0, parts[0] * 3600 + parts[1] * 60 + parts[2])
            if len(parts) == 2:
                return max(0.0, parts[0] * 60 + parts[1])
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Compress & Accélère")
        self.geometry("1360x800")
        self.minsize(1180, 720)
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
        self.last_sources: list[Path] = []
        self.trim_start = tk.StringVar(value="0:00")
        self.trim_end = tk.StringVar(value="0:00")
        self.ranges_text = tk.StringVar(value="Toute la vidéo")
        self.rotate_label = tk.StringVar(value="0°")
        self.flip_h = tk.BooleanVar(value=False)
        self.flip_v = tk.BooleanVar(value=False)
        self.volume_value = tk.DoubleVar(value=100)
        self.fade_in = tk.StringVar(value="0")
        self.fade_out = tk.StringVar(value="0")
        self.crop_label = tk.StringVar(value="Aucun")
        self.text_overlay = tk.StringVar(value="")
        self.text_pos = tk.StringVar(value="Bas")
        self.brightness = tk.DoubleVar(value=0)
        self.contrast = tk.DoubleVar(value=1)
        self.saturation = tk.DoubleVar(value=1)
        self.reverse_clip = tk.BooleanVar(value=False)
        self.join_all = tk.BooleanVar(value=False)
        self.audio_label = tk.StringVar(value="Audio d'origine")
        self._loading_edit = False
        self._draft_ranges: list[ClipRange] = []

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
        self.grid_rowconfigure(1, weight=1)
        self._build_header()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 16))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_preview(body)

    def _accent_btn(self, parent, text, command, width=96, height=34) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_INK,
            font=ctk.CTkFont(size=13, weight="bold"),
            height=height,
            width=width,
            corner_radius=8,
        )

    def _ghost_btn(self, parent, text, command, width=88, height=34) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            fg_color=CARD_ALT,
            hover_color=HOVER,
            text_color=TEXT,
            border_width=1,
            border_color=LINE,
            height=height,
            width=width,
            corner_radius=8,
        )

    def _field(self, parent, variable: tk.StringVar, width: int = 80) -> ctk.CTkEntry:
        return ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=width,
            height=32,
            fg_color=CARD_ALT,
            border_color=LINE,
            text_color=TEXT,
        )

    def _menu(self, parent, values, variable, command, width=140) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(
            parent,
            values=list(values),
            variable=variable,
            command=command,
            fg_color=CARD_ALT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=CARD,
            dropdown_hover_color=HOVER,
            text_color=TEXT,
            height=32,
            width=width,
        )

    def _check(self, parent, text, variable, command) -> ctk.CTkCheckBox:
        return ctk.CTkCheckBox(
            parent,
            text=text,
            variable=variable,
            command=command,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            checkmark_color=ACCENT_INK,
            text_color=TEXT,
            border_color=LINE,
        )

    def _slider(self, parent, variable, start, end) -> ctk.CTkSlider:
        return ctk.CTkSlider(
            parent,
            from_=start,
            to=end,
            variable=variable,
            command=self._on_edit_change,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            fg_color=LINE,
        )

    def _caption(self, parent, text) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, text_color=MUTED, anchor="w", font=ctk.CTkFont(size=12))

    def _heading(self, parent, text) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent, text=text, text_color=TEXT, anchor="w", font=ctk.CTkFont(size=13, weight="bold")
        )

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=18, pady=10)
        ctk.CTkLabel(
            brand,
            text="Compress & Accélère",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="Compression  ·  accélération  ·  montage",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(anchor="w")

        files = ctk.CTkFrame(header, fg_color="transparent")
        files.grid(row=0, column=1, sticky="w", padx=8)
        self._accent_btn(files, "Ajouter", self.add_videos, 100).pack(side="left", padx=(0, 8))
        self._ghost_btn(files, "Vider", self.clear_videos, 80).pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e", padx=18)
        self.start_btn = self._accent_btn(actions, "Lancer", self.start_job, 110)
        self.start_btn.pack(side="left")
        self.cancel_btn = self._ghost_btn(actions, "Annuler", self.cancel_job, 96)
        self.cancel_btn.configure(state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))

    def _small_entry(self, parent: ctk.CTkFrame, variable: tk.StringVar, width: int = 72) -> ctk.CTkEntry:
        return self._field(parent, variable, width)

    def _on_edit_change(self, _value: object = None) -> None:
        if self._loading_edit:
            return
        self._store_edit()
        self._refresh_meta()

    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        side = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, width=380)
        side.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(0, weight=1)

        tabs = ctk.CTkTabview(
            side,
            fg_color=CARD,
            segmented_button_fg_color=CARD_ALT,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD_ALT,
            segmented_button_unselected_hover_color=HOVER,
            text_color=ACCENT_INK,
            text_color_disabled=MUTED,
            corner_radius=10,
        )
        tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(8, 10))
        tabs.add("Export")
        tabs.add("Édition")
        self._fill_export_tab(tabs.tab("Export"))
        self._fill_edit_tab(tabs.tab("Édition"))

    def _fill_export_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        pad = {"padx": 8, "pady": (0, 6)}

        self._heading(tab, "Fichier").grid(row=0, column=0, sticky="w", padx=8, pady=(6, 4))
        self.file_menu = self._menu(tab, ["Aucune vidéo"], self.file_choice, self._on_file_choice, 320)
        self.file_menu.grid(row=1, column=0, sticky="ew", **pad)
        ctk.CTkLabel(
            tab, textvariable=self.meta_text, text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w"
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 10))

        self._heading(tab, "Compression").grid(row=3, column=0, sticky="w", padx=8, pady=(4, 4))
        size_row = ctk.CTkFrame(tab, fg_color="transparent")
        size_row.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 10))
        size_row.grid_columnconfigure(2, weight=1)
        self._caption(size_row, "Taille cible").grid(row=0, column=0, sticky="w")
        self.size_entry = ctk.CTkEntry(
            size_row,
            textvariable=self.target_text,
            width=70,
            height=34,
            fg_color=CARD_ALT,
            border_color=LINE,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.size_entry.grid(row=1, column=0, pady=(4, 0))
        self.size_entry.bind("<KeyRelease>", lambda _e: self._refresh_meta())
        ctk.CTkLabel(size_row, text="Mo", text_color=TEXT).grid(row=1, column=1, padx=(8, 16), pady=(4, 0))
        self._caption(size_row, "Taille réelle").grid(row=0, column=2, sticky="e")
        ctk.CTkLabel(
            size_row,
            textvariable=self.real_size_text,
            text_color=ACCENT,
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="e",
        ).grid(row=1, column=2, sticky="e", pady=(4, 0))

        opts = ctk.CTkFrame(tab, fg_color=CARD_ALT, corner_radius=10)
        opts.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 12))
        opts.grid_columnconfigure((0, 1), weight=1)
        self._caption(opts, "Vitesse").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        self.speed_menu = self._menu(opts, SPEED_CHOICES, self.speed_label, lambda _v: self._on_speed_change(), 130)
        self.speed_menu.grid(row=1, column=0, sticky="w", padx=12, pady=(4, 12))
        self._check(opts, "Supprimer l'audio", self.remove_audio, self._refresh_meta).grid(
            row=1, column=1, sticky="w", padx=12, pady=(4, 12)
        )
        self._check(opts, "Joindre toutes les vidéos", self.join_all, self._on_edit_change).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12)
        )

        self.summary_label = ctk.CTkLabel(
            tab,
            text="",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
            justify="left",
            wraplength=320,
        )
        self.summary_label.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 10))

        self._heading(tab, "Sauvegarde").grid(row=7, column=0, sticky="w", padx=8, pady=(4, 4))
        out = ctk.CTkFrame(tab, fg_color="transparent")
        out.grid(row=8, column=0, sticky="ew", padx=8, pady=(0, 8))
        out.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(
            out,
            textvariable=self.output_dir,
            placeholder_text="Dossier de sauvegarde",
            height=32,
            fg_color=CARD_ALT,
            border_color=LINE,
        )
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._ghost_btn(out, "Dossier", self.choose_output_dir, 78, 32).grid(row=0, column=1)
        self._ghost_btn(out, "Copie", self.copy_output, 70, 32).grid(row=0, column=2, padx=(6, 0))

        extra_btns = ctk.CTkFrame(tab, fg_color="transparent")
        extra_btns.grid(row=9, column=0, sticky="w", padx=8, pady=(8, 12))
        self._ghost_btn(extra_btns, "Retirer cette vidéo", self._remove_selected, 158, 32).pack(
            side="left"
        )
        self.delete_btn = self._ghost_btn(
            extra_btns, "Supprimer l'original", self.delete_originals, 168, 32
        )
        self.delete_btn.configure(state="disabled")
        self.delete_btn.pack(side="left", padx=(8, 0))

    def _fill_edit_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        card = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure((0, 1), weight=1)

        self._heading(card, "Coupe").grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))
        self._caption(card, "Début").grid(row=1, column=0, sticky="w", padx=4)
        self._caption(card, "Fin").grid(row=1, column=1, sticky="w", padx=4)
        start_entry = self._field(card, self.trim_start, 120)
        start_entry.grid(row=2, column=0, sticky="w", padx=4, pady=(2, 6))
        start_entry.bind("<FocusOut>", lambda _e: self._on_edit_change())
        end_entry = self._field(card, self.trim_end, 120)
        end_entry.grid(row=2, column=1, sticky="w", padx=4, pady=(2, 6))
        end_entry.bind("<FocusOut>", lambda _e: self._on_edit_change())
        ctk.CTkLabel(
            card,
            textvariable=self.ranges_text,
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=300,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))

        self._heading(card, "Image").grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))
        self._caption(card, "Rotation").grid(row=5, column=0, sticky="w", padx=4)
        self._caption(card, "Recadrage").grid(row=5, column=1, sticky="w", padx=4)
        self._menu(card, ROTATE_CHOICES, self.rotate_label, self._on_edit_change, 140).grid(
            row=6, column=0, sticky="w", padx=4, pady=(2, 8)
        )
        self._menu(card, CROP_CHOICES, self.crop_label, self._on_edit_change, 140).grid(
            row=6, column=1, sticky="w", padx=4, pady=(2, 8)
        )
        flips = ctk.CTkFrame(card, fg_color="transparent")
        flips.grid(row=7, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 12))
        self._check(flips, "Miroir H", self.flip_h, self._on_edit_change).pack(side="left", padx=(0, 10))
        self._check(flips, "Miroir V", self.flip_v, self._on_edit_change).pack(side="left", padx=(0, 10))
        self._check(flips, "Inverser", self.reverse_clip, self._on_edit_change).pack(side="left")

        self._heading(card, "Audio").grid(row=8, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))
        self._caption(card, "Volume").grid(row=9, column=0, columnspan=2, sticky="w", padx=4)
        self._slider(card, self.volume_value, 0, 200).grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 8)
        )
        self._caption(card, "Fondu entrée (s)").grid(row=11, column=0, sticky="w", padx=4)
        self._caption(card, "Fondu sortie (s)").grid(row=11, column=1, sticky="w", padx=4)
        fade_in = self._field(card, self.fade_in, 90)
        fade_in.grid(row=12, column=0, sticky="w", padx=4, pady=(2, 8))
        fade_in.bind("<FocusOut>", lambda _e: self._on_edit_change())
        fade_out = self._field(card, self.fade_out, 90)
        fade_out.grid(row=12, column=1, sticky="w", padx=4, pady=(2, 8))
        fade_out.bind("<FocusOut>", lambda _e: self._on_edit_change())
        ctk.CTkLabel(card, textvariable=self.audio_label, text_color=MUTED, anchor="w").grid(
            row=13, column=0, columnspan=2, sticky="ew", padx=4
        )
        audio_btns = ctk.CTkFrame(card, fg_color="transparent")
        audio_btns.grid(row=14, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 12))
        self._ghost_btn(audio_btns, "Remplacer l'audio", self.choose_audio, 140, 30).pack(side="left")
        self._ghost_btn(audio_btns, "Audio d'origine", self.clear_audio, 120, 30).pack(side="left", padx=(8, 0))

        self._heading(card, "Texte").grid(row=15, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))
        text_row = ctk.CTkFrame(card, fg_color="transparent")
        text_row.grid(row=16, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))
        text_row.grid_columnconfigure(0, weight=1)
        text_entry = ctk.CTkEntry(
            text_row, textvariable=self.text_overlay, height=32, fg_color=CARD_ALT, border_color=LINE
        )
        text_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        text_entry.bind("<FocusOut>", lambda _e: self._on_edit_change())
        self._menu(text_row, TEXT_POS_CHOICES, self.text_pos, self._on_edit_change, 100).grid(row=0, column=1)

        self._heading(card, "Couleurs").grid(row=17, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))
        for row, label, variable, start, end in (
            (18, "Lumière", self.brightness, -0.4, 0.4),
            (20, "Contraste", self.contrast, 0.5, 1.6),
            (22, "Saturation", self.saturation, 0.0, 2.0),
        ):
            self._caption(card, label).grid(row=row, column=0, columnspan=2, sticky="w", padx=4)
            self._slider(card, variable, start, end).grid(
                row=row + 1, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 8)
            )

    def _build_preview(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        card.grid(row=0, column=1, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        self.preview_label = ctk.CTkLabel(
            card,
            text="Aperçu vidéo",
            text_color=MUTED,
            fg_color=PREVIEW_BG,
            corner_radius=12,
            width=PREVIEW_W,
            height=PREVIEW_H,
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 10))

        transport = ctk.CTkFrame(card, fg_color="transparent")
        transport.grid(row=1, column=0, sticky="ew", padx=14)
        transport.grid_columnconfigure(1, weight=1)
        self.play_btn = self._accent_btn(transport, "▶", self.toggle_play, 44, 36)
        self.play_btn.configure(font=ctk.CTkFont(size=16))
        self.play_btn.grid(row=0, column=0, padx=(0, 10), rowspan=2)

        seek_box = ctk.CTkFrame(transport, fg_color="transparent")
        seek_box.grid(row=0, column=1, rowspan=2, sticky="ew")
        seek_box.grid_columnconfigure(0, weight=1)
        self.mark_bar = ctk.CTkFrame(seek_box, fg_color=LINE, height=8, corner_radius=4)
        self.mark_bar.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        self.mark_span = ctk.CTkFrame(self.mark_bar, fg_color=ACCENT, height=8, corner_radius=4)
        self.mark_in = ctk.CTkFrame(self.mark_bar, fg_color=MARK, width=3, height=8)
        self.mark_out = ctk.CTkFrame(self.mark_bar, fg_color=MARK, width=3, height=8)
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_slider = ctk.CTkSlider(
            seek_box,
            from_=0,
            to=1,
            variable=self.seek_var,
            command=self._on_seek,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            fg_color=LINE,
            height=16,
        )
        self.seek_slider.grid(row=1, column=0, sticky="ew")
        self.time_label = ctk.CTkLabel(
            transport, text="0:00 / 0:00", text_color=MUTED, width=88, font=ctk.CTkFont(size=12)
        )
        self.time_label.grid(row=0, column=2, rowspan=2, padx=(10, 0))

        edit_bar = ctk.CTkFrame(card, fg_color="transparent")
        edit_bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))
        for text, cmd, width in (
            ("Début ici", self.mark_start, 92),
            ("Fin ici", self.mark_end, 80),
            ("+ Extrait", self.add_range, 92),
            ("Retirer", self.remove_last_range, 80),
            ("Image", self.capture_frame, 72),
        ):
            self._ghost_btn(edit_bar, text, cmd, width, 32).pack(side="left", padx=(0, 8))

        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=14, pady=(14, 14))
        foot.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(foot, progress_color=ACCENT, fg_color=LINE, height=8)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        ctk.CTkLabel(
            foot, textvariable=self.status_text, text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w"
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _current_video(self) -> VideoInfo | None:
        if not self.videos:
            return None
        self.selected = max(0, min(self.selected, len(self.videos) - 1))
        return self.videos[self.selected]

    def _working_range(self, info: VideoInfo) -> ClipRange:
        start = min(parse_time(self.trim_start.get()), max(0.0, info.duration))
        end = parse_time(self.trim_end.get())
        if end <= start:
            end = info.duration
        end = min(end, max(0.0, info.duration))
        return ClipRange(start, end)

    def _effective_ranges(self, info: VideoInfo) -> list[ClipRange]:
        if self._draft_ranges:
            return list(self._draft_ranges)
        return [self._working_range(info)]

    def _sync_ranges_label(self) -> None:
        info = self._current_video()
        if not info:
            self.ranges_text.set("Toute la vidéo")
            self._update_trim_marks()
            return
        ranges = self._effective_ranges(info)
        if (
            len(ranges) == 1
            and ranges[0].start <= 0.04
            and ranges[0].end >= info.duration - 0.04
        ):
            self.ranges_text.set("Toute la vidéo")
        else:
            bits = [f"{format_duration(item.start)} → {format_duration(item.end)}" for item in ranges]
            self.ranges_text.set("Extraits : " + "  ·  ".join(bits))
        self._update_trim_marks()

    def _update_trim_marks(self) -> None:
        if not hasattr(self, "mark_bar"):
            return
        info = self._current_video()
        self.mark_span.place_forget()
        self.mark_in.place_forget()
        self.mark_out.place_forget()
        if not info or info.duration <= 0:
            return
        ranges = self._effective_ranges(info)
        if (
            len(ranges) == 1
            and ranges[0].start <= 0.04
            and ranges[0].end >= info.duration - 0.04
        ):
            return
        first = ranges[0]
        last = ranges[-1]
        start = max(0.0, first.start / info.duration)
        end = min(1.0, last.end / info.duration)
        width = max(0.01, end - start)
        self.mark_span.place(relx=start, rely=0, relwidth=width, relheight=1)
        self.mark_in.place(relx=start, rely=0, relheight=1)
        self.mark_out.place(relx=end, rely=0, relheight=1, anchor="ne")

    def _store_edit(self) -> None:
        info = self._current_video()
        if not info or self._loading_edit:
            return
        ranges = self._effective_ranges(info)
        if (
            len(ranges) == 1
            and ranges[0].start <= 0.04
            and ranges[0].end >= info.duration - 0.04
        ):
            ranges = []
        info.edit = EditSettings(
            ranges=ranges,
            rotate=ROTATE_CHOICES.get(self.rotate_label.get(), 0),
            flip_h=bool(self.flip_h.get()),
            flip_v=bool(self.flip_v.get()),
            volume=max(0.0, float(self.volume_value.get()) / 100.0),
            fade_in=max(0.0, parse_time(self.fade_in.get())),
            fade_out=max(0.0, parse_time(self.fade_out.get())),
            crop=self.crop_label.get() if self.crop_label.get() in CROP_CHOICES else "Aucun",
            text=self.text_overlay.get(),
            text_pos=self.text_pos.get() if self.text_pos.get() in TEXT_POS_CHOICES else "Bas",
            brightness=float(self.brightness.get()),
            contrast=float(self.contrast.get()),
            saturation=float(self.saturation.get()),
            reverse=bool(self.reverse_clip.get()),
            audio_path=info.edit.audio_path,
        )
        self._sync_ranges_label()

    def _load_edit(self) -> None:
        info = self._current_video()
        self._loading_edit = True
        try:
            if not info:
                self._draft_ranges = []
                self.trim_start.set("0:00")
                self.trim_end.set("0:00")
                self.ranges_text.set("Toute la vidéo")
                self.audio_label.set("Audio d'origine")
                return
            edit = info.edit
            self._draft_ranges = list(edit.ranges)
            if edit.ranges:
                self.trim_start.set(format_duration(edit.ranges[0].start))
                self.trim_end.set(format_duration(edit.ranges[0].end))
            else:
                self.trim_start.set("0:00")
                self.trim_end.set(format_duration(info.duration))
            self.rotate_label.set(next((k for k, v in ROTATE_CHOICES.items() if v == edit.rotate), "0°"))
            self.flip_h.set(edit.flip_h)
            self.flip_v.set(edit.flip_v)
            self.volume_value.set(edit.volume * 100)
            self.fade_in.set(str(edit.fade_in).rstrip("0").rstrip(".") if edit.fade_in else "0")
            self.fade_out.set(str(edit.fade_out).rstrip("0").rstrip(".") if edit.fade_out else "0")
            self.crop_label.set(edit.crop if edit.crop in CROP_CHOICES else "Aucun")
            self.text_overlay.set(edit.text)
            self.text_pos.set(edit.text_pos if edit.text_pos in TEXT_POS_CHOICES else "Bas")
            self.brightness.set(edit.brightness)
            self.contrast.set(edit.contrast)
            self.saturation.set(edit.saturation)
            self.reverse_clip.set(edit.reverse)
            if edit.audio_path:
                self.audio_label.set(f"Audio : {edit.audio_path.name}")
            else:
                self.audio_label.set("Audio d'origine")
            self._sync_ranges_label()
        finally:
            self._loading_edit = False

    def mark_start(self) -> None:
        if not self._current_video():
            return
        self.trim_start.set(format_duration(self.preview_time))
        self._on_edit_change()

    def mark_end(self) -> None:
        if not self._current_video():
            return
        self.trim_end.set(format_duration(self.preview_time))
        self._on_edit_change()

    def add_range(self) -> None:
        info = self._current_video()
        if not info:
            return
        item = self._working_range(info)
        if item.end - item.start < 0.05:
            messagebox.showinfo("Extrait", "La plage est trop courte.")
            return
        self._draft_ranges.append(item)
        self._on_edit_change()

    def remove_last_range(self) -> None:
        if self._draft_ranges:
            self._draft_ranges.pop()
        else:
            info = self._current_video()
            if info:
                self.trim_start.set("0:00")
                self.trim_end.set(format_duration(info.duration))
        self._on_edit_change()

    def choose_audio(self) -> None:
        info = self._current_video()
        if not info:
            messagebox.showinfo("Audio", "Ajoutez d'abord une vidéo.")
            return
        path = filedialog.askopenfilename(
            title="Remplacer l'audio",
            filetypes=[
                ("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return
        info.edit.audio_path = Path(path)
        self.audio_label.set(f"Audio : {Path(path).name}")
        self._on_edit_change()

    def clear_audio(self) -> None:
        info = self._current_video()
        if info:
            info.edit.audio_path = None
        self.audio_label.set("Audio d'origine")
        self._on_edit_change()

    def capture_frame(self) -> None:
        info = self._current_video()
        if not info or not self.tools:
            messagebox.showinfo("Image", "Ajoutez une vidéo et attendez FFmpeg.")
            return
        dest = filedialog.asksaveasfilename(
            title="Enregistrer l'image",
            defaultextension=".jpg",
            initialfile=f"{info.path.stem}_{format_duration(self.preview_time).replace(':', '-')}.jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")],
        )
        if not dest:
            return
        if extract_frame_to_path(self.tools, info.path, self.preview_time, Path(dest)):
            self.status_text.set(f"Image enregistrée : {Path(dest).name}")
        else:
            messagebox.showerror("Image", "Impossible d'extraire cette image.")

    def _show_image(self, jpeg: bytes) -> None:
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        src_w, src_h = image.size
        scale = min(PREVIEW_W / src_w, PREVIEW_H / src_h)
        size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
        self._preview_ctk = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self.preview_label.configure(image=self._preview_ctk, text="")

    def _clear_preview(self) -> None:
        self.pause_preview()
        blank = Image.new("RGB", (2, 2), PREVIEW_BG)
        self._preview_ctk = ctk.CTkImage(light_image=blank, dark_image=blank, size=(2, 2))
        self.preview_label.configure(image=self._preview_ctk, text="Aperçu vidéo", fg_color=PREVIEW_BG)
        self.seek_var.set(0)
        self.seek_slider.configure(to=1)
        self.time_label.configure(text="0:00 / 0:00")

    def _load_still(self, time_sec: float | None = None) -> None:
        info = self._current_video()
        if not info or not self.tools:
            return
        duration = max(info.duration, 0.1)
        if time_sec is None:
            time_sec = 0.0
        self.preview_time = max(0.0, min(time_sec, duration))
        self.seek_slider.configure(from_=0, to=duration)
        self.seek_var.set(0.0 if self.preview_time < 0.02 else self.preview_time)
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
        start, end = self._play_bounds(info)
        if self.preview_time >= end - 0.15 or self.preview_time < start:
            self.preview_time = start
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
        _, end = self._play_bounds(info)
        if self.preview_time >= min(info.duration, end) - 0.05:
            self.pause_preview()
            return
        self._poll_job = self.after(40, self._poll_preview)

    def _play_bounds(self, info: VideoInfo) -> tuple[float, float]:
        ranges = self._effective_ranges(info)
        if len(ranges) == 1:
            return ranges[0].start, max(ranges[0].start + 0.1, ranges[0].end)
        return 0.0, max(info.duration, 0.1)

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
            self._load_edit()
            self._refresh_meta()
            self._load_still()
        if errors:
            messagebox.showwarning("Certaines vidéos ont été ignorées", "\n".join(errors))

    def clear_videos(self) -> None:
        self.videos.clear()
        self.selected = 0
        self._draft_ranges = []
        self.last_sources = []
        self.delete_btn.configure(state="disabled")
        self._sync_file_menu()
        self._load_edit()
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
        self._store_edit()
        for index, info in enumerate(self.videos):
            if info.path.name == name:
                self.selected = index
                break
        self.pause_preview()
        self.preview_time = 0.0
        self._sync_file_menu()
        self._load_edit()
        self._refresh_meta()
        self._load_still()

    def _remove_selected(self) -> None:
        if not self.videos:
            return
        del self.videos[self.selected]
        self.selected = min(self.selected, max(0, len(self.videos) - 1))
        self._sync_file_menu()
        self._load_edit()
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
        self._store_edit()
        folder = self.output_dir.get().strip()
        info = self._current_video()
        return ProcessSettings(
            mode="size",
            target_mb=target,
            speed=self._speed(),
            resolution="Conserver",
            remove_audio=bool(self.remove_audio.get()),
            output_dir=Path(folder) if folder else None,
            edit=replace(info.edit, ranges=list(info.edit.ranges)) if info else EditSettings(),
            join_all=bool(self.join_all.get()),
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
        duration = edited_output_duration(info, settings)
        if settings.join_all and len(self.videos) > 1:
            duration = sum(
                edited_output_duration(video, replace(settings, edit=video.edit))
                for video in self.videos
            )
        mute = "sans audio" if settings.remove_audio else "avec audio"
        join = "  ·  fusion" if settings.join_all and len(self.videos) > 1 else ""
        self.summary_label.configure(
            text=(
                f"Export ≈ {settings.target_mb:g} Mo  ·  {settings.speed:g}×  ·  "
                f"{format_duration(duration)}  ·  {mute}{join}"
            )
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
        self.last_sources = []
        self.delete_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        snapshot = [copy_video(video) for video in self.videos]
        threading.Thread(target=self._run_job, args=(snapshot, settings), daemon=True).start()

    def _run_job(self, videos: list[VideoInfo], settings: ProcessSettings) -> None:
        results = []
        items = [(info, replace(settings, edit=info.edit)) for info in videos]
        if settings.join_all and len(items) > 1:
            def on_progress(pct: float, message: str) -> None:
                now = time.monotonic()
                if pct < 0.99 and now - self._last_ui_update < 0.2:
                    return
                self._last_ui_update = now
                self.after(0, lambda o=pct, m=message: self._set_progress(o, m))

            results.append(
                process_joined(
                    self.tools,
                    items,
                    settings,
                    progress=on_progress,
                    should_cancel=lambda: self.cancel_flag,
                )
            )
            self.after(0, lambda: self._job_done(results, settings, [info.path for info, _ in items]))
            return

        total = len(items)
        for index, (info, item_settings) in enumerate(items):
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
                item_settings,
                progress=on_progress,
                should_cancel=lambda: self.cancel_flag,
            )
            results.append(result)

        self.after(0, lambda: self._job_done(results, settings, [info.path for info, _ in items]))

    def _erase_sources(self, paths: list[Path], outputs: list[Path]) -> tuple[list[Path], list[str]]:
        protected = {path.resolve() for path in outputs if path}
        deleted: list[Path] = []
        errors: list[str] = []
        for path in paths:
            try:
                resolved = path.resolve()
                if resolved in protected:
                    continue
                if path.exists():
                    path.unlink()
                    deleted.append(path)
            except OSError as exc:
                errors.append(f"{path.name} : {exc}")
        gone = {path.resolve() for path in deleted}
        if gone:
            self.videos = [video for video in self.videos if video.path.resolve() not in gone]
            self.selected = min(self.selected, max(0, len(self.videos) - 1))
            self._sync_file_menu()
            self._load_edit()
            self._refresh_meta()
            if self.videos:
                self._load_still()
            else:
                self._clear_preview()
        return deleted, errors

    def _job_done(self, results: list, settings: ProcessSettings, sources: list[Path]) -> None:
        self.busy = False
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        ok = [item for item in results if item.ok and item.output]
        failed = [item for item in results if not item.ok]
        if ok:
            last = ok[-1].output
            self.last_outputs = [item.output for item in ok if item.output]
            if settings.join_all and len(sources) > 1:
                self.last_sources = list(sources)
            else:
                self.last_sources = [item.source for item in ok]
            self.delete_btn.configure(state="normal")
            self._set_progress(
                1.0, f"{len(ok)} vidéo(s) exportée(s). Vous pouvez supprimer l'original."
            )
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
            self.last_sources = []
            self.delete_btn.configure(state="disabled")
            self._set_progress(0, "Échec ou annulation.")
            messagebox.showerror(
                "Traitement",
                "\n".join(f"{item.source.name} : {item.error}" for item in failed),
            )
        else:
            self.last_sources = []
            self.delete_btn.configure(state="disabled")
            self._set_progress(0, "Annulé.")

    def delete_originals(self) -> None:
        pending = [path for path in self.last_sources if path.exists()]
        if not pending:
            self.delete_btn.configure(state="disabled")
            messagebox.showinfo("Original", "Aucun fichier d'origine à supprimer.")
            return
        names = "\n".join(f"• {path.name}" for path in pending)
        if not messagebox.askyesno(
            "Supprimer l'original",
            "Cette action est définitive (pas de corbeille) :\n\n"
            f"{names}\n\nSupprimer maintenant ?",
        ):
            return
        self.pause_preview()
        deleted, errors = self._erase_sources(pending, self.last_outputs)
        self.last_sources = []
        self.delete_btn.configure(state="disabled")
        if deleted:
            self._set_progress(1.0, f"{len(deleted)} original(aux) supprimé(s).")
        if errors:
            messagebox.showerror("Suppression", "\n".join(errors))

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
