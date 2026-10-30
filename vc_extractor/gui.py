from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import hashlib
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk, ImageStat

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

from .config import APP_NAME, config_dir, default_config_dir, load_settings, save_settings, set_config_dir
from .boot_sound import BootSoundConversionError, convert_boot_sound_to_wav
from .emulators import CaptureCancelled, EmulatorError, EmulatorSettings, capture_game, discover_core, dolphin_system_files_ready, dolphin_sys_directory
from .exporter import (
    ExportOptions,
    export_selected,
    export_metadata_only,
    default_shot_rect,
    icon_template_choices,
    default_icon_template_for_system,
    render_banners_from_source,
    render_icon_from_source,
    SCREENSHOT_ONLY_TEMPLATE,
)
from .roms import ROM_EXTENSIONS, RomInfo, detect_rom, default_output_key, system_folder
from .scoring import FrameSample, select_candidates, select_nds_candidates
from .metadata import LibretroSettings, GameMetadata, lookup_libretro, update_database_for_systems
from .help_docs import APP_VERSION, HELP_SECTIONS, HELP_BY_TITLE
from .repository_art import (
    BOOT_SOUND_EXTENSIONS,
    BootSound,
    RepositoryArtwork,
    RepositoryArtworkError,
    apply_repository_artwork,
    built_in_boot_sounds,
    find_and_download_artwork_candidates,
    find_and_download_boot_sounds,
    myinstants_search_url,
    product_code_candidates,
    system_has_boot_sound_repository,
    system_has_repository,
)
from .models import GameJob

# Legacy UI compatibility note: GameJob defines capture_checked: bool = True in models.py.


DragDropTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class HoverTip:
    """Small non-blocking tooltip shown after the pointer rests on a widget."""

    _active_tip = None

    def __init__(self, widget, text: str, delay_ms: int = 500, wraplength: int = 380, display_ms: int | None = None):
        self.widget = widget
        self.text = (text or "").strip()
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self.display_ms = display_ms or max(5000, min(11000, 4500 + len(self.text) * 18))
        self._after_id = None
        self._hide_after_id = None
        self._window = None
        self._show_point = None
        self._dismissed_until_leave = False
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<ButtonPress>", self._dismiss, add="+")
        widget.bind("<MouseWheel>", self._dismiss, add="+")
        widget.bind("<Button-4>", self._dismiss, add="+")
        widget.bind("<Button-5>", self._dismiss, add="+")
        widget.bind("<KeyPress>", self._dismiss, add="+")
        widget.bind("<Destroy>", self._destroyed, add="+")
        self._install_global_dismiss_handlers()

    def _install_global_dismiss_handlers(self):
        try:
            top = self.widget.winfo_toplevel()
            if getattr(top, "_vc_hover_global_handlers", False):
                return
            top._vc_hover_global_handlers = True
            top.bind("<ButtonPress>", lambda event: HoverTip.dismiss_active(), add="+")
            top.bind("<MouseWheel>", lambda event: HoverTip.dismiss_active(), add="+")
            top.bind("<Button-4>", lambda event: HoverTip.dismiss_active(), add="+")
            top.bind("<Button-5>", lambda event: HoverTip.dismiss_active(), add="+")
            top.bind("<KeyPress>", lambda event: HoverTip.dismiss_active(), add="+")
            top.bind("<FocusOut>", lambda event: HoverTip.dismiss_active(), add="+")
        except Exception:
            pass

    @classmethod
    def dismiss_active(cls):
        active = cls._active_tip
        if active is not None:
            active._dismiss()

    def _on_enter(self, event=None):
        self._dismissed_until_leave = False
        self._schedule()

    def _on_leave(self, event=None):
        self._close()
        self._dismissed_until_leave = False

    def _on_motion(self, event=None):
        if self._window is None:
            if not self._dismissed_until_leave:
                self._schedule()
            return
        try:
            current = (event.x_root, event.y_root) if event is not None else (self.widget.winfo_pointerx(), self.widget.winfo_pointery())
            if self._show_point is None or abs(current[0] - self._show_point[0]) >= 8 or abs(current[1] - self._show_point[1]) >= 8:
                self._dismiss()
        except Exception:
            self._dismiss()

    def _schedule(self, event=None):
        if not self.text or self._dismissed_until_leave or self._window is not None:
            return
        self._cancel_timer()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except Exception:
            self._after_id = None

    def _cancel_timer(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _cancel_hide_timer(self):
        if self._hide_after_id is not None:
            try:
                self.widget.after_cancel(self._hide_after_id)
            except Exception:
                pass
            self._hide_after_id = None

    def _show(self):
        self._after_id = None
        if self._window is not None or not self.text or self._dismissed_until_leave:
            return
        try:
            if not self.widget.winfo_exists():
                return
            pointer_x = self.widget.winfo_pointerx()
            pointer_y = self.widget.winfo_pointery()
            x = pointer_x + 14
            y = pointer_y + 18
            if not (
                self.widget.winfo_rootx() <= pointer_x < self.widget.winfo_rootx() + self.widget.winfo_width()
                and self.widget.winfo_rooty() <= pointer_y < self.widget.winfo_rooty() + self.widget.winfo_height()
            ):
                return
            if HoverTip._active_tip is not None and HoverTip._active_tip is not self:
                HoverTip._active_tip._close()
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            try:
                tip.attributes("-disabled", True)
            except Exception:
                pass
            label = tk.Label(
                tip,
                text=self.text,
                justify="left",
                background="#FFFBEA",
                foreground="#202020",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=5,
                wraplength=self.wraplength,
                font=("Segoe UI", 9),
            )
            label.pack()
            tip.update_idletasks()
            sw = tip.winfo_screenwidth()
            sh = tip.winfo_screenheight()
            tw = tip.winfo_reqwidth()
            th = tip.winfo_reqheight()
            x = max(0, min(x, sw - tw - 8))
            y = max(0, min(y, sh - th - 8))
            tip.wm_geometry(f"+{x}+{y}")
            self._window = tip
            self._show_point = (pointer_x, pointer_y)
            HoverTip._active_tip = self
            self._hide_after_id = self.widget.after(self.display_ms, self._dismiss)
        except Exception:
            self._window = None

    def _dismiss(self, event=None):
        self._dismissed_until_leave = True
        self._close()

    def _close(self):
        self._cancel_timer()
        self._cancel_hide_timer()
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        self._show_point = None
        if HoverTip._active_tip is self:
            HoverTip._active_tip = None

    def _destroyed(self, event=None):
        self._dismissed_until_leave = True
        self._close()


def add_hover_tip(widget, text: str, delay_ms: int = 500):
    """Attach a hover tooltip and return the widget for compact UI construction."""
    try:
        widget._vc_hover_tip = HoverTip(widget, text, delay_ms=delay_ms)
    except Exception:
        pass
    return widget


def _asset_path(filename: str) -> Path:
    """Resolve bundled assets consistently in source and PyInstaller builds."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundle_root / "assets" / filename


def _apply_windows_native_icon(window):
    """Force the ICO onto an existing native Windows window handle."""
    if os.name != "nt":
        return
    icon_path = _asset_path("vc_title_extractor.ico")
    if not icon_path.exists():
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = window.winfo_id()
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.restype = ctypes.c_ssize_t
        image_icon = 1
        load_from_file = 0x0010
        wm_seticon = 0x0080
        icon_small = user32.LoadImageW(None, str(icon_path), image_icon, 16, 16, load_from_file)
        icon_big = user32.LoadImageW(None, str(icon_path), image_icon, 32, 32, load_from_file)
        if icon_small:
            user32.SendMessageW(hwnd, wm_seticon, 0, icon_small)
            user32.SendMessageW(hwnd, wm_seticon, 2, icon_small)
        if icon_big:
            user32.SendMessageW(hwnd, wm_seticon, 1, icon_big)
        window._native_icon_handles = (icon_small, icon_big)
    except Exception:
        pass


def apply_child_window_icon(window):
    """Apply the same application icon to secondary/detached windows."""
    try:
        icon_path = _asset_path("vc_title_extractor.ico")
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        pass
    try:
        window.after_idle(lambda: _apply_windows_native_icon(window))
    except Exception:
        pass
    try:
        root = window._root()
        photo = getattr(root, "_app_icon_photo", None)
        if photo is not None:
            window.iconphoto(False, photo)
    except Exception:
        pass


class App(DragDropTk):
    def __init__(self):
        # Give Windows the app its own taskbar identity instead of inheriting
        # the Python/Tk interpreter identity when running from source.
        try:
            if os.name == "nt":
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WiiUVC.ImageBuilder")
        except Exception:
            pass
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        try:
            icon_path = _asset_path("vc_title_extractor.ico")
            if icon_path.exists():
                self.iconbitmap(default=str(icon_path))
        except Exception:
            pass
        try:
            icon_png_path = _asset_path("vc_title_extractor.png")
            if icon_png_path.exists():
                self._app_icon_source = tk.PhotoImage(file=str(icon_png_path))
                self._app_icon_photo = self._app_icon_source.subsample(32, 32)
                self.iconphoto(True, self._app_icon_photo, self._app_icon_source)
        except Exception:
            self._app_icon_source = None
            self._app_icon_photo = None
        self.after_idle(lambda: _apply_windows_native_icon(self))
        self.after(250, lambda: _apply_windows_native_icon(self))
        self.minsize(1000, 840)
        self.jobs: list[GameJob] = []
        self._last_inline_remove_at = 0.0
        self._last_drop_at = 0.0
        self._last_drop_data = ""
        self._drag_drop_enabled = False
        self.worker: threading.Thread | None = None
        self.metadata_worker: threading.Thread | None = None
        self.artwork_worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.preview_refs = {}
        self.preview_paths = {}
        self.preview_tile_frames = {}
        self._preview_resize_job = None
        self.preview_detached_window = None
        self.preview_detached = False
        self.large_preview_window = None
        self.preview_panel = None
        self.preview_host = None
        self.preview_detach_btn = None
        self.review_artwork_matches_btn = None
        self.artwork_match_status_label = None
        self._settings = load_settings()
        self.auto_repository_artwork_var = tk.BooleanVar(
            value=bool(self._settings.get("automatic_repository_artwork", True))
        )
        self.repository_title_fallback_var = tk.BooleanVar(
            value=bool(self._settings.get("repository_title_fallback", True))
        )
        self.repository_uncertain_matches_var = tk.BooleanVar(
            value=bool(self._settings.get("repository_uncertain_matches", True))
        )
        self.libretro_artwork_var = tk.BooleanVar(
            value=bool(self._settings.get("libretro_artwork_enabled", True))
        )
        self.gamecube_artwork_var = tk.BooleanVar(
            value=bool(self._settings.get("gamecube_artwork_enabled", True))
        )
        self.boot_sound_var = tk.StringVar(value="No boot sound selected")
        self.boot_sound_combo = None
        self.boot_sound_status_label = None
        self.boot_sound_play_btn = None
        self.boot_sound_stop_btn = None
        self._boot_sound_display_map: dict[str, BootSound | None] = {}
        self._boot_sound_preview_path: Path | None = None
        self._repository_artwork_review_window = None
        saved_log_dir = str(self._settings.get("activity_log_dir", "") or "").strip()
        saved_logging_enabled = self._settings.get("activity_logging_enabled")
        self.activity_log_dir: Path | None = Path(saved_log_dir).expanduser() if saved_log_dir else None
        self.activity_logging_enabled_var = tk.BooleanVar(
            value=bool(saved_log_dir)
            and (saved_logging_enabled if isinstance(saved_logging_enabled, bool) else True)
        )
        self._activity_lines: list[str] = []
        self._activity_log_lock = threading.Lock()
        self._session_log_path: Path | None = None
        self._activity_file_error_reported = False
        self.activity_paned = None
        self.activity_dock_host = None
        self.activity_frame = None
        self.activity_logging_status_label = None
        self.activity_detached_window = None
        self.activity_detached = False
        self.activity_visible = True
        self._activity_logging_settings_window = None
        self.log_text = None
        self._initial_autosize_complete = False
        self._build_menu()
        self._build_ui()
        self._load_ui_settings()
        self._enable_drag_and_drop()
        self.log(f"{APP_NAME} v{APP_VERSION} ready.")
        self.after_idle(self._autosize_to_content)
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Add ROMs...", accelerator="Ctrl+O", command=self.add_roms)
        file_menu.add_command(label="Add ROM Folder...", accelerator="Ctrl+Shift+O", command=self.add_folder)
        file_menu.add_separator()
        file_menu.add_command(
            label="Import Screenshot for Selected Game...",
            command=self.import_images,
        )
        file_menu.add_command(
            label="Import Custom TV/Gamepad Image...",
            command=self.import_banner_image,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Search Artwork Sources for Selected Game...",
            command=self.download_repository_artwork_selected,
        )
        file_menu.add_command(
            label="Review Artwork Matches for Selected Game...",
            command=self.review_repository_artwork_selected,
        )
        file_menu.add_command(
            label="Use Generated Artwork for Selected Game",
            command=self.clear_repository_artwork_selected,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Open Selected Output Folder", command=self.open_output)
        file_menu.add_command(label="Open Selected Metadata", command=self.open_metadata)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(label="Open Configuration Folder", command=self.open_configuration_folder)
        settings_menu.add_command(label="Change Configuration Folder", command=self.change_configuration_folder)
        settings_menu.add_command(label="Use Default Configuration Folder", command=self.use_default_configuration_folder)
        settings_menu.add_separator()
        artwork_font_menu = tk.Menu(settings_menu, tearoff=False)
        artwork_font_menu.add_command(label="Select Custom Font...", command=self._browse_uwuvci_font)
        artwork_font_menu.add_command(label="Use Automatic/Default Font", command=self.use_default_artwork_font)
        artwork_font_menu.add_separator()
        artwork_font_menu.add_command(label="Show Active Font", command=self.show_active_artwork_font)
        settings_menu.add_cascade(label="Artwork Font", menu=artwork_font_menu)
        settings_menu.add_separator()
        repository_artwork_menu = tk.Menu(settings_menu, tearoff=False)
        repository_artwork_menu.add_checkbutton(
            label="Automatically Search Artwork and Boot-Sound Sources",
            variable=self.auto_repository_artwork_var,
            command=self._toggle_automatic_repository_artwork,
        )
        repository_artwork_menu.add_checkbutton(
            label="Allow Title and Metadata Fallbacks",
            variable=self.repository_title_fallback_var,
            command=self._save_ui_settings,
        )
        repository_artwork_menu.add_checkbutton(
            label="Include Possible Matches for Manual Review",
            variable=self.repository_uncertain_matches_var,
            command=self._save_ui_settings,
        )
        repository_artwork_menu.add_checkbutton(
            label="Search Libretro Thumbnails",
            variable=self.libretro_artwork_var,
            command=self._save_ui_settings,
        )
        repository_artwork_menu.add_checkbutton(
            label="Search GameCube Artwork Collection",
            variable=self.gamecube_artwork_var,
            command=self._save_ui_settings,
        )
        repository_artwork_menu.add_separator()
        repository_artwork_menu.add_command(
            label="Search for Selected Game...",
            command=self.download_repository_artwork_selected,
        )
        repository_artwork_menu.add_command(
            label="Review Artwork Matches for Selected Game...",
            command=self.review_repository_artwork_selected,
        )
        repository_artwork_menu.add_command(
            label="Open Download Cache",
            command=self.open_repository_artwork_cache,
        )
        repository_artwork_menu.add_separator()
        repository_artwork_menu.add_command(
            label="Artwork Sources and Notices",
            command=lambda: self.open_documentation("Repository Artwork"),
        )
        settings_menu.add_cascade(label="Repository Artwork", menu=repository_artwork_menu)
        settings_menu.add_separator()
        settings_menu.add_command(label="Activity Logging...", command=self.open_activity_logging_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Show Activity Log", command=self.show_activity_log)
        view_menu.add_command(label="Detach Activity Log", command=self.detach_activity_log)
        menubar.add_cascade(label="View", menu=view_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Documentation Center", accelerator="F1", command=self.open_documentation)
        help_menu.add_separator()
        help_menu.add_command(label="Getting Started", command=lambda: self.open_documentation("Getting Started"))
        help_menu.add_command(label="Artwork & Framing", command=lambda: self.open_documentation("Artwork & Framing"))
        help_menu.add_command(label="Game Details & Banner Text", command=lambda: self.open_documentation("Game Details & Banner Text"))
        help_menu.add_command(label="Metadata", command=lambda: self.open_documentation("Metadata"))
        help_menu.add_command(label="Emulator Capture Setup", command=lambda: self.open_documentation("Emulator Capture Setup"))
        help_menu.add_command(label="Supported Systems & Files", command=lambda: self.open_documentation("Supported Systems & Files"))
        help_menu.add_command(label="Repository Artwork", command=lambda: self.open_documentation("Repository Artwork"))
        help_menu.add_command(label="Boot Sounds", command=lambda: self.open_documentation("Boot Sounds"))
        help_menu.add_command(label="Activity Log & File Logging", command=lambda: self.open_documentation("Activity Log & File Logging"))
        help_menu.add_command(label="Troubleshooting", command=lambda: self.open_documentation("Troubleshooting"))
        help_menu.add_separator()
        help_menu.add_command(label="Credits & Notices", command=lambda: self.open_documentation("Credits & Notices"))
        help_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)
        self.bind_all("<F1>", lambda event: self.open_documentation())
        self.bind_all("<Control-o>", self._shortcut_add_roms)
        self.bind_all("<Control-Shift-O>", self._shortcut_add_rom_folder)

    def _shortcut_add_roms(self, event=None):
        self.add_roms()
        return "break"

    def _shortcut_add_rom_folder(self, event=None):
        self.add_folder()
        return "break"

    def open_documentation(self, section: str | None = None):
        existing = getattr(self, "_documentation_window", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                if section:
                    existing.select_section(section)
                return
        except Exception:
            pass
        self._documentation_window = DocumentationWindow(self, initial_section=section)

    def show_about(self):
        AboutWindow(self)

    def _make_grab_paned(self, parent, orient):
        """Create a paned container with an obvious, easy-to-grab resize bar."""
        return tk.PanedWindow(
            parent,
            orient=orient,
            borderwidth=0,
            relief="flat",
            sashwidth=8,
            sashrelief="raised",
            showhandle=True,
            handlesize=12,
            handlepad=8,
            background="#D0D0D0",
        )

    def _bind_responsive_wrap(self, label, container, margin: int = 24, minimum: int = 180):
        """Keep explanatory text within the width currently available to it."""
        def resize(event):
            try:
                label.config(wraplength=max(minimum, event.width - margin))
            except Exception:
                pass

        container.bind("<Configure>", resize, add="+")

    def _scroll_capture_settings(self, event):
        """Scroll Capture Settings when the pointer is inside that panel."""
        canvas = getattr(self, "capture_settings_canvas", None)
        host = getattr(self, "capture_settings_scroll_host", None)
        if canvas is None or host is None:
            return None
        try:
            pointer_x = self.winfo_pointerx()
            pointer_y = self.winfo_pointery()
            inside = (
                host.winfo_rootx() <= pointer_x < host.winfo_rootx() + host.winfo_width()
                and host.winfo_rooty() <= pointer_y < host.winfo_rooty() + host.winfo_height()
            )
            if not inside:
                return None
            delta = getattr(event, "delta", 0)
            if delta:
                units = -max(1, abs(int(delta / 120))) if delta > 0 else max(1, abs(int(delta / 120)))
            elif getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                return None
            canvas.yview_scroll(units, "units")
            return "break"
        except Exception:
            return None

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass

        shell = ttk.Frame(self, padding=10)
        shell.pack(fill="both", expand=True)

        self.activity_paned = self._make_grab_paned(shell, tk.VERTICAL)
        self.activity_paned.pack(fill="both", expand=True)
        main = ttk.Frame(self.activity_paned)
        self.activity_dock_host = ttk.Frame(self.activity_paned)
        self.activity_paned.add(main, minsize=690, stretch="always")
        self.activity_paned.add(self.activity_dock_host, minsize=110, stretch="never")

        paths = ttk.LabelFrame(main, text="Emulators and Export", padding=8)
        paths.pack(fill="x")
        paths.columnconfigure(1, weight=1)

        self.retro_var = tk.StringVar()
        self.dolphin_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.uwuvci_font_var = tk.StringVar()

        self._path_row(paths, 0, "RetroArch.exe", self.retro_var, self._browse_retro)
        self._path_row(paths, 1, "Dolphin.exe fallback", self.dolphin_var, self._browse_dolphin)
        self._path_row(paths, 2, "Export folder", self.output_var, self._browse_output)

        capture_notice = tk.Frame(
            main,
            background="#FFF4CE",
            highlightbackground="#C9A227",
            highlightthickness=1,
            padx=10,
            pady=8,
        )
        capture_notice.pack(fill="x", pady=(8, 0))
        notice_copy = tk.Frame(capture_notice, background="#FFF4CE")
        notice_copy.pack(side="left", fill="x", expand=True)
        tk.Label(
            notice_copy,
            text="Screenshot capture requires an emulator",
            background="#FFF4CE",
            foreground="#3B3100",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        notice_detail = tk.Label(
            notice_copy,
            text=(
                "Automatic ROM screenshot capture uses RetroArch and may use Dolphin for GameCube/Wii titles. "
                "If you already have a screenshot, use Import Screenshot for Selected Game instead; manual screenshot import does not require an emulator."
            ),
            background="#FFF4CE",
            foreground="#3B3100",
            justify="left",
            anchor="w",
            wraplength=940,
        )
        notice_detail.pack(fill="x", pady=(2, 0))
        self._bind_responsive_wrap(notice_detail, notice_copy, margin=12, minimum=260)
        capture_help_btn = ttk.Button(
            capture_notice,
            text="Capture Setup Help",
            command=lambda: self.open_documentation("Emulator Capture Setup"),
        )
        capture_help_btn.pack(side="right", padx=(12, 0))
        add_hover_tip(capture_help_btn, "Open the built-in emulator setup guide. Automatic capture requires RetroArch and may use Dolphin for GameCube/Wii; imported screenshots do not require an emulator.")

        metadata = ttk.LabelFrame(main, text="Game Metadata", padding=8)
        metadata.pack(fill="x", pady=(8, 0))
        metadata.columnconfigure(0, weight=1)

        metadata_summary = ttk.Label(
            metadata,
            text="Metadata is fetched automatically as soon as a ROM is added. metadata.txt is written immediately and refreshed when a database match is found.",
            foreground="#444444",
            justify="left",
            wraplength=760,
        )
        metadata_summary.grid(row=0, column=0, sticky="ew")
        self._bind_responsive_wrap(metadata_summary, metadata, margin=270, minimum=240)
        meta_buttons = ttk.Frame(metadata)
        meta_buttons.grid(row=0, column=1, sticky="e")
        fetch_metadata_btn = ttk.Button(meta_buttons, text="Fetch Metadata", command=self.fetch_metadata_selected)
        fetch_metadata_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(fetch_metadata_btn, "Re-run metadata lookup for the selected game(s). This can refresh title, release year, player count, publisher, developer, and other available details.")
        update_database_btn = ttk.Button(meta_buttons, text="Update Database", command=self.update_metadata_database)
        update_database_btn.pack(side="left")
        add_hover_tip(update_database_btn, "Refresh the local public metadata databases used for game matching. Use this when metadata is missing or you want the newest database files.")

        toolbar = ttk.Frame(main, padding=(0, 8, 0, 4))
        toolbar.pack(fill="x")
        toolbar_buttons = [
            ("Add ROMs", self.add_roms, "Add one or more supported game dumps. The ROM is used to identify the game, system, product code, and metadata."),
            ("Add ROM Folder", self.add_folder, "Scan a folder for supported ROM files and add them to the game list in one batch."),
            ("Import Screenshot for Selected Game", self.import_images, "Attach an existing title-screen screenshot to the selected loaded game. This bypasses emulator capture and immediately rebuilds artwork."),
            # Legacy label reference: text="Import Custom TV/Gamepad Image"
            ("Import Custom TV/Gamepad Image", self.import_banner_image, "For Wii/WiiWare, import a separate image to use only for TV and Gamepad artwork. The Icon Image still uses the selected screenshot."),
            # Legacy label reference: text="Use Selected Screenshot for TV/Gamepad"
            ("Use Selected Screenshot for TV/Gamepad", self.restore_screenshot_for_tv_gamepad, "For Wii/WiiWare, switch TV and Gamepad artwork back to the selected title screenshot instead of a custom imported image."),
            ("Build Images", self.build_selected_images, "Build or rebuild the selected game's enabled Icon, TV, and Gamepad images from the current screenshot, metadata, frame, crop, zoom, and text settings."),
            ("Edit Game Details", self.edit_game_details, "Edit the selected game's display name, release year, player count, and product code. Game names can use up to three explicit lines."),
            ("Open Output", self.open_output, "Open the selected game's export folder in Windows Explorer."),
            ("Open Metadata", self.open_metadata, "Open the selected game's metadata.txt file using the default Windows text editor."),
        ]
        toolbar_column_count = 4
        for column in range(toolbar_column_count):
            toolbar.columnconfigure(column, weight=1, uniform="main_toolbar")
        for i, (caption, command, tip) in enumerate(toolbar_buttons):
            btn = ttk.Button(toolbar, text=caption, command=command)
            btn.grid(
                row=i // toolbar_column_count,
                column=i % toolbar_column_count,
                sticky="ew",
                padx=(
                    0 if i % toolbar_column_count == 0 else 3,
                    0 if i % toolbar_column_count == toolbar_column_count - 1 else 3,
                ),
                pady=(0, 3),
            )
            add_hover_tip(btn, tip)

        self.workspace_paned = self._make_grab_paned(main, tk.VERTICAL)
        self.workspace_paned.pack(fill="both", expand=True)
        self.rom_list_panel = ttk.LabelFrame(self.workspace_paned, text="ROM List", padding=5)
        self.details_paned = self._make_grab_paned(self.workspace_paned, tk.HORIZONTAL)
        self.workspace_paned.add(self.rom_list_panel, minsize=205, stretch="always")
        self.workspace_paned.add(self.details_paned, minsize=210, stretch="always")
        self.rom_list_panel.columnconfigure(0, weight=1)
        self.rom_list_panel.rowconfigure(0, weight=1, minsize=80)

        rom_table = ttk.Frame(self.rom_list_panel)
        rom_table.grid(row=0, column=0, sticky="nsew")
        rom_table.columnconfigure(0, weight=1)
        rom_table.rowconfigure(0, weight=1)

        columns = ("capture", "remove", "file", "source", "title", "system", "year", "players", "code", "status")
        self.tree = ttk.Treeview(rom_table, columns=columns, show="headings", height=8, selectmode="extended")
        widths = {"capture": 68, "remove": 62, "file": 210, "source": 70, "title": 245, "system": 115, "year": 65, "players": 70, "code": 85, "status": 220}
        headings = {"capture": "Capture", "remove": "Remove", "file": "Input File", "source": "Source", "title": "Game Name", "system": "System", "year": "Released", "players": "Players", "code": "Product Code", "status": "Status"}
        for c in columns:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="center" if c in {"capture", "remove"} else "w", stretch=c not in {"capture", "remove", "year", "players"})
        tree_y_scroll = ttk.Scrollbar(rom_table, orient="vertical", command=self.tree.yview)
        tree_x_scroll = ttk.Scrollbar(rom_table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_y_scroll.set, xscrollcommand=tree_x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_y_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll.grid(row=1, column=0, sticky="ew")
        add_hover_tip(self.tree, "Game list. Drop supported ROM files or folders here to add them. Click the box in the Capture column to include or exclude a ROM from batch capture. Click the X in the Remove column to remove only that row from the list; the source ROM is not deleted. If exactly one row is highlighted, Capture ROM Screens processes only that highlighted ROM regardless of its box. Double-click a completed game to review captured candidate frames.")
        self.tree.bind("<Button-1>", self._tree_capture_checkbox_click, add="+")
        self.tree.bind("<Button-1>", self._tree_remove_click, add="+")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_selection_changed())
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree.bind("<Delete>", self._tree_remove_checked_shortcut)

        capture_choice_bar = ttk.Frame(self.rom_list_panel)
        capture_choice_bar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        capture_choice_bar.columnconfigure(0, weight=1)
        capture_choice_summary = ttk.Label(
            capture_choice_bar,
            text="Capture uses one highlighted ROM by itself; otherwise it uses checked ROMs. Remove Checked removes all checked ROMs from this list.",
            foreground="#555555",
            justify="left",
            wraplength=620,
        )
        capture_choice_summary.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        self._bind_responsive_wrap(capture_choice_summary, capture_choice_bar, margin=12, minimum=220)
        for column in range(4):
            capture_choice_bar.columnconfigure(column, weight=1, uniform="capture_actions")
        clear_capture_checks_btn = ttk.Button(capture_choice_bar, text="Clear Checks", command=self.clear_capture_checks)
        clear_capture_checks_btn.grid(row=1, column=2, sticky="ew")
        add_hover_tip(clear_capture_checks_btn, "Clear every Capture checkbox. A single highlighted ROM can still be captured by itself.")
        check_all_capture_btn = ttk.Button(capture_choice_bar, text="Check All", command=self.check_all_for_capture)
        check_all_capture_btn.grid(row=1, column=1, sticky="ew", padx=5)
        add_hover_tip(check_all_capture_btn, "Enable the Capture checkbox for every loaded ROM.")
        self.check_selected_capture_btn = ttk.Button(
            capture_choice_bar,
            text="Check Selected",
            command=self.check_selected_for_capture,
            state="disabled",
        )
        self.check_selected_capture_btn.grid(row=1, column=0, sticky="ew")
        add_hover_tip(
            self.check_selected_capture_btn,
            "Toggle the Capture boxes for highlighted ROMs. If they are all checked, this clears them; otherwise it checks them.",
        )
        self.remove_checked_btn = ttk.Button(
            capture_choice_bar,
            text="Remove Checked",
            command=self.remove_checked,
            state="disabled",
        )
        self.remove_checked_btn.grid(row=1, column=3, sticky="ew", padx=(5, 0))
        add_hover_tip(self.remove_checked_btn, "Remove every checked ROM from the application list. Source ROMs and generated files are not deleted.")

        capture_settings_panel = ttk.LabelFrame(self.details_paned, text="Capture Settings", padding=5)
        right = ttk.LabelFrame(self.details_paned, text="Selected Preview", padding=8)
        self.details_paned.add(capture_settings_panel, minsize=500, stretch="always")
        self.details_paned.add(right, minsize=360, stretch="always")
        self.preview_panel = right

        canvas_background = style.lookup("TFrame", "background") or self.cget("background")
        self.capture_settings_canvas = tk.Canvas(
            capture_settings_panel,
            borderwidth=0,
            highlightthickness=0,
            background=canvas_background,
        )
        self.capture_settings_scroll_host = capture_settings_panel
        capture_scrollbar = ttk.Scrollbar(
            capture_settings_panel,
            orient="vertical",
            command=self.capture_settings_canvas.yview,
        )
        self.capture_settings_canvas.configure(yscrollcommand=capture_scrollbar.set)
        capture_scrollbar.pack(side="right", fill="y")
        self.capture_settings_canvas.pack(side="left", fill="both", expand=True)
        left = ttk.Frame(self.capture_settings_canvas, padding=(3, 3, 8, 3))
        capture_settings_window = self.capture_settings_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind(
            "<Configure>",
            lambda event: self.capture_settings_canvas.configure(scrollregion=self.capture_settings_canvas.bbox("all")),
            add="+",
        )
        self.capture_settings_canvas.bind(
            "<Configure>",
            lambda event: self.capture_settings_canvas.itemconfigure(capture_settings_window, width=event.width),
            add="+",
        )
        self.bind_all("<MouseWheel>", self._scroll_capture_settings, add="+")
        self.bind_all("<Button-4>", self._scroll_capture_settings, add="+")
        self.bind_all("<Button-5>", self._scroll_capture_settings, add="+")

        self.warmup_var = tk.DoubleVar(value=4.0)
        self.duration_var = tk.DoubleVar(value=45.0)
        self.interval_var = tk.DoubleVar(value=0.75)
        self.candidates_var = tk.IntVar(value=6)
        self.crop_var = tk.StringVar(value="auto")
        self.make_icon_var = tk.BooleanVar(value=True)
        self.make_tv_var = tk.BooleanVar(value=True)
        self.make_gamepad_var = tk.BooleanVar(value=True)
        self.include_banner_metadata_var = tk.BooleanVar(value=True)
        self.include_game_title_var = tk.BooleanVar(value=True)
        self.include_release_date_var = tk.BooleanVar(value=True)
        self.include_player_count_var = tk.BooleanVar(value=True)
        self.icon_frame_var = tk.StringVar(value="Screenshot only")
        self.tv_gamepad_source_var = tk.StringVar(value="Selected Screenshot")
        self._icon_frame_map: dict[str, str] = {}

        capture_settings_summary = ttk.Label(
            left,
            text=(
                "Configure capture timing, screen crop, output images, icon framing, artwork source, and boot sound. "
                "Hover over a control for details."
            ),
            foreground="#555555",
            font=("Segoe UI", 9),
            justify="left",
            wraplength=470,
        )
        capture_settings_summary.pack(fill="x", pady=(0, 6))
        self._bind_responsive_wrap(capture_settings_summary, left, margin=24, minimum=240)

        fields = ttk.Frame(left)
        fields.pack(fill="x")
        labels = [
            ("Warm-up seconds", self.warmup_var, 0, 0, "How long the emulator runs before screenshots start. Increase this if startup logos or boot screens are still appearing when capture begins."),
            ("Scan duration", self.duration_var, 0, 2, "How many seconds the application scans for possible title screens after warm-up. Longer scans can find later title screens but take more time."),
            ("Sample interval", self.interval_var, 1, 0, "Seconds between captured samples. Smaller values capture more frames; larger values reduce the number of screenshots analyzed."),
            ("Candidates to keep", self.candidates_var, 1, 2, "How many of the highest-scoring captured frames are retained for Review Selected. More candidates provide more manual choices."),
        ]
        for label_text, var, row, col, tip in labels:
            lbl = ttk.Label(fields, text=label_text)
            lbl.grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
            entry = ttk.Entry(fields, textvariable=var, width=10)
            entry.grid(row=row, column=col + 1, sticky="w", padx=(0, 14), pady=2)
            add_hover_tip(lbl, tip)
            add_hover_tip(entry, tip)
        crop_lbl = ttk.Label(fields, text="Screen crop")
        crop_lbl.grid(row=2, column=0, sticky="w", pady=2)
        crop_combo = ttk.Combobox(fields, textvariable=self.crop_var, values=("auto", "full", "top", "bottom", "left", "right"), state="readonly", width=12)
        crop_combo.grid(row=2, column=1, sticky="w", padx=(0, 14), pady=2)
        crop_tip = "Choose what portion of emulator screenshots is analyzed/exported: Auto = application chooses; Full = whole frame; Top/Bottom = corresponding half; Left/Right = corresponding half. Nintendo DS automatic candidates are already separated to one screen."
        add_hover_tip(crop_lbl, crop_tip)
        add_hover_tip(crop_combo, crop_tip)
        # Formerly labeled "Screenshot Placement / Size". This changes the template rectangle, not crop focus.
        placement_btn = ttk.Button(fields, text="Template Placement / Size", command=self.edit_screenshot_placement)
        placement_btn.grid(row=2, column=2, columnspan=2, sticky="w", pady=2)
        add_hover_tip(placement_btn, "Change the X/Y position and width/height of the screenshot opening on the TV template. This changes the destination rectangle; use the image framing controls to move or zoom the image inside that rectangle.")

        outputs = ttk.LabelFrame(left, text="Create output images", padding=6)
        outputs.pack(fill="x", pady=(8, 0))
        outputs.columnconfigure(3, weight=1)
        icon_output_cb = ttk.Checkbutton(outputs, text="Icon Image", variable=self.make_icon_var)
        icon_output_cb.grid(row=0, column=0, sticky="w", padx=(0, 10))
        add_hover_tip(icon_output_cb, "Create Icon Image.png at 128 x 128. Disable this if you do not want the icon output rebuilt.")
        tv_output_cb = ttk.Checkbutton(outputs, text="TV Image", variable=self.make_tv_var)
        tv_output_cb.grid(row=0, column=1, sticky="w", padx=(0, 10))
        add_hover_tip(tv_output_cb, "Create TV Image.png at 1280 x 720 using the selected system template, artwork framing, and optional metadata text.")
        gamepad_output_cb = ttk.Checkbutton(outputs, text="Gamepad Image", variable=self.make_gamepad_var)
        gamepad_output_cb.grid(row=0, column=2, sticky="w", padx=(0, 10))
        add_hover_tip(gamepad_output_cb, "Create Gamepad Image.png at 854 x 480. Gamepad framing can be adjusted independently or linked to TV framing.")
        output_buttons = ttk.Frame(outputs)
        output_buttons.grid(row=0, column=4, sticky="e")
        select_all_btn = ttk.Button(output_buttons, text="Select All", command=self._select_all_outputs)
        select_all_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(select_all_btn, "Enable Icon, TV, and Gamepad image generation.")
        # Legacy construction reference retained for compatibility: ttk.Button(output_buttons, text="Select None", command=self._select_no_outputs).pack
        select_none_btn = ttk.Button(output_buttons, text="Select None", command=self._select_no_outputs)
        select_none_btn.pack(side="left")
        add_hover_tip(select_none_btn, "Disable Icon, TV, and Gamepad image generation. metadata.txt can still be written independently.")
        # Legacy label retained in source for compatibility with older automated checks:
        # "Include game title / metadata on TV and Gamepad images"
        overlay_cb = ttk.Checkbutton(
            outputs,
            text="Enable metadata overlay",
            variable=self.include_banner_metadata_var,
            command=self._apply_banner_metadata_setting,
        )
        overlay_cb.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        add_hover_tip(overlay_cb, "Master switch for text drawn onto TV and Gamepad images. When enabled, the individual Game title, Release date, and Player count options below control which text appears. metadata.txt is unaffected.")
        meta_parts = ttk.Frame(outputs)
        meta_parts.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))
        title_cb = ttk.Checkbutton(meta_parts, text="Game title", variable=self.include_game_title_var, command=self._apply_banner_metadata_setting)
        title_cb.pack(side="left", padx=(0, 10))
        add_hover_tip(title_cb, "Include the editable Game Name on TV and Gamepad artwork. Manual line breaks from Edit Game Details are preserved, up to three lines.")
        release_cb = ttk.Checkbutton(meta_parts, text="Release date", variable=self.include_release_date_var, command=self._apply_banner_metadata_setting)
        release_cb.pack(side="left", padx=(0, 10))
        add_hover_tip(release_cb, "Include the game's release year on TV and Gamepad artwork when a valid year is available.")
        players_cb = ttk.Checkbutton(meta_parts, text="Player count", variable=self.include_player_count_var, command=self._apply_banner_metadata_setting)
        players_cb.pack(side="left")
        add_hover_tip(players_cb, "Include the player-count text on TV and Gamepad artwork, such as Players: 1-4.")

        icon_frame_row = ttk.Frame(left)
        icon_frame_row.pack(fill="x", pady=(8, 0))
        icon_frame_label = ttk.Label(icon_frame_row, text="Icon Image frame", width=22)
        icon_frame_label.pack(side="left")
        self.icon_frame_combo = ttk.Combobox(icon_frame_row, textvariable=self.icon_frame_var, state="disabled", width=24)
        self.icon_frame_combo.pack(side="left", fill="x", expand=True)
        self.icon_frame_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_selected_icon_frame())
        icon_frame_tip = "Choose the selected game's 128 x 128 icon treatment. Choices depend on the system and normally include Screenshot only plus system-appropriate Light/Dark frames; Wii uses WiiWare Frame or Wii Frame."
        add_hover_tip(icon_frame_label, icon_frame_tip)
        add_hover_tip(self.icon_frame_combo, icon_frame_tip)

        tv_source_row = ttk.Frame(left)
        tv_source_row.pack(fill="x", pady=(8, 0))
        tv_source_label = ttk.Label(tv_source_row, text="Wii TV/Gamepad source", width=22)
        tv_source_label.pack(side="left")
        self.tv_gamepad_source_combo = ttk.Combobox(
            tv_source_row, textvariable=self.tv_gamepad_source_var, state="disabled", width=28
        )
        self.tv_gamepad_source_combo.pack(side="left", fill="x", expand=True)
        self.tv_gamepad_source_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_tv_gamepad_source())
        tv_source_tip = "Wii/WiiWare only. Selected Screenshot uses the retained title screenshot for TV/Gamepad. Custom Image uses the separately imported TV/Gamepad artwork. The Icon Image always uses the selected screenshot."
        add_hover_tip(tv_source_label, tv_source_tip)
        add_hover_tip(self.tv_gamepad_source_combo, tv_source_tip)

        boot_sound_panel = ttk.LabelFrame(left, text="Boot Sound", padding=6)
        boot_sound_panel.pack(fill="x", pady=(8, 0))
        boot_sound_panel.columnconfigure(0, weight=1)
        self.boot_sound_combo = ttk.Combobox(
            boot_sound_panel,
            textvariable=self.boot_sound_var,
            state="disabled",
        )
        self.boot_sound_combo.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.boot_sound_combo.bind("<<ComboboxSelected>>", self._apply_selected_boot_sound)
        boot_sound_tip = (
            "Choose a built-in, manual, downloaded per-game, or console-specific startup sound for the selected game. "
            "Supported files are WAV, MP3, and BTSND. The active sound is converted to bootSound.wav."
        )
        add_hover_tip(self.boot_sound_combo, boot_sound_tip)
        browse_sound_btn = ttk.Button(boot_sound_panel, text="Browse...", command=self.browse_boot_sound_selected)
        browse_sound_btn.grid(row=1, column=0, sticky="ew", pady=(5, 0), padx=(0, 4))
        add_hover_tip(
            browse_sound_btn,
            "Import a WAV, MP3, or BTSND file for this game. A converted copy is stored in the configuration directory so it remains available in later sessions.",
        )
        console_sound_btn = ttk.Button(
            boot_sound_panel,
            text="Set Console...",
            command=self.set_console_boot_sound_selected,
        )
        console_sound_btn.grid(row=1, column=1, sticky="ew", pady=(5, 0), padx=4)
        add_hover_tip(
            console_sound_btn,
            "Choose a system-specific startup sound for this console. It becomes the reusable default for current and future games from the same console.",
        )
        search_sound_btn = ttk.Button(boot_sound_panel, text="Search Sources", command=self.search_boot_sound_selected)
        search_sound_btn.grid(row=1, column=2, sticky="ew", pady=(5, 0), padx=4)
        add_hover_tip(search_sound_btn, "Search configured community repositories for an exact per-game sound, then a distinct startup sound for this console.")
        clear_sound_btn = ttk.Button(boot_sound_panel, text="Clear", command=self.clear_boot_sound_selected)
        clear_sound_btn.grid(row=1, column=3, sticky="ew", pady=(5, 0), padx=(4, 0))
        add_hover_tip(clear_sound_btn, "Remove the selected boot sound and any generated bootSound file from this game's output folder.")
        more_sounds_btn = ttk.Button(
            boot_sound_panel,
            text="More Sounds...",
            command=self.browse_myinstants_boot_sounds,
        )
        more_sounds_btn.grid(row=2, column=0, sticky="ew", pady=(5, 0), padx=(0, 4))
        add_hover_tip(
            more_sounds_btn,
            "Open a console-specific MyInstants search. Download a sound you are permitted to use, then import it with Browse or Set Console.",
        )
        self.boot_sound_play_btn = ttk.Button(
            boot_sound_panel,
            text="Play Boot Sound",
            command=self.play_boot_sound_selected,
            state="disabled",
        )
        self.boot_sound_play_btn.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(5, 0), padx=4)
        add_hover_tip(self.boot_sound_play_btn, "Stop any prior preview, reconvert the active selection, and play that exact manual, per-game, or console sound.")
        self.boot_sound_stop_btn = ttk.Button(
            boot_sound_panel,
            text="Stop",
            command=self.stop_boot_sound,
            state="disabled",
        )
        self.boot_sound_stop_btn.grid(row=2, column=3, sticky="ew", pady=(5, 0), padx=(4, 0))
        add_hover_tip(self.boot_sound_stop_btn, "Stop boot-sound playback.")
        for column in range(4):
            boot_sound_panel.columnconfigure(column, weight=1, uniform="boot_sound_actions")
        self.boot_sound_status_label = ttk.Label(
            boot_sound_panel,
            text="Select one game to configure its boot sound.",
            foreground="#555555",
            justify="left",
            wraplength=460,
        )
        self.boot_sound_status_label.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        self._bind_responsive_wrap(self.boot_sound_status_label, boot_sound_panel, margin=16, minimum=220)

        preview_controls = ttk.Frame(right)
        preview_controls.pack(fill="x", pady=(0, 4))
        preview_controls.columnconfigure(0, weight=1)
        self.preview_detach_btn = ttk.Button(preview_controls, text="Detach Preview", command=self.detach_preview)
        self.preview_detach_btn.grid(row=0, column=2)
        add_hover_tip(self.preview_detach_btn, "Open Selected Preview in a separate resizable window. The detached preview stays available while you adjust image framing.")
        adjust_image_btn = ttk.Button(
            preview_controls,
            text="Adjust Image Crop & Zoom",
            command=self.edit_image_crop_position,
        )
        adjust_image_btn.grid(row=0, column=1, padx=(0, 6))
        add_hover_tip(adjust_image_btn, "Open live framing controls for Icon, TV, and Gamepad images. Adjust horizontal/vertical focus, zoom, and zoom-out background fill; TV and Gamepad controls can be linked.")
        self.artwork_match_status_label = ttk.Label(
            preview_controls,
            text="No retained artwork matches",
            foreground="#555555",
        )
        self.artwork_match_status_label.grid(row=1, column=0, sticky="w", pady=(5, 0), padx=(0, 8))
        self.review_artwork_matches_btn = ttk.Button(
            preview_controls,
            text="Review Artwork Matches",
            command=self.review_repository_artwork_selected,
            state="disabled",
        )
        self.review_artwork_matches_btn.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        add_hover_tip(
            self.review_artwork_matches_btn,
            "Open every retained repository image for the selected game, compare them at a larger size, and apply the preferred match.",
        )

        self.preview_host = ttk.Frame(right)
        self.preview_host.pack(fill="both", expand=True)
        self.preview_labels = {}
        self.preview_title_labels = {}
        self.preview_tile_frames = {}
        self._build_preview_grid(self.preview_host)

        progress_line = ttk.Frame(main, padding=(0, 7, 0, 0))
        progress_line.pack(fill="x")
        self.progress = ttk.Progressbar(progress_line, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        add_hover_tip(self.progress, "Shows progress through the current capture batch.")
        self.start_btn = ttk.Button(progress_line, text="Capture ROM Screens", command=self.start_capture)
        self.start_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(self.start_btn, "Capture exactly one highlighted ROM, or otherwise capture all ROMs whose Capture boxes are checked, using the current emulator and capture settings.")
        self.review_btn = ttk.Button(progress_line, text="Review Selected", command=self.review_selected)
        self.review_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(self.review_btn, "Open the retained candidate frames for one selected game so you can choose a different title screen and re-export its artwork.")
        self.stop_btn = ttk.Button(progress_line, text="Stop and Use Captures", command=self.stop_capture, state="disabled")
        self.stop_btn.pack(side="left")
        add_hover_tip(self.stop_btn, "Stop the active emulator capture, analyze the frames already collected, export the best result available, and stop the remaining batch.")

        self._build_activity_panel(self.activity_dock_host, detached=False)

    def _build_activity_panel(self, parent, detached: bool):
        for child in parent.winfo_children():
            child.destroy()

        frame = ttk.LabelFrame(parent, text="Activity", padding=6)
        if detached:
            frame.pack(fill="both", expand=True, padx=8, pady=8)
        else:
            frame.pack(fill="both", expand=True, pady=(7, 0))
        self.activity_frame = frame

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 5))
        controls.columnconfigure(0, weight=1)
        self.activity_logging_status_label = ttk.Label(controls, anchor="w", justify="left", wraplength=760)
        self.activity_logging_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._bind_responsive_wrap(self.activity_logging_status_label, controls, margin=190, minimum=220)
        add_hover_tip(
            self.activity_logging_status_label,
            "Shows whether Activity messages are being written live to a log file and which output folder is configured. Change these options from Settings, Activity Logging.",
        )
        if detached:
            attach_btn = ttk.Button(controls, text="Reattach", command=self.reattach_activity_log)
            attach_tip = "Return the Activity log to the resizable panel at the bottom of the main window."
        else:
            attach_btn = ttk.Button(controls, text="Detach", command=self.detach_activity_log)
            attach_tip = "Open the Activity log in its own independently resizable window."
        attach_btn.grid(row=0, column=1, padx=(0, 5))
        add_hover_tip(attach_btn, attach_tip)
        close_btn = ttk.Button(controls, text="Close", command=self.close_activity_log)
        close_btn.grid(row=0, column=2)
        add_hover_tip(close_btn, "Close the Activity panel. Use View, Show Activity Log to open it again; logging continues while the panel is closed.")

        self.log_text = ScrolledText(frame, height=7, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        add_hover_tip(
            self.log_text,
            "Activity messages for metadata, capture, exports, source changes, and errors. The panel may be resized, closed, or detached without stopping live file logging.",
        )
        if self._activity_lines:
            self.log_text.config(state="normal")
            self.log_text.insert("end", "\n".join(self._activity_lines) + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self._update_activity_logging_status()

    def _dispose_activity_panel(self):
        frame = self.activity_frame
        self.activity_frame = None
        self.log_text = None
        self.activity_logging_status_label = None
        if frame is not None:
            try:
                frame.destroy()
            except Exception:
                pass

    def _activity_pane_is_attached(self) -> bool:
        if self.activity_paned is None or self.activity_dock_host is None:
            return False
        try:
            return str(self.activity_dock_host) in self.activity_paned.panes()
        except Exception:
            return False

    def show_activity_log(self):
        if self.activity_detached:
            top = self.activity_detached_window
            try:
                if top is not None and top.winfo_exists():
                    top.deiconify()
                    top.lift()
                    top.focus_force()
                    return
            except Exception:
                pass
            self.activity_detached = False
            self.activity_detached_window = None
        if self.activity_paned is None or self.activity_dock_host is None:
            return
        if not self._activity_pane_is_attached():
            self.activity_paned.add(self.activity_dock_host, minsize=110, stretch="never")
        self.activity_visible = True
        try:
            live_widget = self.log_text is not None and self.log_text.winfo_exists()
        except Exception:
            live_widget = False
        if not live_widget:
            self._build_activity_panel(self.activity_dock_host, detached=False)

    def close_activity_log(self):
        self._dispose_activity_panel()
        if self.activity_detached:
            top = self.activity_detached_window
            self.activity_detached_window = None
            self.activity_detached = False
            if top is not None:
                try:
                    top.destroy()
                except Exception:
                    pass
        elif self._activity_pane_is_attached():
            try:
                self.activity_paned.forget(self.activity_dock_host)
            except Exception:
                pass
        self.activity_visible = False

    def detach_activity_log(self):
        if self.activity_detached:
            self.show_activity_log()
            return
        if self.activity_paned is None or self.activity_dock_host is None:
            return
        self._dispose_activity_panel()
        if self._activity_pane_is_attached():
            try:
                self.activity_paned.forget(self.activity_dock_host)
            except Exception:
                pass

        top = tk.Toplevel(self)
        apply_child_window_icon(top)
        self.activity_detached_window = top
        self.activity_detached = True
        self.activity_visible = True
        top.title("Activity Log")
        top.minsize(620, 260)
        top.geometry("850x360")
        top.resizable(True, True)
        top.protocol("WM_DELETE_WINDOW", self.reattach_activity_log)
        self._build_activity_panel(top, detached=True)

    def reattach_activity_log(self):
        if not self.activity_detached:
            self.show_activity_log()
            return
        self._dispose_activity_panel()
        top = self.activity_detached_window
        self.activity_detached_window = None
        self.activity_detached = False
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass
        if self.activity_paned is None or self.activity_dock_host is None:
            return
        if not self._activity_pane_is_attached():
            self.activity_paned.add(self.activity_dock_host, minsize=110, stretch="never")
        self.activity_visible = True
        self._build_activity_panel(self.activity_dock_host, detached=False)

    def _autosize_to_content(self):
        """Choose the initial window size exactly once.

        After startup, the user's chosen window size and internal sash positions
        remain authoritative. ROM selection and preview refreshes must not
        recalculate or replace the main-window geometry.
        """
        if self._initial_autosize_complete:
            return
        try:
            self.update_idletasks()
            req_w = max(self.winfo_reqwidth(), 1000)
            req_h = max(self.winfo_reqheight(), 840)
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            max_w = max(900, screen_w - 40)
            max_h = max(700, screen_h - 80)
            width = min(req_w, max_w)
            height = min(req_h, max_h)
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2 - 10)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass
        finally:
            self._initial_autosize_complete = True

    def _reset_preview_host_layout(self, parent):
        for child in parent.winfo_children():
            child.destroy()
        for i in range(2):
            parent.columnconfigure(i, weight=0, minsize=0)
            parent.rowconfigure(i, weight=0, minsize=0)

    def _build_preview_grid(self, parent):
        self._reset_preview_host_layout(parent)
        parent.columnconfigure(0, weight=1, minsize=150)
        parent.columnconfigure(1, weight=1, minsize=150)
        parent.rowconfigure(0, weight=1, minsize=80)
        parent.rowconfigure(1, weight=1, minsize=80)
        self.preview_labels = {}
        self.preview_title_labels = {}
        self.preview_tile_frames = {}
        self._make_preview_tile(parent, 0, 0, "Title Screen", "title")
        self._make_preview_tile(parent, 0, 1, "Icon Image", "icon")
        self._make_preview_tile(parent, 1, 0, "TV Image", "tv")
        self._make_preview_tile(parent, 1, 1, "Gamepad Image", "gamepad")

    def _autosize_detached_preview(self):
        top = self.preview_detached_window
        if top is None:
            return
        try:
            top.update_idletasks()
            req_w = max(top.winfo_reqwidth(), 620)
            req_h = max(top.winfo_reqheight(), 420)
            screen_w = top.winfo_screenwidth()
            screen_h = top.winfo_screenheight()
            width = min(req_w, max(620, screen_w - 80))
            height = min(req_h, max(420, screen_h - 120))
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            top.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _make_preview_tile(self, parent, row, column, heading, key):
        frame = ttk.LabelFrame(parent, text=heading, padding=4)
        frame.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        lbl = ttk.Label(frame, anchor="center", text="Not available", cursor="hand2")
        lbl.grid(row=0, column=0, sticky="nsew")
        frame.bind("<Configure>", self._schedule_preview_refresh)
        lbl.bind("<Configure>", self._schedule_preview_refresh)
        lbl.bind("<Button-1>", lambda event, preview_key=key: self.open_large_preview(preview_key), add="+")
        preview_tips = {
            "title": "Selected title-screen source. This is saved as title_screen.png and is the source for Icon artwork. Wii/WiiWare TV/Gamepad can optionally use a separate custom image.",
            "icon": "Preview of Icon Image.png at its generated 128 x 128 composition. Use Icon Image frame and Adjust Image Crop & Zoom to change it.",
            "tv": "Preview of TV Image.png (1280 x 720). It combines the system template, framed screenshot/custom artwork, and optional metadata text.",
            "gamepad": "Preview of Gamepad Image.png (854 x 480). It can share TV framing through Link TV and Gamepad crop & zoom or be positioned independently.",
        }
        tip = preview_tips.get(key, f"Preview of {heading}.") + " Click the image to open a larger resizable preview."
        add_hover_tip(frame, tip)
        add_hover_tip(lbl, tip)
        self.preview_labels[key] = lbl
        self.preview_title_labels[key] = heading
        self.preview_tile_frames[key] = frame

    def _schedule_preview_refresh(self, event=None):
        try:
            if self._preview_resize_job is not None:
                self.after_cancel(self._preview_resize_job)
        except Exception:
            pass
        self._preview_resize_job = self.after(60, self._refresh_preview_images)

    def _refresh_preview_images(self):
        self._preview_resize_job = None
        if not getattr(self, "preview_paths", None):
            return
        refs = {}
        for key, lbl in self.preview_labels.items():
            path = self.preview_paths.get(key)
            if not path or not Path(path).exists():
                continue
            try:
                avail_w = max(32, lbl.winfo_width() - 8)
                avail_h = max(32, lbl.winfo_height() - 8)
                if avail_w <= 32 or avail_h <= 32:
                    if key == "icon":
                        avail_w, avail_h = 128, 128
                    else:
                        avail_w, avail_h = 260, 150
                with Image.open(path) as im:
                    image = im.convert("RGB")
                    src_w, src_h = image.size
                    scale = min(avail_w / src_w, avail_h / src_h, 1.0)
                    new_size = (max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale))))
                    if new_size != image.size:
                        image = image.resize(new_size, Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                refs[key] = photo
                lbl.config(image=photo, text="")
            except Exception as exc:
                lbl.config(image="", text=f"Preview error: {exc}")
        self.preview_refs = refs

    def open_large_preview(self, key: str):
        path = getattr(self, "preview_paths", {}).get(key)
        if not path or not Path(path).is_file():
            messagebox.showinfo(APP_NAME, "No image is currently available for this preview.", parent=self)
            return
        heading = self.preview_title_labels.get(key, Path(path).stem)
        window = self.large_preview_window
        try:
            if window is not None and window.winfo_exists():
                window.show_image(key, heading, Path(path))
                return
        except Exception:
            pass
        self.large_preview_window = LargeImagePreviewWindow(self, key, heading, Path(path))

    def _large_preview_closed(self, window):
        if self.large_preview_window is window:
            self.large_preview_window = None

    def _sync_large_preview(self):
        window = self.large_preview_window
        try:
            if window is None or not window.winfo_exists():
                self.large_preview_window = None
                return
            key = window.preview_key
            path = getattr(self, "preview_paths", {}).get(key)
            heading = self.preview_title_labels.get(key, "Image Preview")
            if path and Path(path).is_file():
                window.show_image(key, heading, Path(path), preserve_geometry=True)
            else:
                window.show_unavailable(key, heading)
        except Exception:
            self.large_preview_window = None

    def detach_preview(self):
        if self.preview_detached:
            self.reattach_preview()
            return
        if not self.preview_host:
            return
        self.preview_detached = True
        self._reset_preview_host_layout(self.preview_host)
        ttk.Label(self.preview_host, text="Selected Preview is detached.", foreground="#555555").pack(expand=True, pady=12)
        if self.preview_detach_btn:
            self.preview_detach_btn.config(text="Reattach Preview", command=self.reattach_preview)

        top = tk.Toplevel(self)
        apply_child_window_icon(top)
        self.preview_detached_window = top
        top.title("Selected Preview")
        top.minsize(620, 420)
        top.geometry("760x540")
        top.protocol("WM_DELETE_WINDOW", self.reattach_preview)
        controls = ttk.Frame(top, padding=(8, 8, 8, 0))
        controls.pack(fill="x")
        ttk.Label(controls, text="Selected Preview", font=("Segoe UI", 10, "bold")).pack(side="left")
        reattach_btn = ttk.Button(controls, text="Reattach Preview", command=self.reattach_preview)
        reattach_btn.pack(side="right")
        add_hover_tip(reattach_btn, "Return this preview panel to the main application window.")
        detached_adjust_btn = ttk.Button(controls, text="Adjust Image Crop & Zoom", command=self.edit_image_crop_position)
        detached_adjust_btn.pack(side="right", padx=(0, 6))
        add_hover_tip(detached_adjust_btn, "Open live Icon, TV, and Gamepad framing controls while keeping this detached preview visible behind the adjustment window.")
        detached_host = ttk.Frame(top, padding=8)
        detached_host.pack(fill="both", expand=True)
        self._build_preview_grid(detached_host)
        self._update_preview()
        self.after_idle(self._autosize_detached_preview)

    def reattach_preview(self):
        if not self.preview_detached:
            return
        self.preview_detached = False
        top = self.preview_detached_window
        self.preview_detached_window = None
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass
        if self.preview_host:
            self._build_preview_grid(self.preview_host)
        if self.preview_detach_btn:
            self.preview_detach_btn.config(text="Detach Preview", command=self.detach_preview)
        self._update_preview()

    def _path_row(self, parent, row, label, var, command):
        tips = {
            "RetroArch.exe": "Path to RetroArch.exe used for automatic screenshot capture on supported systems. The appropriate Libretro core must also be installed in RetroArch.",
            "Dolphin.exe fallback": "Optional path to Dolphin.exe. Used as a fallback for GameCube/Wii screenshot capture when the RetroArch Dolphin core is unavailable or produces unusable frames.",
            "Export folder": "Root folder where each game's generated artwork and metadata folder is created. Games are organized by system and game name/product code.",
        }
        tip = tips.get(label, label)
        lbl = ttk.Label(parent, text=label, width=20)
        lbl.grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)
        browse_btn = ttk.Button(parent, text="Browse", command=command, width=10)
        browse_btn.grid(row=row, column=2, pady=2)
        add_hover_tip(lbl, tip)
        add_hover_tip(entry, tip)
        add_hover_tip(browse_btn, f"Browse for {label.lower()}.")

    def _load_ui_settings(self):
        s = self._settings
        self.retro_var.set(s.get("retroarch_path", ""))
        self.dolphin_var.set(s.get("dolphin_path", ""))
        self.output_var.set(s.get("output_root", str(Path.home() / "Documents" / "UWUVCI Artwork")))
        self.uwuvci_font_var.set(s.get("uwuvci_font_path", self._auto_detect_uwuvci_font()))
        self.warmup_var.set(s.get("warmup_seconds", 4.0))
        self.duration_var.set(s.get("duration_seconds", 45.0))
        self.interval_var.set(s.get("interval_seconds", 0.75))
        self.candidates_var.set(s.get("candidate_count", 6))
        self.make_icon_var.set(s.get("create_icon_image", True))
        self.make_tv_var.set(s.get("create_tv_image", True))
        self.make_gamepad_var.set(s.get("create_gamepad_image", True))
        self.include_banner_metadata_var.set(s.get("include_banner_metadata", True))
        self.include_game_title_var.set(s.get("include_game_title", True))
        self.include_release_date_var.set(s.get("include_release_date", True))
        self.include_player_count_var.set(s.get("include_player_count", True))
        saved_crop = s.get("screen_crop", "auto")
        # v1.0 defaulted to Full. In v1.1 Auto behaves exactly like Full for
        # single-screen systems while enabling automatic one-screen selection
        # for Nintendo DS, so it is safe to migrate the old default.
        self.crop_var.set("auto" if saved_crop == "full" else saved_crop)

    def _save_ui_settings(self):
        save_settings({
            "retroarch_path": self.retro_var.get().strip(),
            "dolphin_path": self.dolphin_var.get().strip(),
            "output_root": self.output_var.get().strip(),
            "uwuvci_font_path": self.uwuvci_font_var.get().strip(),
            "warmup_seconds": self.warmup_var.get(),
            "duration_seconds": self.duration_var.get(),
            "interval_seconds": self.interval_var.get(),
            "candidate_count": self.candidates_var.get(),
            "screen_crop": self.crop_var.get(),
            "create_icon_image": self.make_icon_var.get(),
            "create_tv_image": self.make_tv_var.get(),
            "create_gamepad_image": self.make_gamepad_var.get(),
            "include_banner_metadata": self.include_banner_metadata_var.get(),
            "include_game_title": self.include_game_title_var.get(),
            "include_release_date": self.include_release_date_var.get(),
            "include_player_count": self.include_player_count_var.get(),
            "automatic_repository_artwork": bool(self.auto_repository_artwork_var.get()),
            "repository_title_fallback": bool(self.repository_title_fallback_var.get()),
            "repository_uncertain_matches": bool(self.repository_uncertain_matches_var.get()),
            "libretro_artwork_enabled": bool(self.libretro_artwork_var.get()),
            "gamecube_artwork_enabled": bool(self.gamecube_artwork_var.get()),
            "activity_log_dir": str(self.activity_log_dir) if self.activity_log_dir else "",
            "activity_logging_enabled": bool(self.activity_logging_enabled_var.get()),
        })

    def _select_all_outputs(self):
        self.make_icon_var.set(True)
        self.make_tv_var.set(True)
        self.make_gamepad_var.set(True)

    def _select_no_outputs(self):
        self.make_icon_var.set(False)
        self.make_tv_var.set(False)
        self.make_gamepad_var.set(False)

    @staticmethod
    def _repository_icon_artwork(job: GameJob) -> RepositoryArtwork | None:
        return job.repository_icon_artwork or job.repository_artwork

    @staticmethod
    def _repository_tv_gamepad_artwork(job: GameJob) -> RepositoryArtwork | None:
        return job.repository_tv_gamepad_artwork or job.repository_artwork

    @classmethod
    def _job_has_repository_artwork(cls, job: GameJob) -> bool:
        return bool(cls._repository_icon_artwork(job) or cls._repository_tv_gamepad_artwork(job))

    @staticmethod
    def _job_has_renderable_source(job: GameJob) -> bool:
        if job.selected is not None and Path(job.selected.path).is_file():
            return True
        artworks = (
            App._repository_icon_artwork(job),
            App._repository_tv_gamepad_artwork(job),
        )
        return any(
            path is not None and Path(path).is_file()
            for artwork in artworks
            if artwork is not None
            for path in (
                artwork.source_image_path,
                artwork.icon_path,
                artwork.tv_path,
                artwork.gamepad_path,
            )
        )

    @staticmethod
    def _recommended_screen_crop(job: GameJob) -> str | None:
        return "full" if job.image_imported or (job.selected and job.selected.screen_label) else None

    def _apply_banner_metadata_setting(self):
        self._save_ui_settings()
        indices = self.selected_job_indices()
        if len(indices) != 1:
            return
        job = self.jobs[indices[0]]
        if not self._job_has_renderable_source(job):
            self._update_preview()
            return
        try:
            job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
            parts = []
            if self.include_banner_metadata_var.get():
                if self.include_game_title_var.get():
                    parts.append("title")
                if self.include_release_date_var.get():
                    parts.append("release date")
                if self.include_player_count_var.get():
                    parts.append("player count")
            suffix = ", ".join(parts) if parts else "no metadata"
            job.status = f"Complete - banner text updated ({suffix})"
            self.log(f"Updated banner text setting for {job.info.display_name or job.info.path.stem}: {suffix}")
        except Exception as exc:
            job.status = f"Error - {exc}"
            self.log(f"ERROR updating banner text setting for {job.info.display_name}: {exc}")
        self._refresh_tree()
        self.tree.selection_set(str(indices[0]))
        self._update_preview()

    def edit_screenshot_placement(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one game to adjust its screenshot placement and size.")
            return
        job = self.jobs[indices[0]]
        dialog = ScreenshotPlacementDialog(self, job.info)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        x, y, w, h = dialog.result
        job.info.shot_x = x
        job.info.shot_y = y
        job.info.shot_width = w
        job.info.shot_height = h
        if self._job_has_renderable_source(job):
            try:
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - placement updated"
                self.log(f"Updated screenshot placement for {job.info.display_name}: X={x}, Y={y}, W={w}, H={h}")
            except Exception as exc:
                self.log(f"Could not rebuild after changing screenshot placement: {exc}")
        self._refresh_tree()
        self._on_tree_selection_changed()

    def _crop_state_for_info(self, info: RomInfo) -> dict[str, object]:
        return {
            "source_area_selection": getattr(
                info,
                "source_area_selection",
                getattr(info, "nds_screen_selection", "auto"),
            ),
            "icon_x": getattr(info, "icon_focus_x", 50),
            "icon_y": getattr(info, "icon_focus_y", 50),
            "icon_zoom": getattr(info, "icon_zoom", 100),
            "icon_fill_mode": getattr(info, "icon_fill_mode", "Black"),
            "icon_fill_color": getattr(info, "icon_fill_color", "#000000"),
            "tv_x": getattr(info, "tv_focus_x", 50),
            "tv_y": getattr(info, "tv_focus_y", 50),
            "tv_zoom": getattr(info, "tv_zoom", 100),
            "tv_fill_mode": getattr(info, "tv_fill_mode", "Black"),
            "tv_fill_color": getattr(info, "tv_fill_color", "#000000"),
            "gamepad_x": getattr(info, "gamepad_focus_x", 50),
            "gamepad_y": getattr(info, "gamepad_focus_y", 50),
            "gamepad_zoom": getattr(info, "gamepad_zoom", 100),
            "gamepad_fill_mode": getattr(info, "gamepad_fill_mode", "Black"),
            "gamepad_fill_color": getattr(info, "gamepad_fill_color", "#000000"),
        }

    def _set_crop_focus_values(self, job: GameJob, values, *, rebuild: bool = True):
        if isinstance(values, tuple):
            values = {
                "icon_x": values[0], "icon_y": values[1], "icon_zoom": values[2],
                "tv_x": values[3], "tv_y": values[4], "tv_zoom": values[5],
                "gamepad_x": values[6], "gamepad_y": values[7], "gamepad_zoom": values[8],
                "icon_fill_mode": getattr(job.info, "icon_fill_mode", "Black"),
                "icon_fill_color": getattr(job.info, "icon_fill_color", "#000000"),
                "tv_fill_mode": getattr(job.info, "tv_fill_mode", "Black"),
                "tv_fill_color": getattr(job.info, "tv_fill_color", "#000000"),
                "gamepad_fill_mode": getattr(job.info, "gamepad_fill_mode", "Black"),
                "gamepad_fill_color": getattr(job.info, "gamepad_fill_color", "#000000"),
                "source_area_selection": getattr(
                    job.info,
                    "source_area_selection",
                    getattr(job.info, "nds_screen_selection", "auto"),
                ),
            }
        source_area = str(values.get("source_area_selection", "auto")).strip().lower()
        job.info.source_area_selection = (
            source_area if source_area in {"auto", "full", "top", "bottom", "left", "right"} else "auto"
        )
        job.info.icon_focus_x = int(values["icon_x"]); job.info.icon_focus_y = int(values["icon_y"]); job.info.icon_zoom = int(values["icon_zoom"])
        job.info.icon_fill_mode = str(values.get("icon_fill_mode", "Black"))
        job.info.icon_fill_color = str(values.get("icon_fill_color", "#000000"))
        job.info.tv_focus_x = int(values["tv_x"]); job.info.tv_focus_y = int(values["tv_y"]); job.info.tv_zoom = int(values["tv_zoom"])
        job.info.tv_fill_mode = str(values.get("tv_fill_mode", "Black"))
        job.info.tv_fill_color = str(values.get("tv_fill_color", "#000000"))
        job.info.gamepad_focus_x = int(values["gamepad_x"]); job.info.gamepad_focus_y = int(values["gamepad_y"]); job.info.gamepad_zoom = int(values["gamepad_zoom"])
        job.info.gamepad_fill_mode = str(values.get("gamepad_fill_mode", "Black"))
        job.info.gamepad_fill_color = str(values.get("gamepad_fill_color", "#000000"))
        if rebuild and self._job_has_renderable_source(job):
            job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
            self._update_preview()

    def edit_image_crop_position(self):
        owner = self
        if self.preview_detached and self.preview_detached_window is not None:
            try:
                if self.preview_detached_window.winfo_exists():
                    owner = self.preview_detached_window
            except Exception:
                owner = self
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one game to adjust its image crop and zoom.", parent=owner)
            return
        idx = indices[0]
        job = self.jobs[idx]
        original = self._crop_state_for_info(job.info).copy()

        def live_preview(values):
            try:
                self._set_crop_focus_values(job, values, rebuild=True)
                job.status = "Previewing crop and zoom"
                self._refresh_tree()
                self.tree.selection_set(str(idx))
            except Exception as exc:
                self.log(f"Could not refresh crop/zoom preview: {exc}")

        dialog = ImageCropPositionDialog(owner, job.info, on_preview=live_preview)
        self.wait_window(dialog)
        if dialog.result is None:
            try:
                self._set_crop_focus_values(job, original, rebuild=True)
                job.status = "Complete - crop and zoom unchanged"
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"Could not restore original image crop/zoom settings: {exc}")
        else:
            values = dialog.result
            try:
                self._set_crop_focus_values(job, values, rebuild=True)
                job.status = "Complete - crop and zoom updated"
                self.log(
                    f"Updated crop/zoom for {job.info.display_name}: "
                    f"Icon {job.info.icon_focus_x}/{job.info.icon_focus_y} @ {job.info.icon_zoom}% [{getattr(job.info, 'icon_fill_mode', 'Black')}], "
                    f"TV {job.info.tv_focus_x}/{job.info.tv_focus_y} @ {job.info.tv_zoom}% [{getattr(job.info, 'tv_fill_mode', 'Black')}], "
                    f"Gamepad {job.info.gamepad_focus_x}/{job.info.gamepad_focus_y} @ {job.info.gamepad_zoom}% [{getattr(job.info, 'gamepad_fill_mode', 'Black')}]"
                )
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"Could not rebuild after changing image crop/zoom: {exc}")
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._on_tree_selection_changed()

    def _browse_retro(self):
        p = filedialog.askopenfilename(title="Select RetroArch", filetypes=[("RetroArch", "retroarch.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
        if p:
            self.retro_var.set(p)

    def _browse_dolphin(self):
        p = filedialog.askopenfilename(title="Select Dolphin", filetypes=[("Dolphin", "Dolphin.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
        if p:
            self.dolphin_var.set(p)

    def _browse_output(self):
        p = filedialog.askdirectory(title="Select export folder")
        if p:
            self.output_var.set(p)

    def open_configuration_folder(self):
        path = config_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_NAME, str(path))

    def change_configuration_folder(self):
        selected = filedialog.askdirectory(
            title="Select configuration folder",
            initialdir=str(config_dir()),
            mustexist=False,
        )
        if not selected:
            return
        target = Path(selected)
        if target.resolve() == config_dir().resolve():
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Change the configuration folder and copy the current settings, metadata caches, and downloaded databases to the selected folder?\n\n"
            f"New folder:\n{target}",
        ):
            return
        try:
            self._save_ui_settings()
            active = set_config_dir(target, migrate=True)
            self._save_ui_settings()
            messagebox.showinfo(APP_NAME, f"The configuration folder is now:\n\n{active}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"The configuration folder could not be changed.\n\n{exc}")

    def use_default_configuration_folder(self):
        target = default_config_dir()
        if target.resolve() == config_dir().resolve():
            messagebox.showinfo(APP_NAME, f"The default configuration folder is already active.\n\n{target}")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Return to the default configuration folder and copy the current settings and caches into it?\n\n"
            f"Default folder:\n{target}",
        ):
            return
        try:
            self._save_ui_settings()
            active = set_config_dir(target, migrate=True)
            self._save_ui_settings()
            messagebox.showinfo(APP_NAME, f"The default configuration folder is now active:\n\n{active}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"The default configuration folder could not be restored.\n\n{exc}")

    def open_activity_logging_settings(self):
        existing = self._activity_logging_settings_window
        try:
            if existing is not None and existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass
        self._activity_logging_settings_window = ActivityLoggingSettingsDialog(self)

    def open_activity_log_folder(self):
        if self.activity_log_dir is None:
            self.change_activity_log_folder()
            return
        try:
            self.activity_log_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(self.activity_log_dir)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_NAME, str(self.activity_log_dir))

    def change_activity_log_folder(self):
        initial = self.activity_log_dir or config_dir()
        selected = filedialog.askdirectory(
            title="Select activity log output folder",
            initialdir=str(initial),
            mustexist=False,
        )
        if not selected:
            return
        try:
            target = Path(selected).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            self.activity_log_dir = target
            self._session_log_path = None
            self._activity_file_error_reported = False
            self.activity_logging_enabled_var.set(True)
            self._save_ui_settings()
            self._update_activity_logging_status()
            self.log(f"Live activity file logging enabled: {target}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"The activity log output folder could not be configured.\n\n{exc}")

    def toggle_activity_file_logging(self):
        if self.activity_logging_enabled_var.get() and self.activity_log_dir is None:
            self.change_activity_log_folder()
            if self.activity_log_dir is None:
                self.activity_logging_enabled_var.set(False)
                self._update_activity_logging_status()
                return
            return
        self._activity_file_error_reported = False
        enabled = bool(self.activity_logging_enabled_var.get())
        self._save_ui_settings()
        self._update_activity_logging_status()
        if enabled:
            self.log(f"Live activity file logging enabled: {self.activity_log_dir}")
        else:
            self.log("Live activity file logging disabled.")

    def _update_activity_logging_status(self):
        label = self.activity_logging_status_label
        try:
            enabled = bool(self.activity_logging_enabled_var.get())
        except Exception:
            enabled = False
        if enabled and self.activity_log_dir is not None:
            status = f"Live file logging: On    Folder: {self.activity_log_dir}"
        elif self.activity_log_dir is not None:
            status = f"Live file logging: Off    Configured folder: {self.activity_log_dir}"
        else:
            status = "Live file logging: Off    No output folder configured"
        if label is not None:
            try:
                label.config(text=status)
            except Exception:
                pass

    def _write_activity_log_line(self, line: str) -> str | None:
        try:
            enabled = bool(self.activity_logging_enabled_var.get())
        except Exception:
            enabled = False
        directory = self.activity_log_dir
        if not enabled or directory is None:
            return None
        try:
            with self._activity_log_lock:
                directory.mkdir(parents=True, exist_ok=True)
                if self._session_log_path is None:
                    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                    self._session_log_path = directory / f"Wii_U_VC_Image_Builder_{stamp}.log"
                is_new = not self._session_log_path.exists()
                with self._session_log_path.open("a", encoding="utf-8") as handle:
                    if is_new:
                        started = datetime.now().astimezone().isoformat(timespec="seconds")
                        handle.write(f"{APP_NAME} v{APP_VERSION} Activity Log\n")
                        handle.write(f"Session started: {started}\n\n")
                    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                    handle.write(f"[{timestamp}] {line}\n")
                    handle.flush()
            return None
        except Exception as exc:
            return str(exc)

    def _browse_uwuvci_font(self):
        p = filedialog.askopenfilename(
            title="Select custom font",
            filetypes=[("Font files", "*.otf *.ttf"), ("OpenType font", "*.otf"), ("TrueType font", "*.ttf"), ("All files", "*.*")],
        )
        if p:
            self.uwuvci_font_var.set(p)
            self._save_ui_settings()
            self.log(f"Artwork font selected: {p}")

    def use_default_artwork_font(self):
        self.uwuvci_font_var.set("")
        self._save_ui_settings()
        self.log("Artwork font reset to automatic/default.")
        messagebox.showinfo(
            APP_NAME,
            "Artwork text will use the automatic/default font.",
            parent=self,
        )

    def show_active_artwork_font(self):
        configured = self.uwuvci_font_var.get().strip()
        if not configured:
            detail = "Artwork text is using the automatic/default font."
        elif Path(configured).is_file():
            detail = f"Custom artwork font:\n\n{configured}"
        else:
            detail = (
                "The configured custom font was not found. Artwork text will use the "
                f"automatic/default font.\n\nConfigured path:\n{configured}"
            )
        messagebox.showinfo(APP_NAME, detail, parent=self)

    def _auto_detect_uwuvci_font(self) -> str:
        candidates = []
        roots = [Path.cwd(), Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[2]]
        for root in roots:
            candidates.extend([
                root / "bin" / "Tools" / "font.otf",
                root / "UWUVCI" / "bin" / "Tools" / "font.otf",
                root / "UWUVCI AIO WPF" / "bin" / "Tools" / "font.otf",
            ])
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                pass
        return ""


    def _supported_patterns(self):
        patterns = " ".join(f"*{e}" for e in sorted(ROM_EXTENSIONS))
        return [("Supported ROMs", patterns), ("All files", "*.*")]

    @staticmethod
    def _image_patterns():
        return [("Image files", "*.png *.jpg *.jpeg *.bmp *.webp *.tga"), ("All files", "*.*")]

    def add_roms(self):
        paths = filedialog.askopenfilenames(title="Add ROMs", filetypes=self._supported_patterns())
        self._add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Add ROM folder")
        if not folder:
            return
        paths = [str(p) for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in ROM_EXTENSIONS]
        self._add_paths(paths)

    def _toggle_automatic_repository_artwork(self):
        self._save_ui_settings()
        enabled = bool(self.auto_repository_artwork_var.get())
        self.log(f"Automatic artwork and boot-sound source search {'enabled' if enabled else 'disabled'}.")
        if enabled:
            indices = [
                i for i, job in enumerate(self.jobs)
                if (
                    job.source_kind == "ROM"
                    and (not self._job_has_repository_artwork(job) or job.boot_sound is None)
                )
            ]
            if indices:
                self._start_repository_artwork_lookup(indices, automatic=True)

    def open_repository_artwork_cache(self):
        path = config_dir() / "repository_artwork_cache"
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_NAME, str(path), parent=self)

    def download_repository_artwork_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game to download repository artwork.", parent=self)
            return
        idx, job = selected
        if job.source_kind != "ROM":
            messagebox.showinfo(APP_NAME, "Repository artwork lookup requires a loaded ROM.", parent=self)
            return
        if not system_has_repository(job.info.system):
            messagebox.showinfo(
                APP_NAME,
                f"No artwork source is configured for {job.info.system}.",
                parent=self,
            )
            return
        title_candidates = self._repository_title_candidates(job)
        if (
            not product_code_candidates(job.info.product_code)
            and (not self.repository_title_fallback_var.get() or not title_candidates)
        ):
            messagebox.showinfo(
                APP_NAME,
                "This game has neither a usable product code nor an enabled title fallback. "
                "Use Edit Game Details to add a title or product code, or enable title and metadata fallbacks in Settings.",
                parent=self,
            )
            return
        if (self.worker and self.worker.is_alive()) or (self.metadata_worker and self.metadata_worker.is_alive()):
            messagebox.showinfo(
                APP_NAME,
                "Wait for the current capture or metadata operation to finish before downloading repository artwork.",
                parent=self,
            )
            return
        self._start_repository_artwork_lookup([idx], automatic=False)

    def clear_repository_artwork_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        idx, job = selected
        if not self._job_has_repository_artwork(job):
            messagebox.showinfo(APP_NAME, "The selected game is already using generated artwork.", parent=self)
            return

        job.repository_artwork = None
        job.repository_icon_artwork = None
        job.repository_tv_gamepad_artwork = None
        job.prefer_user_source = True
        try:
            options = self._export_options(
                screen_crop="full" if job.image_imported or (job.selected and job.selected.screen_label) else None
            )
            if job.selected is not None:
                job.output_dir = self._export_job(job, options=options)
            else:
                job.output_dir = export_metadata_only(job.info, options, None)
                for filename in ("Icon Image.png", "TV Image.png", "Gamepad Image.png"):
                    (job.output_dir / filename).unlink(missing_ok=True)
            job.status = "Ready - using generated artwork"
            self.log(f"Repository artwork cleared for {job.info.display_name or job.info.path.stem}; generated artwork is active.")
        except Exception as exc:
            job.status = f"Error - {exc}"
            self.log(f"ERROR restoring generated artwork for {job.info.display_name}: {exc}")
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._update_preview()

    def _start_repository_artwork_lookup(self, indices: list[int], automatic: bool, sound_only: bool = False):
        valid_indices = [
            i for i in indices
            if 0 <= i < len(self.jobs) and self.jobs[i].source_kind == "ROM"
        ]
        if automatic:
            valid_indices = [
                i for i in valid_indices
                if not self._job_has_repository_artwork(self.jobs[i]) or self.jobs[i].boot_sound is None
            ]
        if not valid_indices:
            return

        if self.artwork_worker and self.artwork_worker.is_alive():
            if automatic:
                self.after(350, lambda: self._start_repository_artwork_lookup(valid_indices, automatic=True, sound_only=sound_only))
            else:
                messagebox.showinfo(APP_NAME, "A repository artwork lookup is already running.", parent=self)
            return

        if self.worker and self.worker.is_alive():
            if automatic:
                self.after(500, lambda: self._start_repository_artwork_lookup(valid_indices, automatic=True, sound_only=sound_only))
            else:
                messagebox.showinfo(APP_NAME, "Wait for the current capture to finish first.", parent=self)
            return

        if self.metadata_worker and self.metadata_worker.is_alive():
            if automatic:
                self.after(350, lambda: self._start_repository_artwork_lookup(valid_indices, automatic=True, sound_only=sound_only))
            else:
                messagebox.showinfo(APP_NAME, "Wait for the current metadata lookup to finish first.", parent=self)
            return

        options = self._export_options()
        jobs = [(self.jobs[i], i, self.jobs[i].status) for i in valid_indices]
        search_settings = {
            "allow_title_fallback": bool(self.repository_title_fallback_var.get()),
            "include_uncertain_matches": bool(self.repository_uncertain_matches_var.get()),
            "search_libretro": bool(self.libretro_artwork_var.get()),
            "search_gamecube_collection": bool(self.gamecube_artwork_var.get()),
            "sound_only": bool(sound_only),
        }
        self.artwork_worker = threading.Thread(
            target=self._repository_artwork_worker,
            args=(jobs, options, automatic, search_settings),
            daemon=True,
        )
        self.artwork_worker.start()

    @staticmethod
    def _repository_title_candidates(job: GameJob) -> list[str]:
        candidates: list[str] = []
        for value in (job.info.display_name, job.info.title, job.info.path.stem):
            title = str(value or "").strip()
            if title and title.casefold() not in {item.casefold() for item in candidates}:
                candidates.append(title)
        return candidates

    def _repository_artwork_worker(
        self,
        jobs,
        options: ExportOptions,
        automatic: bool,
        search_settings: dict[str, bool],
    ):
        cache_root = config_dir() / "repository_artwork_cache"
        for job, original_index, previous_status in jobs:
            title = job.info.display_name or job.info.path.stem
            title_candidates = self._repository_title_candidates(job)
            sound_only = bool(search_settings.get("sound_only"))
            self.events.put(("status", original_index, "Searching boot-sound sources" if sound_only else "Searching artwork and boot-sound sources"))
            self.events.put((
                "log",
                f"Source search: checking {'boot sounds' if sound_only else 'artwork and boot sounds'} for {title} "
                f"[{job.info.product_code or 'no product code'}]...",
            ))

            sound_candidates: list[BootSound] = []
            if system_has_boot_sound_repository(job.info.system):
                try:
                    sound_candidates = find_and_download_boot_sounds(
                        job.info.system,
                        job.info.product_code,
                        cache_root,
                    )
                    if sound_candidates:
                        self.events.put(("boot_sound_candidates", job, sound_candidates, automatic, sound_only))
                except RepositoryArtworkError as exc:
                    self.events.put(("boot_sound_error", job, str(exc), automatic, sound_only, previous_status))
                except Exception as exc:
                    self.events.put(("boot_sound_error", job, f"Unexpected boot-sound lookup error: {exc}", automatic, sound_only, previous_status))
            if sound_only:
                if not sound_candidates:
                    self.events.put(("boot_sound_missing", job, f"No per-game or online {job.info.system} startup sound was found.", automatic, previous_status))
                continue

            if not system_has_repository(job.info.system):
                reason = f"No artwork source is configured for {job.info.system}."
                self.events.put(("repository_art_missing", job, reason, automatic, previous_status))
                continue
            if (
                not product_code_candidates(job.info.product_code)
                and (not search_settings["allow_title_fallback"] or not title_candidates)
            ):
                reason = "No usable product code or enabled title fallback is available for artwork lookup."
                self.events.put(("repository_art_missing", job, reason, automatic, previous_status))
                continue

            try:
                artwork_candidates = find_and_download_artwork_candidates(
                    job.info.system,
                    job.info.product_code,
                    cache_root,
                    title_candidates=title_candidates,
                    region=job.info.region,
                    allow_title_fallback=search_settings["allow_title_fallback"],
                    search_libretro=search_settings["search_libretro"],
                    search_gamecube_collection=search_settings["search_gamecube_collection"],
                    include_uncertain_matches=search_settings["include_uncertain_matches"],
                )
                if not artwork_candidates:
                    reason = (
                        f"No sufficiently confident artwork match was found for {job.info.system}. "
                        "Exact ID, metadata title, ROM title, and filename evidence were checked according to the current settings."
                    )
                    self.events.put(("repository_art_missing", job, reason, automatic, previous_status))
                else:
                    self.events.put(("repository_art_candidates", job, artwork_candidates, options, automatic))
            except RepositoryArtworkError as exc:
                self.events.put(("repository_art_error", job, str(exc), automatic, previous_status))
            except Exception as exc:
                self.events.put(("repository_art_error", job, f"Unexpected artwork lookup error: {exc}", automatic, previous_status))
        self.events.put(("repository_art_done",))

    def _receive_repository_artwork_candidates(
        self,
        job: GameJob,
        candidates: list[RepositoryArtwork],
        options: ExportOptions,
        automatic: bool,
    ):
        if job not in self.jobs or not candidates:
            return
        job.repository_artwork_candidates = list(candidates)
        self._update_repository_artwork_review_state()
        trusted_best_match = (
            candidates[0].match_method == "Exact product code"
            or candidates[0].confidence >= 0.86
        )
        if automatic:
            if not trusted_best_match:
                job.status = f"Artwork found - review {len(candidates)} possible match(es)"
                self.log(
                    f"Artwork search retained {len(candidates)} possible match option(s) for "
                    f"{job.info.display_name or job.info.path.stem}. Nothing was applied automatically; "
                    "use Review Artwork Matches to decide."
                )
                self._refresh_tree()
                self._update_repository_artwork_review_state()
                return
            self.log(
                f"Artwork search retained {len(candidates)} match option(s) for "
                f"{job.info.display_name or job.info.path.stem}; the highest-priority match was applied."
            )
            self._accept_repository_artwork(job, candidates[0], options, automatic=True)
            return
        if len(candidates) == 1 and trusted_best_match:
            self._accept_repository_artwork(job, candidates[0], options, automatic=False)
            return
        job.status = f"Artwork found - review {len(candidates)} matches"
        self.log(
            f"Artwork search retained {len(candidates)} match options for "
            f"{job.info.display_name or job.info.path.stem}. Review and choose the preferred source."
        )
        self._refresh_tree()
        self._update_repository_artwork_review_state()
        self._open_repository_artwork_review(job, options)

    def review_repository_artwork_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        _, job = selected
        if not job.repository_artwork_candidates:
            messagebox.showinfo(
                APP_NAME,
                "No retained artwork matches are available for this game. Search artwork sources first.",
                parent=self,
            )
            return
        self._open_repository_artwork_review(job, self._export_options())

    def _open_repository_artwork_review(self, job: GameJob, options: ExportOptions):
        existing = self._repository_artwork_review_window
        try:
            if existing is not None and existing.winfo_exists():
                existing.close()
        except Exception:
            pass
        self._repository_artwork_review_window = RepositoryArtworkReviewWindow(
            self,
            job,
            options,
        )

    def _repository_artwork_review_closed(self, window):
        if self._repository_artwork_review_window is window:
            self._repository_artwork_review_window = None

    def _update_repository_artwork_review_state(self):
        button = self.review_artwork_matches_btn
        status = self.artwork_match_status_label
        if button is None:
            return
        selected = self._selected_single_job()
        if selected is None:
            button.config(text="Review Artwork Matches", state="disabled")
            if status is not None:
                status.config(text="Select one game to review artwork")
            return
        _, job = selected
        count = len(job.repository_artwork_candidates)
        if count:
            noun = "match" if count == 1 else "matches"
            button.config(text=f"Review Artwork Matches ({count})", state="normal")
            if status is not None:
                status.config(text=f"{count} retained artwork {noun}")
        else:
            button.config(text="Review Artwork Matches", state="disabled")
            if status is not None:
                status.config(text="No retained artwork matches")

    def _receive_boot_sound_candidates(
        self,
        job: GameJob,
        candidates: list[BootSound],
        automatic: bool,
        sound_only: bool,
    ):
        if job not in self.jobs or not candidates:
            return
        job.boot_sound_candidates = list(candidates)
        preserve_user_sound = (
            job.boot_sound is not None
            and (
                job.boot_sound.provider_name in {"Manual file", "Stored game sound", "Local console sound"}
                or job.boot_sound.sound_kind.startswith("Built-in")
            )
        )
        if not preserve_user_sound:
            try:
                self._activate_boot_sound(job, candidates[0], log_change=False)
                self.log(
                    f"Boot-sound search retained {len(candidates)} option(s) for "
                    f"{job.info.display_name or job.info.path.stem}. Applied {candidates[0].sound_kind.lower()} "
                    f"from {candidates[0].provider_name}."
                )
            except Exception as exc:
                self.log(f"ERROR applying downloaded boot sound for {job.info.display_name}: {exc}")
        else:
            self.log(
                f"Boot-sound search found {len(candidates)} option(s) for {job.info.display_name or job.info.path.stem}; "
                "the stored per-game, built-in, or local console sound remains active."
            )
        if sound_only:
            job.status = f"Complete - {len(candidates)} boot-sound option(s)"
        if sound_only and not automatic:
            messagebox.showinfo(
                APP_NAME,
                f"Found {len(candidates)} boot-sound option(s).\n\n"
                "The highest-priority option is active. Use the Boot Sound selector to choose another result.",
                parent=self,
            )
        self._refresh_tree()
        self._update_boot_sound_selector()

    def _boot_sound_missing(self, job: GameJob, reason: str, automatic: bool, previous_status: str):
        if job not in self.jobs:
            return
        job.status = previous_status if automatic else "Boot sound not found"
        self.log(f"Boot sound not applied for {job.info.display_name or job.info.path.stem}: {reason}")
        self._refresh_tree()
        if not automatic:
            messagebox.showinfo(APP_NAME, reason, parent=self)

    def _boot_sound_error(
        self,
        job: GameJob,
        error: str,
        automatic: bool,
        sound_only: bool,
        previous_status: str,
    ):
        if job not in self.jobs:
            return
        job.status = previous_status if automatic else "Boot-sound lookup failed"
        self.log(f"Boot-sound lookup failed for {job.info.display_name or job.info.path.stem}: {error}")
        self._refresh_tree()
        if sound_only and not automatic:
            messagebox.showerror(APP_NAME, error, parent=self)

    def _accept_repository_artwork(
        self,
        job: GameJob,
        artwork: RepositoryArtwork,
        options: ExportOptions,
        automatic: bool,
        show_confirmation: bool = True,
        assignment: str = "all",
    ) -> bool:
        if job not in self.jobs:
            return False
        if automatic and job.prefer_user_source:
            self.log(
                f"Automatic artwork was not reapplied for {job.info.display_name or job.info.path.stem} "
                "because a captured or imported screenshot is active."
            )
            return False
        assignment = assignment if assignment in {"all", "icon", "tv_gamepad"} else "all"
        if assignment == "icon" and "Icon" not in artwork.available_outputs:
            if not automatic:
                messagebox.showinfo(
                    APP_NAME,
                    "This match does not contain artwork that can be used for Icon Image.",
                    parent=self,
                )
            return False
        if assignment == "tv_gamepad" and not ({"TV", "Gamepad"} & set(artwork.available_outputs)):
            if not automatic:
                messagebox.showinfo(
                    APP_NAME,
                    "This match does not contain artwork that can be used for TV or Gamepad Image.",
                    parent=self,
                )
            return False
        idx = self.jobs.index(job)
        previous_assignment = (
            job.repository_artwork,
            job.repository_icon_artwork,
            job.repository_tv_gamepad_artwork,
            job.prefer_user_source,
        )
        current_icon = self._repository_icon_artwork(job)
        current_tv_gamepad = self._repository_tv_gamepad_artwork(job)
        if assignment == "all":
            has_icon = "Icon" in artwork.available_outputs
            has_tv_gamepad = bool({"TV", "Gamepad"} & set(artwork.available_outputs))
            if has_icon and has_tv_gamepad:
                job.repository_artwork = artwork
                job.repository_icon_artwork = artwork
                job.repository_tv_gamepad_artwork = artwork
            else:
                job.repository_artwork = None
                job.repository_icon_artwork = artwork if has_icon else current_icon
                job.repository_tv_gamepad_artwork = artwork if has_tv_gamepad else current_tv_gamepad
        elif assignment == "icon":
            job.repository_artwork = None
            job.repository_icon_artwork = artwork
            job.repository_tv_gamepad_artwork = current_tv_gamepad
        else:
            job.repository_artwork = None
            job.repository_icon_artwork = current_icon
            job.repository_tv_gamepad_artwork = artwork
        job.prefer_user_source = False
        try:
            use_crop = "full" if job.image_imported or (job.selected and job.selected.screen_label) else None
            job.output_dir = self._export_job(job, screen_crop=use_crop, options=options)
            outputs = [name for name, enabled in (
                (
                    "Icon Image",
                    assignment in {"all", "icon"}
                    and options.create_icon
                    and "Icon" in artwork.available_outputs,
                ),
                (
                    "TV Image",
                    assignment in {"all", "tv_gamepad"}
                    and options.create_tv
                    and "TV" in artwork.available_outputs,
                ),
                (
                    "Gamepad Image",
                    assignment in {"all", "tv_gamepad"}
                    and options.create_gamepad
                    and "Gamepad" in artwork.available_outputs,
                ),
            ) if enabled]
            assignment_name = {
                "all": "all enabled image outputs",
                "icon": "Icon Image",
                "tv_gamepad": "TV and Gamepad Images",
            }[assignment]
            output_text = ", ".join(outputs) if outputs else f"{assignment_name} assignment saved"
            cache_text = " from cache" if artwork.from_cache else ""
            job.status = f"Complete - artwork assigned to {assignment_name}"
            self.log(
                f"Repository artwork matched {job.info.display_name or job.info.path.stem}: "
                f"{artwork.provider_name}, {artwork.match_method.lower()}"
                f"{f' ({artwork.matched_title})' if artwork.matched_title else ''}{cache_text}. "
                f"Assigned to: {assignment_name}. Applied: {output_text}."
            )
            if not automatic and show_confirmation:
                details = [
                    "Artwork assignment updated.",
                    "",
                    f"Source: {artwork.provider_name}",
                    f"Match: {artwork.match_method}",
                ]
                if artwork.matched_title:
                    details.append(f"Matched title: {artwork.matched_title}")
                if artwork.product_code:
                    details.append(f"Product code: {artwork.product_code}")
                if artwork.source_image_kind:
                    details.append(f"Artwork type: {artwork.source_image_kind}")
                if artwork.confidence < 0.999:
                    details.append(f"Confidence: {artwork.confidence:.0%}")
                details.extend((
                    f"Assigned to: {assignment_name}",
                    f"Applied: {output_text}",
                    "",
                    "Downloaded files are cached in the active configuration folder.",
                ))
                messagebox.showinfo(
                    APP_NAME,
                    "\n".join(details),
                    parent=self,
                )
        except Exception as exc:
            (
                job.repository_artwork,
                job.repository_icon_artwork,
                job.repository_tv_gamepad_artwork,
                job.prefer_user_source,
            ) = previous_assignment
            job.status = f"Error - {exc}"
            self.log(f"ERROR applying repository artwork for {job.info.display_name}: {exc}")
            if not automatic:
                messagebox.showerror(APP_NAME, f"The artwork was downloaded but could not be applied:\n\n{exc}", parent=self)
            self._refresh_tree()
            self._update_repository_artwork_review_state()
            return False
        self._refresh_tree()
        self._update_repository_artwork_review_state()
        if str(idx) in self.tree.selection():
            self._update_preview()
        return True

    def _repository_artwork_missing(
        self,
        job: GameJob,
        reason: str,
        automatic: bool,
        previous_status: str,
    ):
        if job not in self.jobs:
            return
        job.status = previous_status if automatic else "Repository artwork not found"
        self.log(f"Repository artwork not applied for {job.info.display_name or job.info.path.stem}: {reason}")
        self._refresh_tree()
        if not automatic:
            messagebox.showinfo(APP_NAME, reason, parent=self)

    def _repository_artwork_error(
        self,
        job: GameJob,
        error: str,
        automatic: bool,
        previous_status: str,
    ):
        if job not in self.jobs:
            return
        job.status = previous_status if automatic else "Repository artwork lookup failed"
        self.log(f"Repository artwork lookup failed for {job.info.display_name or job.info.path.stem}: {error}")
        self._refresh_tree()
        if not automatic:
            messagebox.showerror(APP_NAME, error, parent=self)

    def _enable_drag_and_drop(self):
        if DND_FILES is None:
            return
        try:
            for widget in (self, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop_files)
            self._drag_drop_enabled = True
            self.rom_list_panel.config(text="ROM List - Drop ROM Files or Folders Here")
            self.log("Drag and drop enabled for supported ROM files and folders.")
        except Exception as exc:
            self._drag_drop_enabled = False
            self.log(f"Drag and drop could not be enabled: {exc}")

    def _split_drop_paths(self, data) -> list[Path]:
        raw_data = str(data or "").strip()
        if not raw_data:
            return []
        try:
            values = self.tk.splitlist(raw_data)
        except Exception:
            values = (raw_data,)
        paths = []
        for value in values:
            text = str(value).strip()
            if text.startswith("{") and text.endswith("}"):
                text = text[1:-1]
            if text:
                paths.append(Path(text))
        return paths

    @staticmethod
    def _collect_dropped_rom_paths(dropped_paths) -> tuple[list[Path], int]:
        rom_paths: list[Path] = []
        ignored_items = 0
        seen = set()
        for raw_path in dropped_paths:
            path = Path(raw_path)
            if path.is_dir():
                try:
                    candidates = sorted(
                        (
                            candidate
                            for candidate in path.rglob("*")
                            if candidate.is_file() and candidate.suffix.lower() in ROM_EXTENSIONS
                        ),
                        key=lambda candidate: str(candidate).lower(),
                    )
                except OSError:
                    candidates = []
                if not candidates:
                    ignored_items += 1
            elif path.is_file() and path.suffix.lower() in ROM_EXTENSIONS:
                candidates = [path]
            else:
                candidates = []
                ignored_items += 1

            for candidate in candidates:
                try:
                    identity = candidate.resolve()
                except OSError:
                    identity = candidate
                if identity in seen:
                    continue
                seen.add(identity)
                rom_paths.append(candidate)
        return rom_paths, ignored_items

    def _on_drop_files(self, event):
        HoverTip.dismiss_active()
        raw_data = str(getattr(event, "data", "") or "")
        now = time.monotonic()
        if raw_data == self._last_drop_data and now - self._last_drop_at < 0.35:
            return "break"
        self._last_drop_data = raw_data
        self._last_drop_at = now

        dropped_paths = self._split_drop_paths(raw_data)
        rom_paths, ignored_items = self._collect_dropped_rom_paths(dropped_paths)
        if not rom_paths:
            self.log("Drag and drop found no supported ROM files.")
            messagebox.showinfo(
                APP_NAME,
                "No supported ROM files were found in the dropped items.",
                parent=self,
            )
            return "break"

        added_count = self._add_paths(rom_paths)
        if ignored_items:
            self.log(f"Ignored {ignored_items} dropped item(s) that contained no supported ROM files.")
        if added_count:
            self.log(f"Drag and drop added {added_count} new ROM(s).")
        else:
            messagebox.showinfo(
                APP_NAME,
                "No new ROMs were added. The dropped files may already be loaded or could not be identified.",
                parent=self,
            )
        return "break"

    def _export_options(self, screen_crop: str | None = None) -> ExportOptions:
        return ExportOptions(
            output_root=Path(self.output_var.get().strip()),
            screen_crop=screen_crop if screen_crop is not None else self.crop_var.get(),
            font_path=Path(self.uwuvci_font_var.get().strip()) if self.uwuvci_font_var.get().strip() else None,
            create_icon=self.make_icon_var.get(),
            create_tv=self.make_tv_var.get(),
            create_gamepad=self.make_gamepad_var.get(),
            include_banner_metadata=self.include_banner_metadata_var.get(),
            include_game_title=self.include_game_title_var.get(),
            include_release_date=self.include_release_date_var.get(),
            include_player_count=self.include_player_count_var.get(),
        )

    def _job_banner_sample(self, job: GameJob) -> FrameSample | None:
        path = None
        if job.tv_gamepad_source == "Custom Image":
            path = job.imported_banner_image_path
        if path and Path(path).exists() and self._validate_banner_image(Path(path)):
            return FrameSample(Path(path), 0.0, score=1.0)
        return None

    @staticmethod
    def _source_area_choice(job: GameJob) -> str:
        choice = str(
            getattr(
                job.info,
                "source_area_selection",
                getattr(job.info, "nds_screen_selection", "auto"),
            )
            or "auto"
        ).strip().lower()
        return choice if choice in {"auto", "full", "top", "bottom", "left", "right"} else "auto"

    @classmethod
    def _screen_crop_for_source(
        cls,
        job: GameJob,
        chosen: FrameSample | None,
        fallback: str,
        *,
        repository_source: bool = False,
    ) -> str:
        choice = cls._source_area_choice(job)
        if choice == "auto":
            return fallback
        # Emulator capture already produces individual NDS-screen candidates.
        # Imported and downloaded images remain unsplit and can be cropped here.
        if chosen and chosen.screen_label and not job.image_imported and not repository_source:
            return "full"
        return choice

    @staticmethod
    def _validate_banner_image(path: Path) -> bool:
        try:
            with Image.open(path) as im:
                rgba = im.convert("RGBA")
                if rgba.width < 32 or rgba.height < 16:
                    return False
                if rgba.getbbox() is None:
                    return False
                rgb = rgba.convert("RGB")
                thumb = rgb.copy()
                thumb.thumbnail((128, 128), Image.Resampling.LANCZOS)
                stat = ImageStat.Stat(thumb)
                extrema = thumb.getextrema()
                dynamic_range = sum(max(0, hi - lo) for lo, hi in extrema)
                variance = sum(stat.var)
                return dynamic_range >= 18 and variance >= 8
        except Exception:
            return False

    def _available_tv_gamepad_sources(self, job: GameJob) -> list[str]:
        sources = ["Selected Screenshot"]
        if job.imported_banner_image_path and self._validate_banner_image(Path(job.imported_banner_image_path)):
            sources.append("Custom Image")
        return sources

    def _export_job(
        self,
        job: GameJob,
        sample: FrameSample | None = None,
        candidates: list[FrameSample] | None = None,
        screen_crop: str | None = None,
        options: ExportOptions | None = None,
    ):
        use_options = options or self._export_options(screen_crop)
        if options is not None and screen_crop is not None and options.screen_crop != screen_crop:
            use_options = replace(options, screen_crop=screen_crop)
        icon_artwork = self._repository_icon_artwork(job)
        tv_gamepad_artwork = self._repository_tv_gamepad_artwork(job)
        shared_source_artwork = job.repository_artwork.source_image_path if job.repository_artwork else None
        base_is_repository_source = False
        base_is_role_fallback = False
        if shared_source_artwork and Path(shared_source_artwork).is_file():
            chosen = FrameSample(
                Path(shared_source_artwork),
                0.0,
                score=1.0,
            )
            base_is_repository_source = True
            use_options = replace(
                use_options,
                screen_crop=self._screen_crop_for_source(
                    job,
                    chosen,
                    "full",
                    repository_source=True,
                ),
            )
            use_candidates = [chosen]
            # Use the same cropped source for every generated output. Reopening
            # the raw source as a banner would bypass NDS screen selection for
            # the TV and Gamepad images.
            banner_sample = None
        else:
            chosen = sample or job.selected
            use_candidates = candidates or job.candidates or ([chosen] if chosen else [])
            if chosen is None:
                fallback_artwork = next(
                    (
                        artwork
                        for artwork in (tv_gamepad_artwork, icon_artwork)
                        if artwork is not None
                        and artwork.source_image_path is not None
                        and Path(artwork.source_image_path).is_file()
                    ),
                    None,
                )
                if fallback_artwork is not None:
                    chosen = FrameSample(Path(fallback_artwork.source_image_path), 0.0, score=1.0)
                    use_candidates = [chosen]
                    base_is_repository_source = True
                    base_is_role_fallback = True
            choice = self._source_area_choice(job)
            if (
                job.info.system == "Nintendo DS"
                and choice in {"top", "bottom"}
                and chosen is not None
                and chosen.screen_label
            ):
                matching = next(
                    (
                        candidate
                        for candidate in use_candidates
                        if str(candidate.screen_label or "").strip().lower().startswith(choice)
                    ),
                    None,
                )
                if matching is not None:
                    chosen = matching
            use_options = replace(
                use_options,
                screen_crop=self._screen_crop_for_source(
                    job,
                    chosen,
                    use_options.screen_crop,
                    repository_source=base_is_repository_source,
                ),
            )
            banner_sample = None if base_is_repository_source else self._job_banner_sample(job)
        render_options = use_options
        if base_is_role_fallback:
            render_options = replace(
                use_options,
                create_icon=use_options.create_icon and icon_artwork is not None,
                create_tv=use_options.create_tv and tv_gamepad_artwork is not None,
                create_gamepad=use_options.create_gamepad and tv_gamepad_artwork is not None,
            )
        if chosen is None:
            output = export_metadata_only(job.info, use_options, None)
        else:
            output = export_selected(
                job.info,
                chosen,
                use_candidates,
                render_options,
                banner_sample=banner_sample,
            )
        self._apply_repository_art_outputs(job, output, use_options)
        self._apply_boot_sound_output(job, output)
        return output

    @staticmethod
    def _write_source_notes(job: GameJob, output: Path) -> None:
        icon_artwork = App._repository_icon_artwork(job)
        tv_gamepad_artwork = App._repository_tv_gamepad_artwork(job)
        metadata_path = output / "metadata.txt"
        if not metadata_path.exists():
            return
        try:
            text = metadata_path.read_text(encoding="utf-8")
            for marker in (
                "Repository Artwork\n------------------\n",
                "Boot Sound\n----------\n",
            ):
                if marker in text:
                    text = text.split(marker, 1)[0].rstrip()
            notes: list[str] = []
            artwork_assignments: list[tuple[str, RepositoryArtwork]] = []
            if icon_artwork is not None and icon_artwork == tv_gamepad_artwork:
                artwork_assignments.append(("Icon Image, TV Image, and Gamepad Image", icon_artwork))
            else:
                if icon_artwork is not None:
                    artwork_assignments.append(("Icon Image", icon_artwork))
                if tv_gamepad_artwork is not None:
                    artwork_assignments.append(("TV Image and Gamepad Image", tv_gamepad_artwork))
            if artwork_assignments:
                assignment_text: list[str] = ["Repository Artwork\n------------------"]
                for assigned_outputs, artwork in artwork_assignments:
                    assignment_text.extend((
                        f"\nAssigned Outputs: {assigned_outputs}",
                        f"Source: {artwork.provider_name}",
                        f"Repository: {artwork.provider_url}",
                        f"Match Method: {artwork.match_method}",
                        f"Matched Title: {artwork.matched_title or 'Not applicable'}",
                        f"Source Image Type: {artwork.source_image_kind or 'Finished Wii U artwork'}",
                        f"Confidence: {artwork.confidence:.0%}",
                        f"Product Code: {artwork.product_code or 'Not available'}",
                    ))
                assignment_text.append(
                    "\nNotice: Community-hosted artwork was downloaded on demand. Review the source repository terms before redistribution."
                )
                notes.append("\n".join(assignment_text) + "\n")
            sound = job.boot_sound
            if sound is not None:
                repository_line = f"Repository: {sound.provider_url}\n" if sound.provider_url else ""
                notes.append(
                    "Boot Sound\n----------\n"
                    f"Source: {sound.provider_name}\n"
                    f"Type: {sound.sound_kind}\n"
                    f"Selected File: {sound.path.name}\n"
                    "Output File: bootSound.wav\n"
                    "Conversion: Automatically converted to WAV when selected or exported\n"
                    f"{repository_line}"
                    "Notice: Confirm that you have permission to redistribute built-in, downloaded, or manually selected audio.\n"
                )
            suffix = "" if not notes else "\n\n" + "\n\n".join(notes)
            metadata_path.write_text(text.rstrip() + suffix + "\n", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _write_repository_artwork_note(job: GameJob, output: Path) -> None:
        App._write_source_notes(job, output)

    @staticmethod
    def _write_boot_sound_note(job: GameJob, output: Path) -> None:
        App._write_source_notes(job, output)

    @staticmethod
    def _remove_boot_sound_outputs(output: Path, keep: Path | None = None) -> None:
        for suffix in sorted(BOOT_SOUND_EXTENSIONS):
            candidate = output / f"bootSound{suffix}"
            if keep is not None and candidate == keep:
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

    def _apply_boot_sound_output(self, job: GameJob, output: Path) -> Path | None:
        sound = job.boot_sound
        if sound is None:
            self._write_boot_sound_note(job, output)
            return None
        source = Path(sound.path)
        if not source.is_file() or source.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            raise ValueError("The selected boot-sound file is missing or unsupported.")
        output.mkdir(parents=True, exist_ok=True)
        destination = output / "bootSound.wav"
        try:
            convert_boot_sound_to_wav(source, destination)
        except BootSoundConversionError:
            raise
        except Exception as exc:
            raise BootSoundConversionError(f"The boot sound could not be converted to WAV: {exc}") from exc
        self._remove_boot_sound_outputs(output, keep=destination)
        self._write_boot_sound_note(job, output)
        return destination

    def _apply_repository_art_outputs(self, job: GameJob, output: Path, options: ExportOptions) -> list[Path]:
        icon_artwork = self._repository_icon_artwork(job)
        tv_gamepad_artwork = self._repository_tv_gamepad_artwork(job)
        if not icon_artwork and not tv_gamepad_artwork:
            return []
        for filename, enabled in (
            ("Icon Image.png", options.create_icon),
            ("TV Image.png", options.create_tv),
            ("Gamepad Image.png", options.create_gamepad),
        ):
            if not enabled:
                (output / filename).unlink(missing_ok=True)

        written: list[Path] = []
        if icon_artwork is not None and options.create_icon:
            if icon_artwork.source_image_path and Path(icon_artwork.source_image_path).is_file():
                source = Path(icon_artwork.source_image_path)
                source_options = replace(
                    options,
                    screen_crop=self._screen_crop_for_source(
                        job,
                        FrameSample(source, 0.0, score=1.0),
                        options.screen_crop,
                        repository_source=True,
                    ),
                )
                written.append(render_icon_from_source(
                    source,
                    job.info,
                    source_options,
                    output / "Icon Image.png",
                ))
            else:
                written.extend(apply_repository_artwork(
                    icon_artwork,
                    output,
                    create_icon=True,
                    create_tv=False,
                    create_gamepad=False,
                    preserve_disabled=True,
                ))

        if tv_gamepad_artwork is not None and (options.create_tv or options.create_gamepad):
            if tv_gamepad_artwork.source_image_path and Path(tv_gamepad_artwork.source_image_path).is_file():
                source = Path(tv_gamepad_artwork.source_image_path)
                source_options = replace(
                    options,
                    screen_crop=self._screen_crop_for_source(
                        job,
                        FrameSample(source, 0.0, score=1.0),
                        options.screen_crop,
                        repository_source=True,
                    ),
                )
                written.extend(render_banners_from_source(
                    source,
                    job.info,
                    source_options,
                    output,
                    create_tv=options.create_tv,
                    create_gamepad=options.create_gamepad,
                ))
            else:
                written.extend(apply_repository_artwork(
                    tv_gamepad_artwork,
                    output,
                    create_icon=False,
                    create_tv=options.create_tv,
                    create_gamepad=options.create_gamepad,
                    preserve_disabled=True,
                ))
        self._write_repository_artwork_note(job, output)
        return written

    def _selected_single_job(self, require_wii: bool = False) -> tuple[int, GameJob] | None:
        indices = self.selected_job_indices()
        if len(indices) != 1:
            return None
        idx = indices[0]
        if not (0 <= idx < len(self.jobs)):
            return None
        job = self.jobs[idx]
        if require_wii and job.info.system not in {"Wii", "WiiWare"}:
            return None
        return idx, job

    def _write_metadata_now(self, job: GameJob) -> None:
        try:
            previous = job.output_dir
            if self._job_has_renderable_source(job):
                job.output_dir = self._export_job(job)
            else:
                job.output_dir = export_metadata_only(job.info, self._export_options(), None)
                self._apply_boot_sound_output(job, Path(job.output_dir))
            if previous and previous != job.output_dir:
                try:
                    files = list(previous.iterdir()) if previous.exists() else []
                    if files and all(f.name == "metadata.txt" for f in files):
                        for f in files:
                            f.unlink(missing_ok=True)
                        previous.rmdir()
                except OSError:
                    pass
        except Exception as exc:
            self.log(f"Could not write metadata.txt for {job.info.path.name}: {exc}")

    def _add_paths(self, paths):
        existing = {j.info.path.resolve() for j in self.jobs}
        added_indices: list[int] = []
        for raw in paths:
            p = Path(raw)
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp in existing:
                continue
            info = detect_rom(p)
            if not info:
                self.log(f"Skipped unsupported or ambiguous file: {p.name}")
                continue
            info.icon_template = default_icon_template_for_system(info.system)
            job = GameJob(info=info, source_kind="ROM", status="Metadata queued")
            stored_game_sound = self._stored_game_boot_sound(info)
            if stored_game_sound is not None:
                job.boot_sound = stored_game_sound
                job.boot_sound_candidates.append(stored_game_sound)
            else:
                local_console_sound = self._local_console_boot_sound(info.system)
                if local_console_sound is not None:
                    job.boot_sound = local_console_sound
                    job.boot_sound_candidates.append(local_console_sound)
            self.jobs.append(job)
            idx = len(self.jobs) - 1
            added_indices.append(idx)
            existing.add(rp)
            # Write a useful header-derived metadata file immediately. It will be
            # replaced after Libretro lookup finishes.
            self._write_metadata_now(job)
        self._refresh_tree()
        if added_indices:
            self.log(f"Added {len(added_indices)} ROM(s). Metadata lookup started automatically.")
            self._start_metadata_lookup(added_indices)
        return len(added_indices)

    def import_images(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one loaded ROM first, then import the screenshot you want to use for that game.")
            return
        job = self.jobs[indices[0]]
        if job.source_kind != "ROM":
            messagebox.showinfo(APP_NAME, "Screenshot import requires a loaded ROM so the game identity and metadata come from that ROM.")
            return

        raw = filedialog.askopenfilename(
            title=f"Import title-screen screenshot for {job.info.display_name or job.info.path.stem}",
            filetypes=self._image_patterns(),
        )
        if not raw:
            return
        image_path = Path(raw)
        try:
            with Image.open(image_path) as im:
                im.verify()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open image:\n{image_path}\n\n{exc}")
            return

        sample = FrameSample(image_path, 0.0, score=1.0)
        job.candidates = [sample]
        job.selected = sample
        job.image_imported = True
        job.status = "Building from imported screenshot"
        self._refresh_tree()

        previous_artwork = (
            job.repository_artwork,
            job.repository_icon_artwork,
            job.repository_tv_gamepad_artwork,
        )
        previous_preference = job.prefer_user_source
        job.repository_artwork = None
        job.repository_icon_artwork = None
        job.repository_tv_gamepad_artwork = None
        job.prefer_user_source = True
        try:
            # A manually chosen screenshot is already the intended image. Do not
            # apply emulator/DS split cropping to it.
            job.output_dir = self._export_job(job, sample=sample, candidates=[sample], screen_crop="full")
            job.status = "Complete - imported screenshot"
            self.log(f"Imported screenshot for {job.info.display_name or job.info.path.stem}: {image_path.name}")
            if any(previous_artwork):
                self.log("The imported screenshot replaced downloaded artwork as the active source. The download remains cached.")
            self.log("Emulator capture was bypassed. Existing ROM metadata was used to build the artwork.")
            self.log(f"Exported to: {job.output_dir}")
        except Exception as exc:
            (
                job.repository_artwork,
                job.repository_icon_artwork,
                job.repository_tv_gamepad_artwork,
            ) = previous_artwork
            job.prefer_user_source = previous_preference
            job.status = f"Error - {exc}"
            self.log(f"ERROR building imported screenshot for {job.info.display_name}: {exc}")
        self._refresh_tree()
        self.tree.selection_set(str(indices[0]))
        self._on_tree_selection_changed()

    def import_banner_image(self):
        selected = self._selected_single_job(require_wii=True)
        if not selected:
            messagebox.showinfo(APP_NAME, "Select exactly one Wii or WiiWare game first.")
            return
        idx, job = selected
        raw = filedialog.askopenfilename(
            title=f"Import TV/Gamepad image for {job.info.display_name or job.info.path.stem}",
            filetypes=self._image_patterns(),
        )
        if not raw:
            return
        image_path = Path(raw)
        if not self._validate_banner_image(image_path):
            messagebox.showerror(APP_NAME, f"The selected image could not be used as a TV/Gamepad source:\n{image_path}")
            return
        job.imported_banner_image_path = image_path
        job.tv_gamepad_source = "Custom Image"
        job.banner_image_note = f"Custom TV/Gamepad image: {image_path.name}"
        if self._job_has_renderable_source(job):
            try:
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - custom TV/Gamepad image"
                self.log(f"Using custom TV/Gamepad image for {job.info.display_name or job.info.path.stem}: {image_path.name}")
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"ERROR importing TV/Gamepad image for {job.info.display_name}: {exc}")
        else:
            job.status = "TV/Gamepad image imported - capture or screenshot still needed"
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._on_tree_selection_changed()

    def restore_screenshot_for_tv_gamepad(self):
        selected = self._selected_single_job(require_wii=True)
        if not selected:
            messagebox.showinfo(APP_NAME, "Select exactly one Wii or WiiWare game first.")
            return
        idx, job = selected
        job.tv_gamepad_source = "Selected Screenshot"
        job.banner_image_note = "Selected screenshot"
        if self._job_has_renderable_source(job):
            try:
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - screenshot used for TV/Gamepad"
                self.log(f"Using the selected screenshot for TV/Gamepad artwork for {job.info.display_name or job.info.path.stem}.")
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"ERROR restoring screenshot TV/Gamepad source for {job.info.display_name}: {exc}")
        else:
            job.status = "TV/Gamepad source set to selected screenshot"
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._on_tree_selection_changed()

    def build_selected_images(self):
        indices = self.selected_job_indices() or list(range(len(self.jobs)))
        ready = [
            i for i in indices
            if 0 <= i < len(self.jobs) and self._job_has_renderable_source(self.jobs[i])
        ]
        if not ready:
            messagebox.showinfo(APP_NAME, "Select a game with a capture, imported screenshot, or downloaded artwork source first.")
            return
        built = 0
        for idx in ready:
            job = self.jobs[idx]
            try:
                # Imported screenshots and auto-cropped NDS candidates are already
                # individual screens and should not be cut again.
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - images rebuilt"
                built += 1
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"ERROR rebuilding {job.info.display_name}: {exc}")
        self._refresh_tree()
        self._on_tree_selection_changed()
        self.log(f"Rebuilt artwork for {built} game(s).")

    def _refresh_tree(self):
        selected_indices = set()
        for item in self.tree.selection():
            try:
                selected_indices.add(int(item))
            except Exception:
                pass
        self.tree.delete(*self.tree.get_children())
        for i, job in enumerate(self.jobs):
            source_display = "ROM + Image" if job.image_imported else job.source_kind
            if job.info.system in {"Wii", "WiiWare"} and job.tv_gamepad_source != "Selected Screenshot":
                source_display += " + " + job.tv_gamepad_source
            if self._job_has_repository_artwork(job):
                source_display += " + Repository Art"
            self.tree.insert("", "end", iid=str(i), values=(
                "☑" if job.capture_checked else "☐",
                "✕",
                job.info.path.name, source_display, job.info.display_name or job.info.path.stem, job.info.system,
                job.info.release_year or "-", job.info.players or "-", job.info.product_code or "-", job.status
            ))
        for i in selected_indices:
            if i < len(self.jobs):
                self.tree.selection_add(str(i))
        self._update_checked_action_state()

    def selected_job_indices(self):
        result = []
        for item in self.tree.selection():
            try:
                idx = int(item)
                if 0 <= idx < len(self.jobs):
                    result.append(idx)
            except Exception:
                pass
        return sorted(result)

    def _update_checked_action_state(self):
        selected_roms = [
            idx for idx in self.selected_job_indices()
            if self.jobs[idx].source_kind == "ROM"
        ]
        toggle_button = self.__dict__.get("check_selected_capture_btn")
        if toggle_button is not None:
            all_selected_checked = bool(selected_roms) and all(
                self.jobs[idx].capture_checked for idx in selected_roms
            )
            try:
                toggle_button.config(
                    text="Uncheck Selected" if all_selected_checked else "Check Selected",
                    state="normal" if selected_roms else "disabled",
                )
            except Exception:
                pass

        remove_button = self.__dict__.get("remove_checked_btn")
        if remove_button is None:
            return
        remove_state = "normal" if any(
            job.source_kind == "ROM" and job.capture_checked
            for job in self.jobs
        ) else "disabled"
        try:
            remove_button.config(state=remove_state)
        except Exception:
            pass

    def _tree_remove_checked_shortcut(self, event=None):
        self.remove_checked()
        return "break"

    def _tree_capture_checkbox_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return None
        item = self.tree.identify_row(event.y)
        if not item:
            return "break"
        try:
            idx = int(item)
        except Exception:
            return "break"
        if 0 <= idx < len(self.jobs) and self.jobs[idx].source_kind == "ROM":
            self.jobs[idx].capture_checked = not self.jobs[idx].capture_checked
            self._refresh_tree()
        return "break"

    def _tree_remove_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#2":
            return None
        item = self.tree.identify_row(event.y)
        if not item:
            return "break"
        now = time.monotonic()
        if now - getattr(self, "_last_inline_remove_at", 0.0) < 0.45:
            return "break"
        self._last_inline_remove_at = now
        try:
            idx = int(item)
        except Exception:
            return "break"
        self._remove_job_indices([idx])
        return "break"

    def _tree_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) in {"#1", "#2"}:
            return "break"
        self.review_selected()
        return "break"

    def _job_removal_is_blocked(self) -> bool:
        worker = getattr(self, "worker", None)
        metadata_worker = getattr(self, "metadata_worker", None)
        artwork_worker = self.__dict__.get("artwork_worker")
        capture_active = worker is not None and worker.is_alive()
        metadata_active = metadata_worker is not None and metadata_worker.is_alive()
        artwork_active = artwork_worker is not None and artwork_worker.is_alive()
        if not capture_active and not metadata_active and not artwork_active:
            return False
        messagebox.showinfo(
            APP_NAME,
            "Wait for the current capture, metadata, or repository artwork operation to finish before removing ROMs.",
            parent=self,
        )
        return True

    def _remove_job_indices(self, indices) -> bool:
        valid_indices = sorted({i for i in indices if 0 <= i < len(self.jobs)})
        if not valid_indices or self._job_removal_is_blocked():
            return False

        removed = set(valid_indices)
        selected_jobs = [
            self.jobs[i]
            for i in self.selected_job_indices()
            if i not in removed and 0 <= i < len(self.jobs)
        ]
        removed_jobs = [self.jobs[i] for i in valid_indices]
        current_selection = self.tree.selection()
        if current_selection:
            self.tree.selection_remove(*current_selection)
        for idx in reversed(valid_indices):
            del self.jobs[idx]
        self._refresh_tree()

        restored_indices = []
        for selected_job in selected_jobs:
            restored_index = next(
                (i for i, job in enumerate(self.jobs) if job is selected_job),
                None,
            )
            if restored_index is not None:
                restored_indices.append(restored_index)
                self.tree.selection_add(str(restored_index))
        if not restored_indices and self.jobs:
            next_index = min(valid_indices[0], len(self.jobs) - 1)
            self.tree.selection_set(str(next_index))

        for job in removed_jobs:
            self.log(
                f"Removed from ROM list: {job.info.display_name or job.info.path.name}. "
                "The source ROM was not deleted."
            )
        self._on_tree_selection_changed()
        return True

    def check_selected_for_capture(self):
        selected_roms = [
            idx for idx in self.selected_job_indices()
            if self.jobs[idx].source_kind == "ROM"
        ]
        if not selected_roms:
            return
        new_checked_state = not all(
            self.jobs[idx].capture_checked for idx in selected_roms
        )
        for idx in selected_roms:
            self.jobs[idx].capture_checked = new_checked_state
        self._refresh_tree()

    def check_all_for_capture(self):
        for job in self.jobs:
            if job.source_kind == "ROM":
                job.capture_checked = True
        self._refresh_tree()

    def clear_capture_checks(self):
        for job in self.jobs:
            job.capture_checked = False
        self._refresh_tree()

    def remove_checked(self):
        indices = [
            i for i, job in enumerate(self.jobs)
            if job.source_kind == "ROM" and job.capture_checked
        ]
        self._remove_job_indices(indices)

    def edit_game_details(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select one game to edit its details.")
            return
        job = self.jobs[indices[0]]
        dialog = GameDetailsDialog(self, job.info)
        self.wait_window(dialog)
        if dialog.result:
            title, year, players, product_code = dialog.result
            previous_title = job.info.display_name
            previous_product_code = job.info.product_code
            job.info.display_name = title
            job.info.release_year = year
            job.info.players = players
            job.info.product_code = product_code
            if (
                (previous_product_code != product_code or previous_title != title)
            ):
                job.repository_artwork_candidates = []
                job.boot_sound_candidates = []
            if (
                (previous_product_code != product_code or previous_title != title)
                and self._job_has_repository_artwork(job)
            ):
                job.repository_artwork = None
                job.repository_icon_artwork = None
                job.repository_tv_gamepad_artwork = None
            job.info.metadata_source = "Manual"
            if self._job_has_renderable_source(job):
                try:
                    job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                except Exception as exc:
                    self.log(f"Could not rebuild after editing details: {exc}")
            else:
                self._write_metadata_now(job)
            self._refresh_tree()
            if self.auto_repository_artwork_var.get() and (product_code or title):
                self._start_repository_artwork_lookup([indices[0]], automatic=True)

    def _metadata_settings(self) -> LibretroSettings:
        return LibretroSettings()

    @staticmethod
    def _apply_metadata(info: RomInfo, metadata: GameMetadata) -> None:
        if metadata.title:
            info.display_name = metadata.title
        info.release_year = metadata.release_year or info.release_year
        info.players = metadata.players or info.players
        info.publisher = metadata.publisher or info.publisher
        info.developer = metadata.developer or info.developer
        info.metadata_source = metadata.source or "Libretro Database"
        info.metadata_game_id = metadata.game_id
        info.metadata_rom_id = metadata.rom_id
        info.metadata_sha1 = metadata.sha1

    def _start_metadata_lookup(self, indices: list[int]):
        rom_indices = [i for i in indices if 0 <= i < len(self.jobs) and self.jobs[i].source_kind == "ROM"]
        if not rom_indices:
            return
        # If a lookup is already active, schedule another pass as soon as it ends.
        if (self.metadata_worker and self.metadata_worker.is_alive()) or (self.artwork_worker and self.artwork_worker.is_alive()):
            self.after(300, lambda: self._start_metadata_lookup(rom_indices))
            return
        options = self._export_options()
        self.metadata_worker = threading.Thread(
            target=self._metadata_worker, args=(rom_indices, self._metadata_settings(), options), daemon=True
        )
        self.metadata_worker.start()

    def fetch_metadata_selected(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Wait for the current ROM capture to finish first.")
            return
        indices = self.selected_job_indices() or list(range(len(self.jobs)))
        indices = [i for i in indices if self.jobs[i].source_kind == "ROM"]
        if not indices:
            messagebox.showinfo(APP_NAME, "Select at least one ROM. Imported screenshots do not have ROM hashes for Libretro lookup.")
            return
        self._save_ui_settings()
        self._start_metadata_lookup(indices)

    def _metadata_worker(self, indices: list[int], settings: LibretroSettings, options: ExportOptions):
        for idx in indices:
            if not (0 <= idx < len(self.jobs)):
                continue
            job = self.jobs[idx]
            if job.source_kind != "ROM":
                continue
            self.events.put(("status", idx, "Fetching metadata"))
            self.events.put(("log", f"Metadata: {job.info.path.name} - checking public metadata databases..."))
            try:
                metadata = lookup_libretro(
                    job.info.path,
                    job.info.system,
                    job.info.region,
                    job.info.product_code,
                    settings,
                    use_cache=True,
                    title_hint=job.info.display_name or job.info.title or job.info.path.stem,
                )
                self._apply_metadata(job.info, metadata)
                previous = job.output_dir
                job.output_dir = self._export_job(
                    job,
                    screen_crop=("full" if job.image_imported or (job.selected and job.selected.screen_label) else None),
                    options=options,
                )
                if previous and previous != job.output_dir:
                    try:
                        files = list(previous.iterdir()) if previous.exists() else []
                        if files and all(f.name == "metadata.txt" for f in files):
                            for f in files:
                                f.unlink(missing_ok=True)
                            previous.rmdir()
                    except OSError:
                        pass
                detail = ", ".join(x for x in [metadata.release_year, f"{metadata.players} player(s)" if metadata.players else ""] if x)
                self.events.put(("status", idx, f"Metadata ready{f' - {detail}' if detail else ''}"))
                self.events.put(("metadata_refresh", idx))
                self.events.put(("log", f"Metadata matched: {job.info.display_name}{f' | Released {job.info.release_year}' if job.info.release_year else ''}{f' | Players {job.info.players}' if job.info.players else ''}"))
            except Exception as exc:
                # Keep the header/filename metadata file useful even if Libretro
                # has no record or the network is offline.
                try:
                    job.output_dir = self._export_job(
                        job,
                        screen_crop=("full" if job.image_imported or (job.selected and job.selected.screen_label) else None),
                        options=options,
                    )
                except Exception:
                    pass
                self.events.put(("status", idx, "Metadata unavailable - fallback saved"))
                self.events.put(("log", f"Metadata lookup failed for {job.info.path.name}: {exc}"))
        self.events.put(("metadata_done", list(indices)))

    def update_metadata_database(self):
        if self.metadata_worker and self.metadata_worker.is_alive():
            messagebox.showinfo(APP_NAME, "A metadata operation is already running.")
            return
        systems = sorted({job.info.system for job in self.jobs if job.source_kind == "ROM"})
        if not systems:
            messagebox.showinfo(APP_NAME, "Add at least one ROM first. The application updates only the database files needed for loaded systems.")
            return
        self._save_ui_settings()

        def worker():
            try:
                self.events.put(("log", "Updating public Libretro metadata database files..."))
                result = update_database_for_systems(systems, self._metadata_settings())
                self.events.put(("message", "Libretro Database", result, "info"))
                self.events.put(("log", result.replace("\n", " | ")))
            except Exception as exc:
                self.events.put(("message", "Libretro Database", str(exc), "error"))
            self.events.put(("metadata_done",))

        self.metadata_worker = threading.Thread(target=worker, daemon=True)
        self.metadata_worker.start()

    def _capture_target_indices(self) -> list[int]:
        selected = self.selected_job_indices()
        selected_roms = [i for i in selected if self.jobs[i].source_kind == "ROM"]
        if len(selected_roms) == 1:
            return selected_roms
        checked = [i for i, job in enumerate(self.jobs) if job.source_kind == "ROM" and job.capture_checked]
        if checked:
            return checked
        return selected_roms

    def _validate_capture_settings(self, targets: list[int]) -> bool:
        if not targets:
            messagebox.showinfo(APP_NAME, "Check at least one ROM in the Capture column, or highlight exactly one ROM to capture it by itself. Imported screenshots do not require emulator capture.")
            return False
        try:
            if self.warmup_var.get() < 0 or self.duration_var.get() < 2 or self.interval_var.get() < 0.2:
                raise ValueError
            if self.candidates_var.get() < 1 or self.candidates_var.get() > 20:
                raise ValueError
        except Exception:
            messagebox.showerror(APP_NAME, "Capture settings are invalid. Duration must be at least 2 seconds, interval at least 0.2 seconds, and candidates 1-20.")
            return False
        target_jobs = [self.jobs[i] for i in targets]
        dolphin_family = [j for j in target_jobs if j.info.system in {"GameCube", "Wii", "WiiWare"}]
        regular_retro = [j for j in target_jobs if j.info.backend == "RetroArch" and j.info.system not in {"GameCube", "Wii", "WiiWare"}]
        if regular_retro and not Path(self.retro_var.get().strip()).exists():
            messagebox.showerror(APP_NAME, "Configure a valid RetroArch.exe path.")
            return False

        if dolphin_family:
            retro_path = Path(self.retro_var.get().strip()) if self.retro_var.get().strip() else Path("")
            standalone_path = Path(self.dolphin_var.get().strip()) if self.dolphin_var.get().strip() else None
            has_standalone = bool(standalone_path and standalone_path.exists())
            dolphin_core = discover_core(retro_path, dolphin_family[0].info.system) if retro_path.exists() else None
            core_ready = bool(dolphin_core and dolphin_system_files_ready(retro_path))
            if not core_ready and not has_standalone:
                messagebox.showerror(APP_NAME, "Configure a valid standalone Dolphin.exe fallback, or repair the RetroArch Dolphin core and its system files.")
                return False
            if dolphin_core and not core_ready and has_standalone:
                self.log("RetroArch Dolphin system files are incomplete. Capture will automatically use external Dolphin.exe instead.")
            elif not dolphin_core and has_standalone:
                self.log("RetroArch Dolphin core is unavailable. Capture will automatically use external Dolphin.exe instead.")

        if not self.output_var.get().strip():
            messagebox.showerror(APP_NAME, "Choose an export folder.")
            return False
        return True

    def start_capture(self):
        if self.worker and self.worker.is_alive():
            return
        targets = self._capture_target_indices()
        if not self._validate_capture_settings(targets):
            return
        self._save_ui_settings()
        self.cancel_event.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.config(value=0, maximum=max(1, len(targets)))
        settings = EmulatorSettings(
            retroarch_path=Path(self.retro_var.get().strip()) if self.retro_var.get().strip() else None,
            dolphin_path=Path(self.dolphin_var.get().strip()) if self.dolphin_var.get().strip() else None,
            warmup_seconds=float(self.warmup_var.get()),
            duration_seconds=float(self.duration_var.get()),
            interval_seconds=float(self.interval_var.get()),
        )
        options = self._export_options()
        count = int(self.candidates_var.get())
        self.worker = threading.Thread(
            target=self._capture_worker,
            args=(targets, settings, options, count),
            daemon=True,
        )
        self.worker.start()

    def stop_capture(self):
        self.cancel_event.set()
        self.stop_btn.config(state="disabled")
        self.log("Stop requested. The current emulator will close and any captured frames will be analyzed. The best available title_screen.png, selected output images, and metadata.txt will be exported.")

    def _finalize_captured_samples(self, job: GameJob, samples: list[FrameSample], session_root: Path, options: ExportOptions, candidate_count: int):
        if not samples:
            raise EmulatorError("No captured frames are available to finalize.")

        self.events.put(("log", f"Capture finished with {len(samples)} frame(s). Analyzing and exporting artwork..."))
        if job.info.system == "Nintendo DS" and options.screen_crop == "auto":
            candidates = select_nds_candidates(samples, session_root / "nds_single_screens", candidate_count)
            # Never access Tk variables from the worker thread. Reuse the options
            # captured on the UI thread and change only the crop mode.
            export_options = replace(options, screen_crop="full")
        else:
            candidates = select_candidates(samples, candidate_count)
            export_options = options

        if not candidates:
            raise EmulatorError("No usable title-screen candidates were found.")

        job.candidates = candidates
        job.selected = candidates[0]
        job.image_imported = False
        previous_artwork = (
            job.repository_artwork,
            job.repository_icon_artwork,
            job.repository_tv_gamepad_artwork,
        )
        previous_preference = job.prefer_user_source
        job.repository_artwork = None
        job.repository_icon_artwork = None
        job.repository_tv_gamepad_artwork = None
        job.prefer_user_source = True
        try:
            job.output_dir = self._export_job(
                job,
                sample=job.selected,
                candidates=candidates,
                options=export_options,
            )
        except Exception:
            (
                job.repository_artwork,
                job.repository_icon_artwork,
                job.repository_tv_gamepad_artwork,
            ) = previous_artwork
            job.prefer_user_source = previous_preference
            raise
        if any(previous_artwork):
            self.events.put((
                "log",
                "The new capture replaced downloaded artwork as the active source. The download remains cached.",
            ))

        expected = [job.output_dir / "title_screen.png", job.output_dir / "metadata.txt"]
        if export_options.create_icon:
            expected.append(job.output_dir / "Icon Image.png")
        if export_options.create_tv:
            expected.append(job.output_dir / "TV Image.png")
        if export_options.create_gamepad:
            expected.append(job.output_dir / "Gamepad Image.png")
        missing = [p.name for p in expected if not p.exists()]
        if missing:
            raise EmulatorError("Export finished but expected files were not created: " + ", ".join(missing))
        return candidates

    def _capture_worker(self, targets: list[int], settings: EmulatorSettings, options: ExportOptions, candidate_count: int):
        try:
            for order, i in enumerate(targets):
                job = self.jobs[i]
                if self.cancel_event.is_set():
                    break
                self.events.put(("status", i, "Starting capture"))
                self.events.put(("log", f"[{order + 1}/{len(targets)}] {job.info.path.name} - {job.info.system}"))
                session_root = Path(tempfile.mkdtemp(prefix="vc_title_capture_"))
                job.session_dir = session_root
                try:
                    samples = capture_game(
                        job.info,
                        settings,
                        session_root,
                        log=lambda msg: self.events.put(("log", msg)),
                        cancelled=self.cancel_event.is_set,
                    )
                    self.events.put(("status", i, f"Finalizing {len(samples)} captured frames"))
                    candidates = self._finalize_captured_samples(job, samples, session_root, options, candidate_count)
                    if self.cancel_event.is_set():
                        self.events.put(("status", i, f"Stopped - {len(candidates)} candidates retained"))
                        self.events.put(("log", f"Stopped early with {len(samples)} captured frame(s). Best available artwork was exported."))
                    else:
                        self.events.put(("status", i, f"Complete - {len(candidates)} candidates"))
                        self.events.put(("log", "Timed capture completed normally. Captured frames were finalized and exported."))
                    self.events.put(("log", f"Selected {job.selected.timestamp:.1f}s frame{f' ({job.selected.screen_label} screen)' if job.selected.screen_label else ''}, score {job.selected.score:.3f}"))
                    self.events.put(("log", f"Exported to: {job.output_dir}"))
                    self.events.put(("preview", i))
                except CaptureCancelled:
                    self.events.put(("status", i, "Cancelled - no usable frames"))
                    self.events.put(("log", "Capture was cancelled before any usable frames were available."))
                    break
                except Exception as exc:
                    self.events.put(("status", i, f"Error - {exc}"))
                    self.events.put(("log", f"ERROR: {exc}"))
                self.events.put(("progress", order + 1))
                if self.cancel_event.is_set():
                    self.events.put(("log", "Batch stopped. Captures from the current game remain available for review and re-export."))
                    break
        finally:
            self.events.put(("done",))

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.log(event[1])
                elif kind == "status":
                    idx, text = event[1], event[2]
                    if 0 <= idx < len(self.jobs):
                        self.jobs[idx].status = text
                        self._refresh_tree()
                elif kind == "progress":
                    self.progress.config(value=event[1])
                elif kind == "preview":
                    idx = event[1]
                    if not self.tree.selection() and idx < len(self.jobs):
                        self.tree.selection_set(str(idx))
                    self._on_tree_selection_changed()
                elif kind == "metadata_refresh":
                    self._refresh_tree()
                elif kind == "metadata_done":
                    self._refresh_tree()
                    if len(event) > 1 and self.auto_repository_artwork_var.get():
                        self._start_repository_artwork_lookup(event[1], automatic=True)
                elif kind == "repository_art_candidates":
                    self._receive_repository_artwork_candidates(*event[1:])
                elif kind == "repository_art_missing":
                    self._repository_artwork_missing(*event[1:])
                elif kind == "repository_art_error":
                    self._repository_artwork_error(*event[1:])
                elif kind == "boot_sound_candidates":
                    self._receive_boot_sound_candidates(*event[1:])
                elif kind == "boot_sound_missing":
                    self._boot_sound_missing(*event[1:])
                elif kind == "boot_sound_error":
                    self._boot_sound_error(*event[1:])
                elif kind == "repository_art_done":
                    self._refresh_tree()
                elif kind == "message":
                    title, text, level = event[1], event[2], event[3]
                    if level == "error":
                        messagebox.showerror(title, text, parent=self)
                    else:
                        messagebox.showinfo(title, text, parent=self)
                elif kind == "done":
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    if self.cancel_event.is_set():
                        self.log("Stopped. Any available title-screen capture and metadata have been exported.")
                    else:
                        self.log("Batch finished.")
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _on_tree_selection_changed(self):
        self._update_checked_action_state()
        self._update_icon_frame_selector()
        self._update_tv_gamepad_source_selector()
        self._update_boot_sound_selector()
        self._update_repository_artwork_review_state()
        self._update_preview()

    def _update_tv_gamepad_source_selector(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            self.tv_gamepad_source_combo.config(state="disabled", values=())
            self.tv_gamepad_source_var.set("Selected Screenshot")
            return
        job = self.jobs[indices[0]]
        if job.info.system not in {"Wii", "WiiWare"}:
            self.tv_gamepad_source_combo.config(state="disabled", values=())
            self.tv_gamepad_source_var.set("Selected Screenshot")
            return
        sources = self._available_tv_gamepad_sources(job)
        if job.tv_gamepad_source not in sources:
            job.tv_gamepad_source = "Selected Screenshot"
        self.tv_gamepad_source_combo.config(state="readonly", values=sources)
        self.tv_gamepad_source_var.set(job.tv_gamepad_source)

    def _apply_tv_gamepad_source(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            return
        job = self.jobs[indices[0]]
        if job.info.system not in {"Wii", "WiiWare"}:
            return
        choice = self.tv_gamepad_source_var.get().strip() or "Selected Screenshot"
        if choice not in self._available_tv_gamepad_sources(job):
            choice = "Selected Screenshot"
        job.tv_gamepad_source = choice
        if self._job_has_renderable_source(job):
            try:
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = f"Complete - TV/Gamepad source: {choice}"
                self.log(f"TV/Gamepad source for {job.info.display_name or job.info.path.stem}: {choice}")
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"ERROR changing TV/Gamepad source for {job.info.display_name}: {exc}")
        self._refresh_tree()
        self.tree.selection_set(str(indices[0]))
        self._update_preview()

    @staticmethod
    def _console_boot_sound_path(system: str) -> Path:
        key = re.sub(r"[^a-z0-9]+", "_", str(system or "console").casefold()).strip("_") or "console"
        return config_dir() / "console_boot_sounds" / f"{key}.wav"

    @staticmethod
    def _game_boot_sound_path(info: RomInfo) -> Path:
        system_key = re.sub(r"[^a-z0-9]+", "_", str(info.system or "console").casefold()).strip("_") or "console"
        identity = str(info.product_code or info.path.stem or "game").strip()
        identity_key = re.sub(r"[^a-z0-9]+", "_", identity.casefold()).strip("_")[:64] or "game"
        if not info.product_code:
            filename_digest = hashlib.sha256(info.path.name.casefold().encode("utf-8", errors="ignore")).hexdigest()[:10]
            identity_key = f"{identity_key}_{filename_digest}"
        return config_dir() / "game_boot_sounds" / system_key / f"{identity_key}.wav"

    @classmethod
    def _stored_game_boot_sound(cls, info: RomInfo) -> BootSound | None:
        path = cls._game_boot_sound_path(info)
        try:
            usable = path.is_file() and path.stat().st_size > 44
        except OSError:
            usable = False
        if not usable:
            return None
        return BootSound(
            provider_name="Stored game sound",
            provider_url="",
            system=info.system,
            path=path,
            label=f"{info.display_name or info.path.stem} imported sound",
            sound_kind="Stored per-game",
            product_code=info.product_code,
        )

    @classmethod
    def _local_console_boot_sound(cls, system: str) -> BootSound | None:
        path = cls._console_boot_sound_path(system)
        if not path.is_file() or path.stat().st_size <= 44:
            return None
        return BootSound(
            provider_name="Local console sound",
            provider_url="",
            system=system,
            path=path,
            label=f"{system} startup sound",
            sound_kind="Local console default",
        )

    def _update_boot_sound_selector(self):
        combo = self.boot_sound_combo
        status = self.boot_sound_status_label
        if combo is None:
            return
        indices = self.selected_job_indices()
        if len(indices) != 1:
            self._boot_sound_display_map = {}
            combo.config(state="disabled", values=())
            self.boot_sound_var.set("No boot sound selected")
            if self.boot_sound_play_btn is not None:
                self.boot_sound_play_btn.config(state="disabled")
            if self.boot_sound_stop_btn is not None:
                self.boot_sound_stop_btn.config(state="disabled")
            if status is not None:
                status.config(text="Select one game to configure its boot sound.")
            return

        job = self.jobs[indices[0]]
        stored_game_sound = self._stored_game_boot_sound(job.info)
        local_console_sound = self._local_console_boot_sound(job.info.system)
        bundled_sounds = built_in_boot_sounds(job.info.system)
        sounds: list[BootSound] = []
        seen_paths: set[str] = set()
        for sound in (job.boot_sound, stored_game_sound, local_console_sound, *bundled_sounds, *job.boot_sound_candidates):
            if sound is None:
                continue
            path_key = os.path.normcase(str(Path(sound.path).resolve(strict=False)))
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            sounds.append(sound)
        display_map: dict[str, BootSound | None] = {}
        for sound in sounds:
            base = sound.display_label
            label = base
            counter = 2
            while label in display_map:
                label = f"{base} ({counter})"
                counter += 1
            display_map[label] = sound
        self._boot_sound_display_map = display_map
        values = tuple(display_map)
        combo.config(state="readonly" if values else "disabled", values=values)
        active_label = next(
            (label for label, sound in display_map.items() if job.boot_sound is not None and sound and sound.path == job.boot_sound.path),
            "No boot sound selected",
        )
        self.boot_sound_var.set(active_label)
        playback_state = "normal" if job.boot_sound is not None else "disabled"
        if self.boot_sound_play_btn is not None:
            self.boot_sound_play_btn.config(state=playback_state)
        if self.boot_sound_stop_btn is not None:
            self.boot_sound_stop_btn.config(state=playback_state)
        if status is not None:
            if job.boot_sound is not None:
                status.config(
                    text=(
                        f"Active: {job.boot_sound.display_label}. Source file: {job.boot_sound.path.name}. "
                        "Play previews this exact selection; export writes it as bootSound.wav."
                    )
                )
            elif values:
                bundled_count = len(bundled_sounds)
                status.config(
                    text=(
                        f"{len(values)} sound option(s) are available, including {bundled_count} offline built-in choice(s). "
                        "Choose one above, find more sounds online, or browse for a local file."
                    )
                )
            else:
                status.config(text="No boot sound selected. Find more sounds online or browse for a local WAV, MP3, or BTSND file.")

    def _activate_boot_sound(self, job: GameJob, sound: BootSound, *, log_change: bool = True):
        if not sound.path.is_file() or sound.path.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            raise ValueError("The selected boot-sound file is missing or unsupported.")
        self.stop_boot_sound(log_change=False)
        previous_sound = job.boot_sound
        job.boot_sound = sound
        if all(candidate.path != sound.path for candidate in job.boot_sound_candidates):
            job.boot_sound_candidates.insert(0, sound)
        try:
            if not job.output_dir:
                self._write_metadata_now(job)
            if not job.output_dir:
                raise BootSoundConversionError("The output folder for the converted boot sound is unavailable.")
            self._apply_boot_sound_output(job, Path(job.output_dir))
        except Exception:
            job.boot_sound = previous_sound
            raise
        if log_change:
            self.log(
                f"Boot sound selected for {job.info.display_name or job.info.path.stem}: "
                f"{sound.sound_kind}, {sound.provider_name}, {sound.path.name}; converted to bootSound.wav."
            )

    def _apply_selected_boot_sound(self, event=None):
        selected = self._selected_single_job()
        if selected is None:
            return
        idx, job = selected
        sound = self._boot_sound_display_map.get(self.boot_sound_var.get())
        if sound is None:
            return
        try:
            self._activate_boot_sound(job, sound)
            job.status = f"Complete - boot sound: {sound.sound_kind.lower()}"
        except Exception as exc:
            job.status = f"Error - {exc}"
            messagebox.showerror(APP_NAME, f"The boot sound could not be applied:\n\n{exc}", parent=self)
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._update_boot_sound_selector()

    def browse_boot_sound_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        idx, job = selected
        raw = filedialog.askopenfilename(
            title=f"Select boot sound for {job.info.display_name or job.info.path.stem}",
            filetypes=[
                ("Supported boot sounds", "*.wav *.mp3 *.btsnd"),
                ("WAV audio", "*.wav"),
                ("MP3 audio", "*.mp3"),
                ("Wii U boot sound", "*.btsnd"),
                ("All files", "*.*"),
            ],
        )
        if not raw:
            return
        path = Path(raw)
        try:
            if path.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
                raise ValueError("Choose a WAV, MP3, or BTSND file.")
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError("The selected file is empty or unavailable.")
            if path.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("The selected boot sound exceeds the 24 MB safety limit.")
            destination = self._game_boot_sound_path(job.info)
            convert_boot_sound_to_wav(path, destination)
            sound = BootSound(
                provider_name="Stored game sound",
                provider_url="",
                system=job.info.system,
                path=destination,
                label=path.stem,
                sound_kind="Stored per-game",
                product_code=job.info.product_code,
            )
            self._activate_boot_sound(job, sound)
            job.status = "Complete - stored per-game boot sound"
            self.log(
                f"Imported {path.name} for {job.info.display_name or job.info.path.stem}; "
                f"the persistent copy is stored at {destination}."
            )
        except Exception as exc:
            job.status = f"Error - {exc}"
            messagebox.showerror(APP_NAME, f"The selected boot sound could not be used:\n\n{exc}", parent=self)
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._update_boot_sound_selector()

    def set_console_boot_sound_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        idx, selected_job = selected
        raw = filedialog.askopenfilename(
            title=f"Set {selected_job.info.system} console startup sound",
            filetypes=[
                ("Supported boot sounds", "*.wav *.mp3 *.btsnd"),
                ("WAV audio", "*.wav"),
                ("MP3 audio", "*.mp3"),
                ("Wii U boot sound", "*.btsnd"),
                ("All files", "*.*"),
            ],
        )
        if not raw:
            return
        source = Path(raw)
        try:
            if source.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
                raise ValueError("Choose a WAV, MP3, or BTSND file.")
            if not source.is_file() or source.stat().st_size <= 0:
                raise ValueError("The selected file is empty or unavailable.")
            if source.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("The selected console sound exceeds the 24 MB safety limit.")
            self.stop_boot_sound(log_change=False)
            destination = self._console_boot_sound_path(selected_job.info.system)
            convert_boot_sound_to_wav(source, destination)
            console_sound = self._local_console_boot_sound(selected_job.info.system)
            if console_sound is None:
                raise BootSoundConversionError("The console startup sound could not be stored.")

            updated = 0
            for job in self.jobs:
                if job.info.system != selected_job.info.system:
                    continue
                job.boot_sound_candidates = [
                    candidate
                    for candidate in job.boot_sound_candidates
                    if candidate.provider_name != "Local console sound"
                    and candidate.path != destination
                ]
                job.boot_sound_candidates.insert(0, console_sound)
                should_activate = (
                    job is selected_job
                    or job.boot_sound is None
                    or job.boot_sound.sound_kind in {"Console default", "Local console default"}
                )
                if not should_activate:
                    continue
                job.boot_sound = console_sound
                if job.output_dir:
                    self._apply_boot_sound_output(job, Path(job.output_dir))
                else:
                    self._write_metadata_now(job)
                job.status = f"Complete - {job.info.system} console startup sound"
                updated += 1
            self.log(
                f"Stored {selected_job.info.system} console startup sound from {source.name}. "
                f"Activated it for {updated} compatible loaded game(s); per-game and manual overrides were preserved."
            )
        except Exception as exc:
            selected_job.status = f"Error - {exc}"
            messagebox.showerror(APP_NAME, f"The console startup sound could not be set:\n\n{exc}", parent=self)
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._update_boot_sound_selector()

    def search_boot_sound_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        idx, job = selected
        if not system_has_boot_sound_repository(job.info.system):
            messagebox.showinfo(
                APP_NAME,
                f"No online boot-sound source is configured for {job.info.system}. "
                "Use Set Console to choose a system-specific startup sound from this computer.",
                parent=self,
            )
            return
        self._start_repository_artwork_lookup([idx], automatic=False, sound_only=True)

    def browse_myinstants_boot_sounds(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        _, job = selected
        url = myinstants_search_url(job.info.system)
        try:
            opened = webbrowser.open_new_tab(url)
        except Exception as exc:
            messagebox.showerror(
                APP_NAME,
                f"The MyInstants search could not be opened:\n\n{exc}\n\n{url}",
                parent=self,
            )
            return
        if not opened:
            messagebox.showinfo(
                APP_NAME,
                "Open this address in a browser, download a sound you are permitted to use, then return and choose Browse or Set Console:\n\n"
                + url,
                parent=self,
            )
        self.log(
            f"Opened MyInstants sound search for {job.info.system}. "
            "Downloaded files can be imported with Browse or Set Console."
        )

    def _prepare_boot_sound_preview(self, job: GameJob) -> Path:
        """Create a unique preview copy for the currently active sound.

        Reusing the standard bootSound.wav path can leave Windows playing the
        previous asynchronous file while that same filename is being replaced.
        Stopping first and playing a unique copy guarantees that manual and
        per-console selections preview the exact active source.
        """
        if job.boot_sound is None:
            raise BootSoundConversionError("No boot sound is active.")
        self.stop_boot_sound(log_change=False)
        if job.output_dir:
            wav_path = self._apply_boot_sound_output(job, Path(job.output_dir))
        else:
            self._write_metadata_now(job)
            wav_path = Path(job.output_dir) / "bootSound.wav" if job.output_dir else None
        if wav_path is None or not wav_path.is_file():
            raise BootSoundConversionError("The converted bootSound.wav file is unavailable.")

        descriptor, preview_name = tempfile.mkstemp(
            prefix="Wii_U_VC_Image_Builder_boot_preview_",
            suffix=".wav",
        )
        os.close(descriptor)
        preview_path = Path(preview_name)
        try:
            shutil.copyfile(wav_path, preview_path)
        except Exception:
            preview_path.unlink(missing_ok=True)
            raise
        self._boot_sound_preview_path = preview_path
        return preview_path

    def play_boot_sound_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        _, job = selected
        if job.boot_sound is None:
            messagebox.showinfo(APP_NAME, "Choose or search for a boot sound first.", parent=self)
            return
        try:
            if os.name != "nt":
                raise RuntimeError("Boot-sound playback is available in the Windows build.")
            preview_path = self._prepare_boot_sound_preview(job)
            import winsound
            winsound.PlaySound(
                str(preview_path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            if self.boot_sound_status_label is not None:
                self.boot_sound_status_label.config(
                    text=(
                        f"Playing active selection: {job.boot_sound.display_label}. "
                        f"Source file: {job.boot_sound.path.name}."
                    )
                )
            self.log(
                f"Playing active boot sound for {job.info.display_name or job.info.path.stem}: "
                f"{job.boot_sound.sound_kind}, {job.boot_sound.provider_name}, {job.boot_sound.path.name}."
            )
        except Exception as exc:
            self.stop_boot_sound(log_change=False)
            messagebox.showerror(APP_NAME, f"The boot sound could not be played:\n\n{exc}", parent=self)

    def stop_boot_sound(self, *, log_change: bool = True):
        if os.name == "nt":
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        preview_path = getattr(self, "_boot_sound_preview_path", None)
        if preview_path is not None:
            try:
                Path(preview_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._boot_sound_preview_path = None
        if log_change:
            self.log("Boot-sound playback stopped.")

    def clear_boot_sound_selected(self):
        selected = self._selected_single_job()
        if selected is None:
            messagebox.showinfo(APP_NAME, "Select exactly one game first.", parent=self)
            return
        idx, job = selected
        self.stop_boot_sound(log_change=False)
        stored_copy_removed = False
        active_sound = job.boot_sound
        if active_sound is not None and active_sound.provider_name == "Stored game sound":
            expected = self._game_boot_sound_path(job.info)
            active_key = os.path.normcase(str(Path(active_sound.path).resolve(strict=False)))
            expected_key = os.path.normcase(str(expected.resolve(strict=False)))
            if active_key == expected_key:
                try:
                    expected.unlink(missing_ok=True)
                    stored_copy_removed = True
                except OSError as exc:
                    messagebox.showerror(
                        APP_NAME,
                        f"The stored per-game sound could not be removed:\n\n{exc}",
                        parent=self,
                    )
                    return
                job.boot_sound_candidates = [
                    candidate for candidate in job.boot_sound_candidates
                    if os.path.normcase(str(Path(candidate.path).resolve(strict=False))) != expected_key
                ]
        job.boot_sound = None
        if job.output_dir:
            self._remove_boot_sound_outputs(Path(job.output_dir))
            self._write_boot_sound_note(job, Path(job.output_dir))
        job.status = "Ready - no boot sound"
        removal_note = " The stored configuration copy was removed." if stored_copy_removed else ""
        self.log(f"Boot sound cleared for {job.info.display_name or job.info.path.stem}.{removal_note}")
        self._refresh_tree()
        self.tree.selection_set(str(idx))
        self._update_boot_sound_selector()

    def _update_icon_frame_selector(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            self._icon_frame_map = {}
            self.icon_frame_combo.config(state="disabled", values=())
            self.icon_frame_var.set("Screenshot only")
            return
        job = self.jobs[indices[0]]
        choices = icon_template_choices(job.info.system)
        self._icon_frame_map = {label: filename for label, filename in choices}
        labels = [label for label, _ in choices]
        self.icon_frame_combo.config(state="readonly", values=labels)
        current_filename = job.info.icon_template or default_icon_template_for_system(job.info.system)
        current_label = next((label for label, filename in choices if filename == current_filename), labels[0] if labels else "Screenshot only")
        self.icon_frame_var.set(current_label)

    def _apply_selected_icon_frame(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            return
        job = self.jobs[indices[0]]
        choice = self.icon_frame_var.get().strip()
        filename = self._icon_frame_map.get(choice, "")
        if job.info.icon_template == filename:
            return
        job.info.icon_template = filename
        if self._job_has_renderable_source(job):
            try:
                job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = f"Complete - icon frame set to {choice}"
                self.log(f"Updated icon frame for {job.info.display_name or job.info.path.stem}: {choice}")
            except Exception as exc:
                job.status = f"Error - {exc}"
                self.log(f"ERROR updating icon frame for {job.info.display_name}: {exc}")
            self._refresh_tree()
            self.tree.selection_set(str(indices[0]))
            self._update_preview()

    def _update_preview(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            self.preview_refs = {}
            self.preview_paths = {}
            for key, lbl in self.preview_labels.items():
                lbl.config(image="", text="Select one game")
            self._sync_large_preview()
            return

        job = self.jobs[indices[0]]
        output_dir = Path(job.output_dir) if job.output_dir else None
        path_map = {
            "title": output_dir / "title_screen.png" if output_dir else None,
            "icon": output_dir / "Icon Image.png" if output_dir else None,
            "tv": output_dir / "TV Image.png" if output_dir else None,
            "gamepad": output_dir / "Gamepad Image.png" if output_dir else None,
        }
        fallback_title = None
        if job.selected and Path(job.selected.path).exists():
            fallback_title = Path(job.selected.path)
        elif job.info.path and Path(job.info.path).exists() and job.info.path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            fallback_title = Path(job.info.path)
        if (not path_map["title"] or not Path(path_map["title"]).exists()) and fallback_title:
            path_map["title"] = fallback_title

        self.preview_paths = {}
        for key, path in path_map.items():
            if path and Path(path).exists():
                self.preview_paths[key] = Path(path)
            else:
                missing_text = "No screenshot yet" if key == "title" else "Not generated"
                self.preview_labels[key].config(image="", text=missing_text)

        self._refresh_preview_images()
        self._sync_large_preview()
        # _autosize_detached_preview is intentionally called only when the
        # detached preview is first created. Subsequent image refreshes preserve
        # whatever size/position the user chose for either window.

    def review_selected(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select one completed game to review.")
            return
        job = self.jobs[indices[0]]
        if not job.candidates:
            messagebox.showinfo(APP_NAME, "This game has not been captured yet.")
            return
        ReviewWindow(self, job, self._review_export_options(), on_saved=lambda: self._after_review(job))

    def _review_export_options(self):
        return self._export_options()

    def _after_review(self, job):
        job.status = f"Complete - manually selected {job.selected.timestamp:.1f}s"
        self._refresh_tree()
        self._on_tree_selection_changed()

    def open_output(self):
        indices = self.selected_job_indices()
        path = None
        if len(indices) == 1 and self.jobs[indices[0]].output_dir:
            path = self.jobs[indices[0]].output_dir
        elif self.output_var.get().strip():
            path = Path(self.output_var.get().strip())
        if not path:
            return
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_NAME, str(path))

    def open_metadata(self):
        indices = self.selected_job_indices()
        if len(indices) != 1:
            messagebox.showinfo(APP_NAME, "Select one game first.")
            return
        job = self.jobs[indices[0]]
        path = job.output_dir / "metadata.txt" if job.output_dir else None
        if not path or not path.exists():
            messagebox.showinfo(APP_NAME, "No metadata.txt has been exported for this game yet.")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo(APP_NAME, str(path))

    def log(self, text: str):
        cleaned = str(text).rstrip()
        lines = cleaned.splitlines() or [""]
        for line in lines:
            self._activity_lines.append(line)
            if len(self._activity_lines) > 5000:
                del self._activity_lines[: len(self._activity_lines) - 5000]
            widget = self.log_text
            if widget is not None:
                try:
                    widget.config(state="normal")
                    widget.insert("end", line + "\n")
                    widget.see("end")
                    widget.config(state="disabled")
                except Exception:
                    pass
            error = self._write_activity_log_line(line)
            if error and not self._activity_file_error_reported:
                self._activity_file_error_reported = True
                self.activity_logging_enabled_var.set(False)
                failure = f"ERROR writing activity log file; live file logging was disabled: {error}"
                self._activity_lines.append(failure)
                if widget is not None:
                    try:
                        widget.config(state="normal")
                        widget.insert("end", failure + "\n")
                        widget.see("end")
                        widget.config(state="disabled")
                    except Exception:
                        pass
                self._update_activity_logging_status()

    def _on_close(self):
        self.cancel_event.set()
        self.stop_boot_sound(log_change=False)
        try:
            self.log("Application closing.")
        except Exception:
            pass
        try:
            self._save_ui_settings()
        except Exception:
            pass
        self.destroy()


class LargeImagePreviewWindow(tk.Toplevel):
    RESIZE_DELAY_MS = 60

    def __init__(self, parent: App, preview_key: str, heading: str, path: Path):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.parent_app = parent
        self.preview_key = preview_key
        self._source_image = None
        self._photo = None
        self._resize_job = None
        self._geometry_initialized = False
        self.title(f"{heading} - Large Preview")
        self.minsize(420, 300)
        self.resizable(True, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda event: self.close())

        header = ttk.Frame(self, padding=(10, 8, 10, 6))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        self.heading_label = ttk.Label(header, font=("Segoe UI", 10, "bold"), anchor="w")
        self.heading_label.grid(row=0, column=0, sticky="ew")
        close_btn = ttk.Button(header, text="Close", command=self.close)
        close_btn.grid(row=0, column=1, rowspan=2, padx=(10, 0))
        add_hover_tip(close_btn, "Close this enlarged image preview. Escape also closes the window.")
        self.details_label = ttk.Label(header, foreground="#666666", anchor="w")
        self.details_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        self.canvas = tk.Canvas(self, background="#202020", borderwidth=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", self._schedule_render, add="+")
        self.show_image(preview_key, heading, path)

    @staticmethod
    def _scaled_size(source_size: tuple[int, int], available_size: tuple[int, int]) -> tuple[int, int]:
        source_w, source_h = source_size
        available_w, available_h = available_size
        if source_w <= 0 or source_h <= 0 or available_w <= 0 or available_h <= 0:
            return (1, 1)
        scale = min(available_w / source_w, available_h / source_h, 4.0)
        return (
            max(1, int(round(source_w * scale))),
            max(1, int(round(source_h * scale))),
        )

    def show_image(
        self,
        preview_key: str,
        heading: str,
        path: Path,
        *,
        preserve_geometry: bool = False,
    ):
        try:
            with Image.open(path) as source:
                source.load()
                self._source_image = source.convert("RGB")
        except Exception as exc:
            self.show_unavailable(preview_key, heading, f"Could not open image: {exc}")
            return
        self.preview_key = preview_key
        self.title(f"{heading} - Large Preview")
        self.heading_label.config(text=heading)
        self.details_label.config(
            text=f"{path.name}    {self._source_image.width} x {self._source_image.height}    Fit to window"
        )
        if not self._geometry_initialized and not preserve_geometry:
            self._set_initial_geometry()
        self._geometry_initialized = True
        self._schedule_render()
        try:
            self.deiconify()
            self.lift()
            self.focus_set()
        except Exception:
            pass

    def show_unavailable(self, preview_key: str, heading: str, message: str = "No image is currently available."):
        self.preview_key = preview_key
        self._source_image = None
        self._photo = None
        self.title(f"{heading} - Large Preview")
        self.heading_label.config(text=heading)
        self.details_label.config(text=message)
        self._schedule_render()

    def _set_initial_geometry(self):
        if self._source_image is None:
            return
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            max_w = max(420, screen_w - 100)
            max_h = max(300, screen_h - 140)
            width = min(max_w, max(640, self._source_image.width + 40))
            height = min(max_h, max(480, self._source_image.height + 105))
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry("900x650")

    def _schedule_render(self, event=None):
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(self.RESIZE_DELAY_MS, self._render)

    def _render(self):
        self._resize_job = None
        self.canvas.delete("all")
        if self._source_image is None:
            self.canvas.create_text(
                max(1, self.canvas.winfo_width() // 2),
                max(1, self.canvas.winfo_height() // 2),
                text="No image available",
                fill="#DDDDDD",
                font=("Segoe UI", 11),
            )
            return
        available = (
            max(1, self.canvas.winfo_width() - 20),
            max(1, self.canvas.winfo_height() - 20),
        )
        size = self._scaled_size(self._source_image.size, available)
        image = self._source_image
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(
            max(1, self.canvas.winfo_width() // 2),
            max(1, self.canvas.winfo_height() // 2),
            image=self._photo,
            anchor="center",
        )

    def close(self):
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
            self._resize_job = None
        self.parent_app._large_preview_closed(self)
        self.destroy()


class ActivityLoggingSettingsDialog(tk.Toplevel):
    def __init__(self, parent: App):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.parent_app = parent
        self.title(f"{APP_NAME} - Activity Logging Settings")
        self.geometry("720x320")
        self.minsize(620, 280)
        self.resizable(True, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)

        suggested_directory = parent.activity_log_dir or (config_dir() / "Logs")
        self.enabled_var = tk.BooleanVar(value=bool(parent.activity_logging_enabled_var.get()))
        self.directory_var = tk.StringVar(value=str(suggested_directory))

        content = ttk.LabelFrame(self, text="Logging", padding=14)
        content.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        content.columnconfigure(1, weight=1)

        enabled_check = ttk.Checkbutton(
            content,
            text="Enable file logging",
            variable=self.enabled_var,
        )
        enabled_check.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 18))
        add_hover_tip(enabled_check, "Enable or disable live writing of Activity messages to a timestamped log file.")

        directory_label = ttk.Label(content, text="Log output directory:")
        directory_label.grid(row=1, column=0, sticky="w", padx=(0, 10))
        directory_entry = ttk.Entry(content, textvariable=self.directory_var)
        directory_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        browse_button = ttk.Button(content, text="Browse...", command=self._browse_directory)
        browse_button.grid(row=1, column=2, sticky="e")

        directory_tip = "Folder where timestamped Activity log files are written when file logging is enabled."
        add_hover_tip(directory_label, directory_tip)
        add_hover_tip(directory_entry, directory_tip)
        add_hover_tip(browse_button, "Select the folder used for Activity log files.")

        explanation = ttk.Label(
            content,
            text="When enabled, Activity is written live to a timestamped log file in this directory.",
            foreground="#555555",
            justify="left",
            wraplength=500,
        )
        explanation.grid(row=2, column=1, columnspan=2, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        open_button = ttk.Button(buttons, text="Open Folder", command=self._open_directory)
        open_button.pack(side="left")
        cancel_button = ttk.Button(buttons, text="Cancel", command=self._close)
        cancel_button.pack(side="right")
        save_button = ttk.Button(buttons, text="Save", command=self._save)
        save_button.pack(side="right", padx=(0, 8))
        add_hover_tip(open_button, "Open the directory currently shown above.")
        add_hover_tip(cancel_button, "Close without changing the current logging settings.")
        add_hover_tip(save_button, "Save the logging status and output directory.")

        self.bind("<Escape>", lambda event: self._close())
        self.bind("<Control-s>", lambda event: self._save())
        self.after_idle(lambda: self._activate(enabled_check))

    def _activate(self, focus_widget):
        try:
            self.grab_set()
        except Exception:
            pass
        focus_widget.focus_set()

    def _browse_directory(self):
        current = Path(self.directory_var.get().strip()).expanduser() if self.directory_var.get().strip() else config_dir()
        initial = current if current.exists() else config_dir()
        selected = filedialog.askdirectory(
            title="Select activity log output folder",
            initialdir=str(initial),
            mustexist=False,
            parent=self,
        )
        if selected:
            self.directory_var.set(selected)

    def _open_directory(self):
        raw_directory = self.directory_var.get().strip()
        if not raw_directory:
            messagebox.showwarning(APP_NAME, "Select a log output directory first.", parent=self)
            return
        try:
            directory = Path(raw_directory).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(directory)  # type: ignore[attr-defined]
        except AttributeError:
            messagebox.showinfo(APP_NAME, str(directory), parent=self)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"The log output directory could not be opened.\n\n{exc}", parent=self)

    def _save(self):
        enabled = bool(self.enabled_var.get())
        raw_directory = self.directory_var.get().strip()
        if enabled and not raw_directory:
            messagebox.showwarning(
                APP_NAME,
                "Select a log output directory before enabling file logging.",
                parent=self,
            )
            return

        directory = None
        if raw_directory:
            try:
                directory = Path(raw_directory).expanduser().resolve()
                directory.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                messagebox.showerror(
                    APP_NAME,
                    f"The log output directory could not be configured.\n\n{exc}",
                    parent=self,
                )
                return

        parent = self.parent_app
        directory_changed = parent.activity_log_dir != directory
        parent.activity_log_dir = directory
        parent.activity_logging_enabled_var.set(enabled and directory is not None)
        parent._activity_file_error_reported = False
        if directory_changed:
            parent._session_log_path = None
        parent._save_ui_settings()
        parent._update_activity_logging_status()
        if parent.activity_logging_enabled_var.get():
            parent.log(f"Live activity file logging enabled: {directory}")
        else:
            parent.log("Live activity file logging disabled.")
        self._close()

    def _close(self):
        self.parent_app._activity_logging_settings_window = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()



class ImageCropPositionDialog(tk.Toplevel):
    PREVIEW_DELAY_MS = 160
    ZOOM_MIN = 50
    ZOOM_MAX = 200
    FILL_CHOICES = ("Black", "Blurred Image", "Solid Color")

    def __init__(self, parent: App, info: RomInfo, on_preview=None):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.info = info
        self.on_preview = on_preview
        self.result = None
        self._preview_after_id = None
        self._link_guard = False
        self.title("Adjust Image Framing")
        self.geometry("760x950")
        self.resizable(False, False)
        self.transient(parent)
        self.vars = {
            "source_area_selection": tk.StringVar(
                value=getattr(info, "source_area_selection", getattr(info, "nds_screen_selection", "auto"))
            ),
            "icon_x": tk.IntVar(value=getattr(info, "icon_focus_x", 50)),
            "icon_y": tk.IntVar(value=getattr(info, "icon_focus_y", 50)),
            "icon_zoom": tk.IntVar(value=getattr(info, "icon_zoom", 100)),
            "icon_fill_mode": tk.StringVar(value=getattr(info, "icon_fill_mode", "Black")),
            "icon_fill_color": tk.StringVar(value=getattr(info, "icon_fill_color", "#000000")),
            "tv_x": tk.IntVar(value=getattr(info, "tv_focus_x", 50)),
            "tv_y": tk.IntVar(value=getattr(info, "tv_focus_y", 50)),
            "tv_zoom": tk.IntVar(value=getattr(info, "tv_zoom", 100)),
            "tv_fill_mode": tk.StringVar(value=getattr(info, "tv_fill_mode", "Black")),
            "tv_fill_color": tk.StringVar(value=getattr(info, "tv_fill_color", "#000000")),
            "gamepad_x": tk.IntVar(value=getattr(info, "gamepad_focus_x", 50)),
            "gamepad_y": tk.IntVar(value=getattr(info, "gamepad_focus_y", 50)),
            "gamepad_zoom": tk.IntVar(value=getattr(info, "gamepad_zoom", 100)),
            "gamepad_fill_mode": tk.StringVar(value=getattr(info, "gamepad_fill_mode", "Black")),
            "gamepad_fill_color": tk.StringVar(value=getattr(info, "gamepad_fill_color", "#000000")),
        }
        self.link_tv_gamepad_var = tk.BooleanVar(value=False)
        for key, var in self.vars.items():
            var.trace_add("write", lambda *args, k=key: self._value_changed(k))
        self.link_tv_gamepad_var.trace_add("write", self._link_changed)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _build(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        ttk.Label(
            root,
            text="Adjust how each generated image is framed. Position and zoom update the preview automatically. When zooming out far enough to reveal empty space, you can choose a black fill, a blurred background, or a solid color.",
            wraplength=720,
            foreground="#444444",
        ).pack(fill="x", pady=(0, 4))
        ttk.Label(
            root,
            text="Focus: 0 = left/top, 50 = center, 100 = right/bottom. Zoom: 100% = normal fit, above 100% zooms in, below 100% zooms out.",
            wraplength=720,
            foreground="#666666",
        ).pack(fill="x", pady=(0, 8))

        source_frame = ttk.LabelFrame(root, text="Source Image Area", padding=(8, 6))
        source_frame.pack(fill="x", pady=(0, 7))
        ttk.Label(
            source_frame,
            text="Choose which part of a stacked or side-by-side source image to use before applying the framing controls.",
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 5))
        source_buttons = ttk.Frame(source_frame)
        source_buttons.pack(fill="x")
        choices = (
            ("Automatic", "auto"),
            ("Full Image", "full"),
            ("Top", "top"),
            ("Bottom", "bottom"),
            ("Left", "left"),
            ("Right", "right"),
        )
        for label, value in choices:
            button = ttk.Radiobutton(
                source_buttons,
                text=label,
                value=value,
                variable=self.vars["source_area_selection"],
                style="Toolbutton",
            )
            button.pack(side="left", padx=(0, 4))
            add_hover_tip(
                button,
                f"{label}: choose the source image area first, then apply the existing Icon, TV, and Gamepad framing settings in real time.",
            )

        link_row = ttk.Frame(root)
        link_row.pack(fill="x", pady=(0, 6))
        link_cb = ttk.Checkbutton(link_row, text="Link TV and Gamepad crop & zoom", variable=self.link_tv_gamepad_var)
        link_cb.pack(side="left")
        add_hover_tip(link_cb, "When enabled, changing TV horizontal focus, vertical focus, or zoom immediately mirrors the matching Gamepad value, and vice versa. Icon controls remain independent.")
        link_note = ttk.Label(link_row, text="When linked, TV and Gamepad position and zoom stay matched.", foreground="#666666")
        link_note.pack(side="left", padx=(10, 0))
        add_hover_tip(link_note, "Useful when TV and Gamepad should use the same focal point and zoom. Disable it when their different aspect ratios need separate framing.")

        control_tips = {
            "x": "Horizontal focus chooses which horizontal part of the source image is favored when cropping or positioning. 0 keeps the left side, 50 centers it, and 100 keeps the right side.",
            "y": "Vertical focus chooses which vertical part of the source image is favored when cropping or positioning. 0 keeps the top, 50 centers it, and 100 keeps the bottom.",
            "zoom": "Zoom changes source-image magnification without distorting aspect ratio. 100% is normal cover-fit; above 100% zooms in; below 100% zooms out and may reveal the selected background fill.",
        }
        for title, prefix in (("Icon Image", "icon"), ("TV Image", "tv"), ("Gamepad Image", "gamepad")):
            frame = ttk.LabelFrame(root, text=title, padding=8)
            frame.pack(fill="x", pady=5)
            frame.columnconfigure(1, weight=1)
            row = 0
            for label, suffix, default, start, end in (("Horizontal focus", "x", 50, 0, 100), ("Vertical focus", "y", 50, 0, 100), ("Zoom (%)", "zoom", 100, self.ZOOM_MIN, self.ZOOM_MAX)):
                key = f"{prefix}_{suffix}"
                tip = f"{title}: {control_tips[suffix]}"
                control_label = ttk.Label(frame, text=label)
                control_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(0 if row == 0 else 6, 0))
                slider = ttk.Scale(frame, from_=start, to=end, variable=self.vars[key], orient="horizontal")
                slider.grid(row=row, column=1, sticky="ew", pady=(0 if row == 0 else 6, 0))
                spin = ttk.Spinbox(frame, from_=start, to=end, textvariable=self.vars[key], width=6)
                spin.grid(row=row, column=2, padx=(8, 0), pady=(0 if row == 0 else 6, 0))
                reset_btn = ttk.Button(frame, text="Reset", command=lambda k=key, d=default: self.vars[k].set(d))
                reset_btn.grid(row=row, column=3, padx=(8, 0), pady=(0 if row == 0 else 6, 0))
                add_hover_tip(control_label, tip)
                add_hover_tip(slider, tip)
                add_hover_tip(spin, tip)
                add_hover_tip(reset_btn, f"Reset {title} {label.lower()} to {default}{'%' if suffix == 'zoom' else ''}.")
                row += 1

            fill_label = ttk.Label(frame, text="Fill when zoomed out")
            fill_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
            fill_box = ttk.Combobox(frame, textvariable=self.vars[f"{prefix}_fill_mode"], values=list(self.FILL_CHOICES), width=18, state="readonly")
            fill_box.grid(row=row, column=1, sticky="w", pady=(8, 0))
            fill_tip = f"{title}: controls empty area revealed by zooming out. Black = black bars/space; Blurred Image = a blurred full-frame copy of the source behind the image; Solid Color = use the chosen hex color."
            add_hover_tip(fill_label, fill_tip)
            add_hover_tip(fill_box, fill_tip)
            fill_reset = ttk.Button(frame, text="Reset", command=lambda p=prefix: self.vars[f"{p}_fill_mode"].set("Black"))
            fill_reset.grid(row=row, column=3, padx=(8, 0), pady=(8, 0))
            add_hover_tip(fill_reset, f"Reset {title} zoom-out fill mode to Black.")
            row += 1

            color_label = ttk.Label(frame, text="Solid color")
            color_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            color_entry = ttk.Entry(frame, textvariable=self.vars[f"{prefix}_fill_color"], width=12)
            color_entry.grid(row=row, column=1, sticky="w", pady=(6, 0))
            pick_btn = ttk.Button(frame, text="Pick", command=lambda p=prefix: self._pick_color(p))
            pick_btn.grid(row=row, column=2, padx=(8, 0), pady=(6, 0))
            color_reset = ttk.Button(frame, text="Reset", command=lambda p=prefix: self.vars[f"{p}_fill_color"].set("#000000"))
            color_reset.grid(row=row, column=3, padx=(8, 0), pady=(6, 0))
            color_tip = f"{title}: hex color used only when Fill when zoomed out is set to Solid Color. Example: #000000 is black and #FFFFFF is white."
            add_hover_tip(color_label, color_tip)
            add_hover_tip(color_entry, color_tip)
            add_hover_tip(pick_btn, f"Open the Windows color picker for the {title} solid background fill.")
            add_hover_tip(color_reset, f"Reset {title} solid fill color to #000000 (black).")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(12, 0))
        reset_all_btn = ttk.Button(buttons, text="Reset All", command=self._reset)
        reset_all_btn.pack(side="left")
        add_hover_tip(reset_all_btn, "Reset all Icon, TV, and Gamepad focus values to center, zoom to 100%, fill mode to Black, and solid color to #000000.")
        cancel_btn = ttk.Button(buttons, text="Cancel", command=self._cancel)
        cancel_btn.pack(side="right", padx=(5, 0))
        add_hover_tip(cancel_btn, "Close this window and restore the crop/zoom/fill settings that were active when the window was opened.")
        done_btn = ttk.Button(buttons, text="Done", command=self._done)
        done_btn.pack(side="right")
        add_hover_tip(done_btn, "Keep the current live-preview framing settings and close this window.")
        self.grab_set()

    def _pick_color(self, prefix: str):
        current = self.vars[f"{prefix}_fill_color"].get().strip() or "#000000"
        chosen = colorchooser.askcolor(color=current, parent=self)[1]
        if chosen:
            self.vars[f"{prefix}_fill_color"].set(chosen.upper())

    def _values(self):
        try:
            values = {k: v.get() for k, v in self.vars.items()}
            for key in ("icon_x", "icon_y", "tv_x", "tv_y", "gamepad_x", "gamepad_y"):
                values[key] = int(round(float(values[key])))
            for key in ("icon_zoom", "tv_zoom", "gamepad_zoom"):
                values[key] = int(round(float(values[key])))
        except Exception:
            return None
        if any(values[k] < 0 or values[k] > 100 for k in ("icon_x", "icon_y", "tv_x", "tv_y", "gamepad_x", "gamepad_y")):
            return None
        if any(values[k] < self.ZOOM_MIN or values[k] > self.ZOOM_MAX for k in ("icon_zoom", "tv_zoom", "gamepad_zoom")):
            return None
        valid_modes = set(self.FILL_CHOICES)
        for key in ("icon_fill_mode", "tv_fill_mode", "gamepad_fill_mode"):
            if values[key] not in valid_modes:
                values[key] = "Black"
        for key in ("icon_fill_color", "tv_fill_color", "gamepad_fill_color"):
            raw = str(values[key]).strip() or "#000000"
            if not raw.startswith("#"):
                raw = f"#{raw}"
            if len(raw) == 4:
                raw = "#" + "".join(ch * 2 for ch in raw[1:])
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
                raw = "#000000"
            values[key] = raw.upper()
        source_area = str(values.get("source_area_selection", "auto") or "auto").strip().lower()
        values["source_area_selection"] = (
            source_area if source_area in {"auto", "full", "top", "bottom", "left", "right"} else "auto"
        )
        return values

    def _value_changed(self, key):
        if self._link_guard:
            return
        if self.link_tv_gamepad_var.get():
            partner = {
                "tv_x": "gamepad_x",
                "tv_y": "gamepad_y",
                "tv_zoom": "gamepad_zoom",
                "gamepad_x": "tv_x",
                "gamepad_y": "tv_y",
                "gamepad_zoom": "tv_zoom",
            }.get(key)
            if partner:
                try:
                    self._link_guard = True
                    self.vars[partner].set(self.vars[key].get())
                finally:
                    self._link_guard = False
        self._schedule_preview()

    def _link_changed(self, *args):
        if self.link_tv_gamepad_var.get():
            try:
                self._link_guard = True
                self.vars["gamepad_x"].set(self.vars["tv_x"].get())
                self.vars["gamepad_y"].set(self.vars["tv_y"].get())
                self.vars["gamepad_zoom"].set(self.vars["tv_zoom"].get())
            finally:
                self._link_guard = False
            self._schedule_preview()

    def _schedule_preview(self):
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        self._preview_after_id = self.after(self.PREVIEW_DELAY_MS, self._run_preview)

    def _run_preview(self):
        self._preview_after_id = None
        values = self._values()
        if values is None or self.on_preview is None:
            return
        self.on_preview(values)

    def _reset(self):
        defaults = {
            "source_area_selection": "auto",
            "icon_x": 50, "icon_y": 50, "icon_zoom": 100, "icon_fill_mode": "Black", "icon_fill_color": "#000000",
            "tv_x": 50, "tv_y": 50, "tv_zoom": 100, "tv_fill_mode": "Black", "tv_fill_color": "#000000",
            "gamepad_x": 50, "gamepad_y": 50, "gamepad_zoom": 100, "gamepad_fill_mode": "Black", "gamepad_fill_color": "#000000",
        }
        for key, value in defaults.items():
            self.vars[key].set(value)
        self._schedule_preview()

    def _validate_or_show_error(self):
        values = self._values()
        if values is None:
            messagebox.showerror(APP_NAME, f"Focus values must be 0 through 100 and zoom must be {self.ZOOM_MIN} through {self.ZOOM_MAX} percent.", parent=self)
            return None
        return values


    def _apply_without_closing(self):
        values = self._validate_or_show_error()
        if values is None:
            return
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self.on_preview is not None:
            self.on_preview(values)

    def _done(self):
        values = self._validate_or_show_error()
        if values is None:
            return
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self.on_preview is not None:
            self.on_preview(values)
        self.result = values
        self.destroy()

    def _cancel(self):
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        self.result = None
        self.destroy()


class ScreenshotPlacementDialog(tk.Toplevel):
    def __init__(self, parent: App, info: RomInfo):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.info = info
        self.result = None
        self.title("Template Placement / Size")
        self.geometry("470x300")
        self.resizable(False, False)
        self.transient(parent)
        self.default_rect = default_shot_rect(info.system)
        current = (info.shot_x, info.shot_y, info.shot_width, info.shot_height) if info.shot_width > 0 and info.shot_height > 0 else self.default_rect
        self.x_var = tk.IntVar(value=current[0]); self.y_var = tk.IntVar(value=current[1])
        self.w_var = tk.IntVar(value=current[2]); self.h_var = tk.IntVar(value=current[3])
        self._build()

    def _build(self):
        root = ttk.Frame(self, padding=12); root.pack(fill="both", expand=True)
        ttk.Label(root, text=f"Console: {self.info.system}", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Label(root, text="This changes the destination rectangle on the 1280 x 720 TV template. Gamepad placement scales automatically. Use Image Crop & Zoom to move or zoom the image within its crop instead.", wraplength=430, foreground="#555555").grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))
        placement_tips = {
            "X": "Horizontal position of the screenshot rectangle on the 1280 x 720 TV template, measured in pixels from the left edge.",
            "Y": "Vertical position of the screenshot rectangle on the 1280 x 720 TV template, measured in pixels from the top edge.",
            "Width": "Width of the screenshot rectangle in TV-template pixels. Gamepad placement scales automatically from this TV geometry.",
            "Height": "Height of the screenshot rectangle in TV-template pixels. The full rectangle must remain inside 1280 x 720.",
        }
        for label, var, row, col in [("X", self.x_var, 2, 0), ("Y", self.y_var, 2, 2), ("Width", self.w_var, 3, 0), ("Height", self.h_var, 3, 2)]:
            lbl = ttk.Label(root, text=label)
            lbl.grid(row=row, column=col, sticky="w", padx=(0, 5), pady=5)
            entry = ttk.Entry(root, textvariable=var, width=12)
            entry.grid(row=row, column=col+1, sticky="w", padx=(0, 18), pady=5)
            add_hover_tip(lbl, placement_tips[label])
            add_hover_tip(entry, placement_tips[label])
        dx, dy, dw, dh = self.default_rect
        default_note = ttk.Label(root, text=f"Console default: X={dx}, Y={dy}, Width={dw}, Height={dh}", foreground="#666666")
        default_note.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 10))
        add_hover_tip(default_note, "These are the built-in template coordinates for the selected console and can be restored with Reset to Console Default.")
        buttons = ttk.Frame(root); buttons.grid(row=5, column=0, columnspan=4, sticky="e")
        reset_btn = ttk.Button(buttons, text="Reset to Console Default", command=self._reset)
        reset_btn.pack(side="left", padx=(0, 8))
        add_hover_tip(reset_btn, "Restore this system's original built-in screenshot rectangle.")
        cancel_btn = ttk.Button(buttons, text="Cancel", command=self.destroy)
        cancel_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(cancel_btn, "Close without changing the template placement rectangle.")
        apply_btn = ttk.Button(buttons, text="Apply", command=self._apply)
        apply_btn.pack(side="left")
        add_hover_tip(apply_btn, "Save the entered template rectangle and close this dialog. The selected game's generated artwork will be rebuilt using the new placement.")
        self.grab_set()

    def _reset(self):
        x, y, w, h = self.default_rect
        self.x_var.set(x); self.y_var.set(y); self.w_var.set(w); self.h_var.set(h)

    def _apply(self):
        try:
            x, y, w, h = self.x_var.get(), self.y_var.get(), self.w_var.get(), self.h_var.get()
        except Exception:
            messagebox.showerror(APP_NAME, "X, Y, Width and Height must be whole numbers.", parent=self); return
        if x < 0 or y < 0 or w < 16 or h < 16 or x + w > 1280 or y + h > 720:
            messagebox.showerror(APP_NAME, "The rectangle must stay within the 1280 x 720 TV template and be at least 16 x 16 pixels.", parent=self); return
        self.result = (x, y, w, h)
        self.destroy()


class GameDetailsDialog(tk.Toplevel):
    def __init__(self, parent: App, info: RomInfo):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.title("Edit Game Details")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.initial_title = info.display_name or info.path.stem
        self.year_var = tk.StringVar(value=info.release_year or "")
        self.players_var = tk.StringVar(value=info.players or "")
        self.product_code_var = tk.StringVar(value=info.product_code or "")

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        game_name_label = ttk.Label(root, text="Game name")
        game_name_label.grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.title_text = tk.Text(root, width=48, height=4, wrap="word")
        self.title_text.grid(row=0, column=1, sticky="ew", pady=4)
        self.title_text.insert("1.0", self.initial_title)
        game_name_tip = "Display name used for output folders, metadata, and optional banner title text. You can enter up to three explicit lines; those line breaks are preserved on generated artwork."
        add_hover_tip(game_name_label, game_name_tip)
        add_hover_tip(self.title_text, game_name_tip)
        year_label = ttk.Label(root, text="Released year")
        year_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        year_entry = ttk.Entry(root, textvariable=self.year_var, width=15)
        year_entry.grid(row=1, column=1, sticky="w", pady=4)
        year_tip = "Four-digit release year, such as 2006. Used in metadata.txt and optional TV/Gamepad release-date text. May be left blank."
        add_hover_tip(year_label, year_tip); add_hover_tip(year_entry, year_tip)
        players_label = ttk.Label(root, text="Players")
        players_label.grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        players_entry = ttk.Entry(root, textvariable=self.players_var, width=15)
        players_entry.grid(row=2, column=1, sticky="w", pady=4)
        players_tip = "Player-count value, for example 1 or 1-4. Used in metadata.txt and optional TV/Gamepad player-count text."
        add_hover_tip(players_label, players_tip); add_hover_tip(players_entry, players_tip)
        code_label = ttk.Label(root, text="Product code")
        code_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        code_entry = ttk.Entry(root, textvariable=self.product_code_var, width=20)
        code_entry.grid(row=3, column=1, sticky="w", pady=4)
        code_tip = "Game/product identifier detected from the ROM when available. It is used for metadata matching and output-folder naming. Only letters, numbers, underscore, and hyphen are kept."
        add_hover_tip(code_label, code_tip); add_hover_tip(code_entry, code_tip)
        ttk.Label(root, text='Game name may use up to three lines. Examples: year "2006", players "1-4". Product code is normalized to letters, numbers, _ and -.', foreground="#666666", wraplength=500).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(3, 8)
        )
        buttons = ttk.Frame(root)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        cancel_btn = ttk.Button(buttons, text="Cancel", command=self.destroy)
        cancel_btn.pack(side="left", padx=(0, 5))
        add_hover_tip(cancel_btn, "Close without saving changes to the selected game's details.")
        save_btn = ttk.Button(buttons, text="Save", command=self._save)
        save_btn.pack(side="left")
        add_hover_tip(save_btn, "Save the edited game details. Existing generated files are rebuilt/refreshed by the calling workflow as appropriate.")
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.after(10, lambda: self.focus_force())

    def _save(self):
        raw_title = self.title_text.get("1.0", "end").strip()
        lines = [" ".join(line.strip().split()) for line in raw_title.splitlines()]
        lines = [line for line in lines if line][:3]
        title = "\n".join(lines)
        year = self.year_var.get().strip()
        players = self.players_var.get().strip()
        product_code = re.sub(r"[^A-Za-z0-9_-]+", "", self.product_code_var.get().strip()).upper()
        if not title:
            messagebox.showerror(APP_NAME, "Game name cannot be blank.", parent=self)
            return
        if year and (not year.isdigit() or len(year) != 4):
            messagebox.showerror(APP_NAME, "Released year must be a four-digit year, or blank.", parent=self)
            return
        self.result = (title, year, players, product_code)
        self.destroy()


class RepositoryArtworkReviewWindow(tk.Toplevel):
    def __init__(self, parent: App, job: GameJob, options: ExportOptions):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.parent_app = parent
        self.job = job
        self.options = options
        self.candidates = list(job.repository_artwork_candidates)
        self.preview_ref = None
        self.asset_var = tk.StringVar()
        self.title(f"Review Artwork Matches - {job.info.display_name or job.info.path.stem}")
        self.geometry("1000x720")
        self.minsize(760, 520)
        self.resizable(True, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda event: self.close())
        self._build()
        initial = 0
        active_artwork = (
            job.repository_artwork
            or parent._repository_icon_artwork(job)
            or parent._repository_tv_gamepad_artwork(job)
        )
        if active_artwork in self.candidates:
            initial = self.candidates.index(active_artwork)
        self.listbox.selection_set(initial)
        self.listbox.activate(initial)
        self._show_candidate(initial)

    def _candidate_label(self, index: int, artwork: RepositoryArtwork) -> str:
        type_text = artwork.source_image_kind or "Finished Wii U artwork"
        title_text = artwork.matched_title or artwork.product_code or "Exact assets"
        review_text = "[Manual review] " if artwork.confidence < 0.86 else ""
        icon_assigned = self.parent_app._repository_icon_artwork(self.job) == artwork
        tv_gamepad_assigned = self.parent_app._repository_tv_gamepad_artwork(self.job) == artwork
        if icon_assigned and tv_gamepad_assigned:
            assignment_text = "[Icon + TV/Gamepad] "
        elif icon_assigned:
            assignment_text = "[Icon] "
        elif tv_gamepad_assigned:
            assignment_text = "[TV/Gamepad] "
        else:
            assignment_text = ""
        return f"{index + 1}. {assignment_text}{review_text}{artwork.provider_name} | {type_text} | {title_text}"

    @staticmethod
    def _image_choices(artwork: RepositoryArtwork) -> list[tuple[str, Path]]:
        choices: list[tuple[str, Path]] = []
        for label, path in (
            ("Source image", artwork.source_image_path),
            ("Icon asset", artwork.icon_path),
            ("TV asset", artwork.tv_path),
            ("Gamepad asset", artwork.gamepad_path),
        ):
            if path is not None and Path(path).is_file():
                choices.append((label, Path(path)))
        return choices

    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=(
                "Compare every retained repository match. A single choice can be used for all outputs, "
                "or different choices can be assigned to Icon Image and TV/Gamepad Images."
            ),
            foreground="#555555",
            justify="left",
            wraplength=940,
        ).pack(fill="x", pady=(0, 8))

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)
        list_frame = ttk.LabelFrame(paned, text="Artwork Matches", padding=6)
        preview_frame = ttk.LabelFrame(paned, text="Preview", padding=8)
        paned.add(list_frame, weight=2)
        paned.add(preview_frame, weight=3)

        list_host = ttk.Frame(list_frame)
        list_host.pack(fill="both", expand=True)
        list_host.columnconfigure(0, weight=1)
        list_host.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(list_host, exportselection=False, activestyle="dotbox")
        list_scroll = ttk.Scrollbar(list_host, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll.grid(row=0, column=1, sticky="ns")
        for index, artwork in enumerate(self.candidates):
            self.listbox.insert("end", self._candidate_label(index, artwork))
        self.listbox.bind("<<ListboxSelect>>", self._list_changed)
        self.listbox.bind("<Double-1>", lambda event: self.use_selected("all"))
        add_hover_tip(self.listbox, "Select a retained source match to inspect it. Double-click to assign it to all enabled image outputs.")

        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)
        asset_row = ttk.Frame(preview_frame)
        asset_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        asset_row.columnconfigure(1, weight=1)
        ttk.Label(asset_row, text="Preview asset:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.asset_combo = ttk.Combobox(asset_row, textvariable=self.asset_var, state="readonly")
        self.asset_combo.grid(row=0, column=1, sticky="ew")
        self.asset_combo.bind("<<ComboboxSelected>>", self._asset_changed)
        self.preview_label = ttk.Label(preview_frame, text="No preview available", anchor="center")
        self.preview_label.grid(row=1, column=0, sticky="nsew")
        self.preview_label.bind("<Configure>", self._asset_changed, add="+")
        self.details_label = ttk.Label(
            preview_frame,
            text="",
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.details_label.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        preview_frame.bind(
            "<Configure>",
            lambda event: self.details_label.config(wraplength=max(260, event.width - 30)),
            add="+",
        )

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(8, 0))
        self.status_label = ttk.Label(controls, text="", foreground="#555555")
        self.status_label.pack(side="left", fill="x", expand=True)
        ttk.Button(controls, text="Close", command=self.close).pack(side="right")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(6, 0))
        for column in range(3):
            actions.columnconfigure(column, weight=1, uniform="artwork_actions")
        self.use_all_btn = ttk.Button(
            actions,
            text="Use for All Outputs",
            command=lambda: self.use_selected("all"),
        )
        self.use_all_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.use_icon_btn = ttk.Button(
            actions,
            text="Use for Icon Image",
            command=lambda: self.use_selected("icon"),
        )
        self.use_icon_btn.grid(row=0, column=1, sticky="ew", padx=3)
        self.use_tv_gamepad_btn = ttk.Button(
            actions,
            text="Use for TV/Gamepad Images",
            command=lambda: self.use_selected("tv_gamepad"),
        )
        self.use_tv_gamepad_btn.grid(row=0, column=2, sticky="ew", padx=(3, 0))
        add_hover_tip(self.use_all_btn, "Assign this match to Icon Image, TV Image, and Gamepad Image wherever the source provides them.")
        add_hover_tip(self.use_icon_btn, "Assign this match only to Icon Image. The current TV and Gamepad assignment is preserved.")
        add_hover_tip(self.use_tv_gamepad_btn, "Assign this match only to TV Image and Gamepad Image. The current Icon assignment is preserved.")

    def _selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        return selection[0] if selection else None

    def _list_changed(self, event=None):
        index = self._selected_index()
        if index is not None:
            self._show_candidate(index)

    def _show_candidate(self, index: int):
        if not (0 <= index < len(self.candidates)):
            return
        artwork = self.candidates[index]
        choices = self._image_choices(artwork)
        labels = [label for label, _ in choices]
        self.asset_combo.config(values=labels, state="readonly" if labels else "disabled")
        self.asset_var.set(labels[0] if labels else "")
        outputs = ", ".join(artwork.available_outputs) or "None"
        decision = "Manual approval required" if artwork.confidence < 0.86 else "Confident match"
        icon_assignment = "This match" if self.parent_app._repository_icon_artwork(self.job) == artwork else "Another source or generated artwork"
        tv_gamepad_assignment = "This match" if self.parent_app._repository_tv_gamepad_artwork(self.job) == artwork else "Another source or generated artwork"
        self.details_label.config(text=(
            f"Source: {artwork.provider_name}\n"
            f"Artwork type: {artwork.source_image_kind or 'Finished Wii U artwork'}\n"
            f"Match: {artwork.match_method}\n"
            f"Matched title: {artwork.matched_title or 'Not applicable'}\n"
            f"Confidence: {artwork.confidence:.0%}\n"
            f"Decision: {decision}\n"
            f"Available outputs: {outputs}\n"
            f"Icon assignment: {icon_assignment}\n"
            f"TV/Gamepad assignment: {tv_gamepad_assignment}"
        ))
        self.use_icon_btn.config(state="normal" if "Icon" in artwork.available_outputs else "disabled")
        self.use_tv_gamepad_btn.config(
            state="normal" if {"TV", "Gamepad"} & set(artwork.available_outputs) else "disabled"
        )
        self.use_all_btn.config(state="normal" if artwork.available_outputs else "disabled")
        self._render_preview()

    def _asset_changed(self, event=None):
        self.after_idle(self._render_preview)

    def _render_preview(self):
        index = self._selected_index()
        if index is None or not (0 <= index < len(self.candidates)):
            return
        choices = dict(self._image_choices(self.candidates[index]))
        path = choices.get(self.asset_var.get())
        if path is None:
            self.preview_ref = None
            self.preview_label.config(image="", text="No preview available")
            return
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                available = (
                    max(80, self.preview_label.winfo_width() - 16),
                    max(80, self.preview_label.winfo_height() - 16),
                )
                image.thumbnail(available, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
            self.preview_ref = photo
            self.preview_label.config(image=photo, text="")
        except Exception as exc:
            self.preview_ref = None
            self.preview_label.config(image="", text=f"Preview unavailable: {exc}")

    def _refresh_candidate_labels(self, selected_index: int):
        self.listbox.delete(0, "end")
        for index, artwork in enumerate(self.candidates):
            self.listbox.insert("end", self._candidate_label(index, artwork))
        self.listbox.selection_set(selected_index)
        self.listbox.activate(selected_index)

    def use_selected(self, assignment: str = "all"):
        index = self._selected_index()
        if index is None or not (0 <= index < len(self.candidates)):
            return
        artwork = self.candidates[index]
        applied = self.parent_app._accept_repository_artwork(
            self.job,
            artwork,
            self.options,
            automatic=False,
            show_confirmation=False,
            assignment=assignment,
        )
        if applied:
            assignment_name = {
                "all": "all enabled outputs",
                "icon": "Icon Image",
                "tv_gamepad": "TV and Gamepad Images",
            }.get(assignment, "all enabled outputs")
            self.status_label.config(
                text=f"Assigned {artwork.provider_name} to {assignment_name}."
            )
            self._refresh_candidate_labels(index)
            self._show_candidate(index)

    def close(self):
        self.parent_app._repository_artwork_review_closed(self)
        self.destroy()


class ReviewWindow(tk.Toplevel):
    def __init__(self, parent: App, job: GameJob, options: ExportOptions, on_saved=None):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.parent = parent
        self.job = job
        self.options = options
        self.on_saved = on_saved
        self.title(f"Review - {job.info.path.name}")
        self.geometry("980x700")
        self.transient(parent)
        self.selected_index = tk.IntVar(value=max(0, job.candidates.index(job.selected) if job.selected in job.candidates else 0))
        self.crop_var = tk.StringVar(value=options.screen_crop)
        self.preview_ref = None
        self._build()
        self._show_candidate(self.selected_index.get())

    def _build(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        top = ttk.Frame(root)
        top.pack(fill="both", expand=True)
        self.preview = ttk.Label(top, anchor="center")
        self.preview.pack(side="left", fill="both", expand=True, padx=(0, 8))
        add_hover_tip(self.preview, "Preview of the currently selected captured candidate frame. This is the source frame that will become title_screen.png if you re-export it.")

        side = ttk.Frame(top)
        side.pack(side="left", fill="y")
        ttk.Label(side, text="Candidate frames", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(side, width=34, height=22, exportselection=False)
        self.listbox.pack(fill="y", expand=True)
        add_hover_tip(self.listbox, "Captured candidate frames ranked by the application's title-screen scoring. Click a candidate to preview it before choosing Use This Frame and Re-export.")
        for i, c in enumerate(self.job.candidates):
            self.listbox.insert("end", f"#{i + 1}  {c.timestamp:5.1f}s   score {c.score:.3f}")
        self.listbox.selection_set(self.selected_index.get())
        self.listbox.bind("<<ListboxSelect>>", self._list_changed)

        info = ttk.LabelFrame(root, text="Candidate Metrics", padding=8)
        info.pack(fill="x", pady=(8, 0))
        self.metrics = ttk.Label(info, text="")
        self.metrics.pack(anchor="w")
        add_hover_tip(self.metrics, "Capture-analysis metrics used to rank frames. Score is the combined ranking; Stability, Complexity, and Brightness help distinguish likely title screens from transitions, black frames, or gameplay.")

        controls = ttk.Frame(root, padding=(0, 8, 0, 0))
        controls.pack(fill="x")

        # Keep the primary action on its own compact row so it is always visible
        # at the default Review window width. The explanatory text wraps below it
        # instead of pushing the button off the right edge.
        action_row = ttk.Frame(controls)
        action_row.pack(fill="x")
        crop_label = ttk.Label(action_row, text="Screen crop:")
        crop_label.pack(side="left")
        crop = ttk.Combobox(action_row, textvariable=self.crop_var, values=("auto", "full", "top", "bottom", "left", "right"), state="readonly", width=10)
        crop.pack(side="left", padx=(4, 10))
        review_crop_tip = "Crop applied when re-exporting this candidate: Auto = application chooses; Full = whole frame; Top/Bottom/Left/Right = corresponding half. Use Full for NDS candidates that were already separated to one screen."
        add_hover_tip(crop_label, review_crop_tip)
        add_hover_tip(crop, review_crop_tip)
        done_btn = ttk.Button(action_row, text="Done", command=self.destroy)
        done_btn.pack(side="right")
        add_hover_tip(done_btn, "Close the Review window without changing the currently exported frame.")
        use_frame_btn = ttk.Button(action_row, text="Use This Frame and Re-export", command=self.save)
        use_frame_btn.pack(side="right", padx=(0, 6))
        add_hover_tip(use_frame_btn, "Make the currently previewed candidate the selected title screen and immediately rebuild the enabled Icon, TV, Gamepad, and metadata outputs. The Review window stays open.")

        self.save_status = ttk.Label(controls, text="", foreground="#555555")
        self.save_status.pack(fill="x", pady=(6, 0))

        note = ttk.Label(
            controls,
            text="NDS Auto candidates are already cropped to one screen. Use Full when reviewing them. Re-export replaces title_screen.png, the selected Icon Image, TV Image, Gamepad Image outputs, and refreshes metadata.txt.",
            justify="left",
            wraplength=820,
        )
        note.pack(fill="x", pady=(6, 0))

    def _list_changed(self, event=None):
        selection = self.listbox.curselection()
        if selection:
            self.selected_index.set(selection[0])
            self._show_candidate(selection[0])

    def _show_candidate(self, index: int):
        c = self.job.candidates[index]
        try:
            with Image.open(c.path) as im:
                image = im.convert("RGB")
                image.thumbnail((650, 500), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
            self.preview_ref = photo
            self.preview.config(image=photo)
        except Exception:
            self.preview.config(image="")
        self.metrics.config(text=(
            f"Time: {c.timestamp:.2f}s    Score: {c.score:.3f}    Stability: {c.stability:.3f}    "
            f"Complexity: {c.complexity:.3f}    Brightness: {c.brightness:.3f}    "
            f"Screen: {c.screen_label or 'full'}"
        ))

    def save(self):
        idx = self.selected_index.get()
        if not (0 <= idx < len(self.job.candidates)):
            return
        self.options.screen_crop = self.crop_var.get()
        self.job.selected = self.job.candidates[idx]
        self.job.output_dir = self.parent._export_job(
            self.job,
            sample=self.job.selected,
            candidates=self.job.candidates,
            options=self.options,
        )
        if self.on_saved:
            self.on_saved()
        self.save_status.config(text=f"Re-exported selected frame to: {self.job.output_dir}")



class DocumentationWindow(tk.Toplevel):
    def __init__(self, parent: App, initial_section: str | None = None):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.parent = parent
        self.title(f"{APP_NAME} Documentation")
        self.geometry("980x700")
        self.minsize(760, 520)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self.select_section(initial_section or HELP_SECTIONS[0][0])
        self.after(10, self.focus_force)

    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=f"{APP_NAME} Documentation", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}", foreground="#666666").pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(outer, orient="horizontal")
        content.pack(fill="both", expand=True)

        nav_frame = ttk.Frame(content, padding=(0, 0, 8, 0))
        text_frame = ttk.Frame(content)
        content.add(nav_frame, weight=1)
        content.add(text_frame, weight=4)

        ttk.Label(nav_frame, text="Topics", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.topic_list = tk.Listbox(
            nav_frame,
            exportselection=False,
            activestyle="none",
            font=("Segoe UI", 10),
            background="#F3F6FA",
            foreground="#273244",
            selectbackground="#4F76C7",
            selectforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#CBD3DF",
        )
        self.topic_list.pack(fill="both", expand=True)
        add_hover_tip(self.topic_list, "Choose a documentation topic. The selected topic opens in the panel on the right.")
        for title, _ in HELP_SECTIONS:
            self.topic_list.insert("end", title)
        self.topic_list.bind("<<ListboxSelect>>", self._topic_selected)

        self.section_title = ttk.Label(text_frame, text="", font=("Segoe UI", 16, "bold"), foreground="#273244")
        self.section_title.pack(anchor="w", fill="x", pady=(0, 6))
        self.help_text = ScrolledText(
            text_frame,
            wrap="word",
            padx=20,
            pady=16,
            font=("Segoe UI", 10),
            background="#FFFFFF",
            foreground="#303744",
            insertbackground="#303744",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#CBD3DF",
            selectbackground="#C9D8F2",
        )
        self.help_text.pack(fill="both", expand=True)
        self._configure_document_tags()
        self.help_text.configure(state="disabled")
        add_hover_tip(self.help_text, "Built-in documentation for the selected topic. Use Copy Topic below if you want to copy the current section to the clipboard.")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, text="F1 opens this documentation center from the main application.", foreground="#666666").pack(side="left")
        copy_btn = ttk.Button(footer, text="Copy Topic", command=self._copy_topic)
        copy_btn.pack(side="right", padx=(5, 0))
        add_hover_tip(copy_btn, "Copy the current documentation topic title and text to the clipboard.")
        close_btn = ttk.Button(footer, text="Close", command=self.destroy)
        close_btn.pack(side="right")
        add_hover_tip(close_btn, "Close the Documentation Center. Press F1 from the main application to open it again.")

    def _topic_selected(self, event=None):
        selected = self.topic_list.curselection()
        if selected:
            title = self.topic_list.get(selected[0])
            self._show_section(title)

    def select_section(self, title: str):
        titles = [name for name, _ in HELP_SECTIONS]
        if title not in HELP_BY_TITLE:
            title = titles[0]
        index = titles.index(title)
        self.topic_list.selection_clear(0, "end")
        self.topic_list.selection_set(index)
        self.topic_list.activate(index)
        self.topic_list.see(index)
        self._show_section(title)

    def _show_section(self, title: str):
        body = HELP_BY_TITLE.get(title, "")
        self.section_title.config(text=title)
        self.help_text.configure(state="normal")
        self.help_text.delete("1.0", "end")
        self._render_document_body(body)
        self.help_text.configure(state="disabled")
        self.help_text.yview_moveto(0.0)

    def _configure_document_tags(self):
        self.help_text.tag_configure(
            "section_heading",
            font=("Segoe UI", 11, "bold"),
            foreground="#365FAD",
            spacing1=16,
            spacing3=6,
        )
        self.help_text.tag_configure("body", font=("Segoe UI", 10), spacing3=9, lmargin1=2, lmargin2=2)
        self.help_text.tag_configure(
            "bullet",
            font=("Segoe UI", 10),
            lmargin1=22,
            lmargin2=38,
            spacing1=2,
            spacing3=3,
        )
        self.help_text.tag_configure(
            "numbered",
            font=("Segoe UI", 10),
            lmargin1=22,
            lmargin2=42,
            spacing1=2,
            spacing3=4,
        )
        self.help_text.tag_configure("definition_label", font=("Segoe UI", 10, "bold"), foreground="#273244")
        self.help_text.tag_configure("accent", foreground="#365FAD", font=("Segoe UI", 10, "bold"))

    @staticmethod
    def _is_document_heading(line: str) -> bool:
        letters = [char for char in line if char.isalpha()]
        return bool(letters) and len(line) <= 64 and line == line.upper()

    def _render_document_body(self, body: str):
        for raw_line in body.strip().splitlines():
            line = raw_line.strip()
            if not line:
                self.help_text.insert("end", "\n")
                continue
            if self._is_document_heading(line):
                self.help_text.insert("end", line + "\n", "section_heading")
                continue
            if line.startswith("- "):
                self.help_text.insert("end", "•  ", "accent")
                self.help_text.insert("end", line[2:] + "\n", "bullet")
                continue
            number_match = re.match(r"^(\d+)\.\s+(.+)$", line)
            if number_match:
                self.help_text.insert("end", f"{number_match.group(1)}.  ", "accent")
                self.help_text.insert("end", number_match.group(2) + "\n", "numbered")
                continue
            definition_match = re.match(r"^([^:]{1,42}):\s+(.+)$", line)
            if definition_match:
                self.help_text.insert("end", definition_match.group(1) + ": ", "definition_label")
                self.help_text.insert("end", definition_match.group(2) + "\n", "body")
                continue
            self.help_text.insert("end", line + "\n", "body")

    def _copy_topic(self):
        title = self.section_title.cget("text")
        body = HELP_BY_TITLE.get(title, "")
        self.clipboard_clear()
        self.clipboard_append(f"{title}\n\n{body.strip()}")


class AboutWindow(tk.Toplevel):
    def __init__(self, parent: App):
        super().__init__(parent)
        apply_child_window_icon(self)
        self.title(f"About {APP_NAME}")
        self.geometry("600x500")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="center")
        ttk.Label(root, text=f"Version {APP_VERSION}", foreground="#666666").pack(anchor="center", pady=(2, 12))
        ttk.Label(
            root,
            text="A Windows utility for creating and refining Wii U Virtual Console artwork and reference metadata from supported source-game dumps and screenshots.",
            wraplength=540,
            justify="center",
        ).pack(fill="x", pady=(0, 12))

        notice = ScrolledText(root, wrap="word", height=14, padx=10, pady=8, font=("Segoe UI", 9), relief="solid", borderwidth=1)
        notice.pack(fill="both", expand=True)
        notice.insert("1.0", HELP_BY_TITLE["Credits & Notices"].strip())
        notice.configure(state="disabled")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(12, 0))
        docs_btn = ttk.Button(buttons, text="Open Documentation", command=lambda: parent.open_documentation("Getting Started"))
        docs_btn.pack(side="left")
        add_hover_tip(docs_btn, "Open the full built-in Documentation Center at Getting Started.")
        close_btn = ttk.Button(buttons, text="Close", command=self.destroy)
        close_btn.pack(side="right")
        add_hover_tip(close_btn, "Close the About window.")
        self.bind("<Escape>", lambda event: self.destroy())
        self.after(10, self.focus_force)

def run():
    app = App()
    app.mainloop()
