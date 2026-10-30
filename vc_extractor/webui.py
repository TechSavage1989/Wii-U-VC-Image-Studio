from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
import base64
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import webbrowser
from typing import Any, Iterable

from PIL import Image, ImageStat

from .boot_sound import BootSoundConversionError, convert_boot_sound_to_wav
from .config import APP_NAME, config_dir, default_config_dir, load_settings, save_settings, set_config_dir
from .emulators import (
    CaptureCancelled,
    EmulatorError,
    EmulatorSettings,
    capture_game,
    discover_core,
    dolphin_system_files_ready,
)
from .exporter import (
    ExportOptions,
    SCREENSHOT_ONLY_TEMPLATE,
    default_icon_template_for_system,
    default_shot_rect,
    export_metadata_only,
    export_selected,
    icon_template_choices,
    render_banners_from_source,
    render_icon_from_source,
)
from .help_docs import APP_VERSION, HELP_BY_TITLE, HELP_SECTIONS
from .updater import UpdateManager, acknowledge_health
from .metadata import GameMetadata, LibretroSettings, lookup_libretro, update_database_for_systems
from .models import GameJob
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
from .roms import ROM_EXTENSIONS, RomInfo, detect_rom
from .scoring import FrameSample, select_candidates, select_nds_candidates


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga"}
PREVIEW_NAMES = {
    "title": "title_screen.png",
    "icon": "Icon Image.png",
    "tv": "TV Image.png",
    "gamepad": "Gamepad Image.png",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "retroarch_path": "",
    "dolphin_path": "",
    "output_root": str(Path.home() / "Documents" / "UWUVCI Artwork"),
    "uwuvci_font_path": "",
    "warmup_seconds": 4.0,
    "duration_seconds": 45.0,
    "interval_seconds": 0.75,
    "candidate_count": 6,
    "screen_crop": "auto",
    "create_icon_image": True,
    "create_tv_image": True,
    "create_gamepad_image": True,
    "include_banner_metadata": True,
    "include_game_title": True,
    "include_release_date": True,
    "include_player_count": True,
    "automatic_repository_artwork": True,
    "repository_title_fallback": True,
    "repository_uncertain_matches": True,
    "libretro_artwork_enabled": True,
    "gamecube_artwork_enabled": True,
    "activity_log_dir": "",
    "activity_logging_enabled": False,
    "theme": "dark",
}


class UserFacingError(RuntimeError):
    pass


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser().absolute()


def _path_key(path: Path | str) -> str:
    return os.path.normcase(str(_safe_resolve(Path(path))))


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _web_root() -> Path:
    return _resource_root() / "vc_extractor" / "webui_assets"


def _mime_for_image(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


class WebUIApi:
    """Local application controller exposed to the bundled WebUI.

    All ROM processing remains in Python. JavaScript receives only serialized
    application state and explicitly requested preview or audio data.
    """

    def __init__(self, window=None):
        # pywebview recursively inspects every public attribute on js_api
        # objects while it builds the JavaScript bridge. The native Window
        # contains the GUI backend and cycles through a large object graph, so
        # exposing it as a window field can stall bridge creation at page load.
        self._window = window
        self._updates = UpdateManager(APP_VERSION)
        self._window_shown = False
        self._restarting = False
        self._health_args = None
        self._health_confirmed = False
        self.jobs: list[GameJob] = []
        self.selected_ids: list[str] = []
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._busy_kind = ""
        self._busy_message = ""
        self._cancel_event = threading.Event()
        self._progress_current = 0
        self._progress_total = 0
        self._notifications: list[dict[str, str]] = []
        self._activity_lines: list[str] = []
        self._log_revision = 0
        self._state_revision = 0
        self._preview_cache: dict[tuple[str, int, int, int, int], dict[str, Any]] = {}
        self._pending_auto_artwork: list[str] = []
        self._child_apis: list[Any] = []
        self._large_preview_window = None
        self._large_preview_api = None
        self._preview_panel_window = None
        self._preview_panel_api = None
        self._activity_window = None
        self._session_log_path: Path | None = None
        self._settings = self._load_settings()
        self.log(f"{APP_NAME} v{APP_VERSION} WebUI ready.")

    # ---------- API response and state helpers ----------

    @staticmethod
    def _ok(**payload) -> dict[str, Any]:
        return {"ok": True, **payload}

    @staticmethod
    def _error(message: str, **payload) -> dict[str, Any]:
        return {"ok": False, "error": str(message), **payload}

    def _touch(self, job: GameJob | None = None) -> None:
        self._state_revision += 1
        if job is not None:
            job.preview_revision += 1
        if len(self._preview_cache) > 96:
            self._preview_cache.clear()

    def _notify(self, level: str, title: str, message: str) -> None:
        self._notifications.append({"level": level, "title": title, "message": str(message)})
        self._notifications = self._notifications[-30:]
        self._touch()

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {str(message).strip()}"
        with self._lock:
            self._activity_lines.append(line)
            self._activity_lines = self._activity_lines[-1200:]
            self._log_revision += 1
            self._touch()
            if not self._settings.get("activity_logging_enabled"):
                return
            folder_text = str(self._settings.get("activity_log_dir", "") or "").strip()
            if not folder_text:
                return
            try:
                folder = Path(folder_text).expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                if self._session_log_path is None or self._session_log_path.parent != folder:
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    self._session_log_path = folder / f"Wii_U_VC_Image_Builder_{stamp}.log"
                with self._session_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
            except OSError:
                self._settings["activity_logging_enabled"] = False

    def _load_settings(self) -> dict[str, Any]:
        merged = dict(DEFAULT_SETTINGS)
        try:
            merged.update(load_settings())
        except Exception:
            pass
        if merged.get("screen_crop") == "full":
            merged["screen_crop"] = "auto"
        return merged

    def _persist_settings(self) -> None:
        save_settings(dict(self._settings))

    def _job(self, job_id: str) -> GameJob:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        raise UserFacingError("The selected game is no longer available.")

    def _selected_jobs(self) -> list[GameJob]:
        selected = set(self.selected_ids)
        return [job for job in self.jobs if job.job_id in selected]

    @staticmethod
    def _repository_icon_artwork(job: GameJob) -> RepositoryArtwork | None:
        return job.repository_icon_artwork or job.repository_artwork

    @staticmethod
    def _repository_tv_gamepad_artwork(job: GameJob) -> RepositoryArtwork | None:
        return job.repository_tv_gamepad_artwork or job.repository_artwork

    @classmethod
    def _job_has_repository_artwork(cls, job: GameJob) -> bool:
        return bool(cls._repository_icon_artwork(job) or cls._repository_tv_gamepad_artwork(job))

    @classmethod
    def _job_has_renderable_source(cls, job: GameJob) -> bool:
        if job.selected is not None and Path(job.selected.path).is_file():
            return True
        for artwork in (cls._repository_icon_artwork(job), cls._repository_tv_gamepad_artwork(job)):
            if artwork is None:
                continue
            for path in (artwork.source_image_path, artwork.icon_path, artwork.tv_path, artwork.gamepad_path):
                if path is not None and Path(path).is_file():
                    return True
        return False

    @staticmethod
    def _recommended_screen_crop(job: GameJob) -> str | None:
        return "full" if job.image_imported or (job.selected and job.selected.screen_label) else None

    @staticmethod
    def _source_area_choice(job: GameJob) -> str:
        value = str(getattr(job.info, "source_area_selection", "auto") or "auto").strip().lower()
        return value if value in {"auto", "full", "top", "bottom", "left", "right"} else "auto"

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
        if chosen and chosen.screen_label and not job.image_imported and not repository_source:
            return "full"
        return choice

    def _source_display(self, job: GameJob) -> str:
        value = "ROM + Image" if job.image_imported else job.source_kind
        if job.info.system in {"Wii", "WiiWare"} and job.tv_gamepad_source != "Selected Screenshot":
            value += " + " + job.tv_gamepad_source
        if self._job_has_repository_artwork(job):
            value += " + Repository Art"
        return value

    def _preview_paths(self, job: GameJob) -> dict[str, Path | None]:
        result: dict[str, Path | None] = {key: None for key in PREVIEW_NAMES}
        if job.output_dir:
            for key, filename in PREVIEW_NAMES.items():
                candidate = Path(job.output_dir) / filename
                if candidate.is_file():
                    result[key] = candidate
        if result["title"] is None and job.selected and Path(job.selected.path).is_file():
            result["title"] = Path(job.selected.path)
        return result

    def _sound_options(self, job: GameJob) -> tuple[list[dict[str, Any]], dict[str, BootSound]]:
        sounds: list[BootSound] = []
        seen: set[str] = set()
        for sound in (
            job.boot_sound,
            self._stored_game_boot_sound(job.info),
            self._local_console_boot_sound(job.info.system),
            *built_in_boot_sounds(job.info.system),
            *job.boot_sound_candidates,
        ):
            if sound is None:
                continue
            key = _path_key(sound.path)
            if key in seen:
                continue
            seen.add(key)
            sounds.append(sound)
        mapping: dict[str, BootSound] = {}
        options: list[dict[str, Any]] = []
        active_key = _path_key(job.boot_sound.path) if job.boot_sound else ""
        for sound in sounds:
            token = hashlib.sha256(
                (str(sound.path) + "|" + sound.provider_name + "|" + sound.sound_kind).encode("utf-8", errors="ignore")
            ).hexdigest()[:20]
            mapping[token] = sound
            options.append({
                "token": token,
                "label": sound.display_label,
                "active": _path_key(sound.path) == active_key,
                "kind": sound.sound_kind,
                "provider": sound.provider_name,
            })
        return options, mapping

    def _serialize_artwork(self, job: GameJob, artwork: RepositoryArtwork, index: int) -> dict[str, Any]:
        icon = self._repository_icon_artwork(job)
        banners = self._repository_tv_gamepad_artwork(job)
        assets = []
        if artwork.source_image_path:
            assets.append("source")
        if artwork.icon_path:
            assets.append("icon")
        if artwork.tv_path:
            assets.append("tv")
        if artwork.gamepad_path:
            assets.append("gamepad")
        return {
            "index": index,
            "provider": artwork.provider_name,
            "provider_url": artwork.provider_url,
            "product_code": artwork.product_code,
            "matched_title": artwork.matched_title,
            "match_method": artwork.match_method,
            "confidence": artwork.confidence,
            "source_kind": artwork.source_image_kind or "Finished Wii U artwork",
            "available_outputs": list(artwork.available_outputs),
            "assets": assets,
            "assigned_icon": artwork == icon,
            "assigned_tv_gamepad": artwork == banners,
            "from_cache": artwork.from_cache,
        }

    def _serialize_job(self, job: GameJob) -> dict[str, Any]:
        previews = self._preview_paths(job)
        sound_options, _ = self._sound_options(job)
        active_sound = next((entry for entry in sound_options if entry["active"]), None)
        frame_choices = [
            {"label": label, "value": filename}
            for label, filename in icon_template_choices(job.info.system)
        ]
        return {
            "id": job.job_id,
            "checked": job.capture_checked,
            "file_name": job.info.path.name,
            "file_path": str(job.info.path),
            "source": self._source_display(job),
            "name": job.info.display_name or job.info.path.stem,
            "system": job.info.system,
            "year": job.info.release_year or "",
            "players": job.info.players or "",
            "product_code": job.info.product_code or "",
            "region": job.info.region or "",
            "publisher": job.info.publisher or "",
            "developer": job.info.developer or "",
            "metadata_source": job.info.metadata_source or "",
            "status": job.status,
            "output_dir": str(job.output_dir) if job.output_dir else "",
            "candidate_count": len(job.candidates),
            "candidates": [
                {
                    "index": index,
                    "timestamp": candidate.timestamp,
                    "score": candidate.score,
                    "screen_label": candidate.screen_label,
                    "selected": candidate is job.selected,
                }
                for index, candidate in enumerate(job.candidates)
            ],
            "artwork_match_count": len(job.repository_artwork_candidates),
            "preview_revision": job.preview_revision,
            "preview_available": {key: bool(path) for key, path in previews.items()},
            "icon_template": job.info.icon_template or SCREENSHOT_ONLY_TEMPLATE,
            "icon_frame_choices": frame_choices,
            "source_area_selection": self._source_area_choice(job),
            "tv_gamepad_source": job.tv_gamepad_source,
            "tv_gamepad_sources": self._available_tv_gamepad_sources(job),
            "placement": {
                "x": job.info.shot_x or default_shot_rect(job.info.system)[0],
                "y": job.info.shot_y or default_shot_rect(job.info.system)[1],
                "width": job.info.shot_width or default_shot_rect(job.info.system)[2],
                "height": job.info.shot_height or default_shot_rect(job.info.system)[3],
            },
            "framing": self._crop_state_for_info(job.info),
            "boot_sound_options": sound_options,
            "active_boot_sound": active_sound,
            "has_boot_sound": job.boot_sound is not None,
            "artwork_matches": [
                self._serialize_artwork(job, artwork, index)
                for index, artwork in enumerate(job.repository_artwork_candidates)
            ],
        }

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            known_ids = {job.job_id for job in self.jobs}
            self.selected_ids = [job_id for job_id in self.selected_ids if job_id in known_ids]
            notifications = list(self._notifications)
            self._notifications.clear()
            return {
                "app_name": APP_NAME,
                "version": APP_VERSION,
                "state_revision": self._state_revision,
                "log_revision": self._log_revision,
                "jobs": [self._serialize_job(job) for job in self.jobs],
                "selected_ids": list(self.selected_ids),
                "settings": dict(self._settings),
                "configuration_folder": str(config_dir()),
                "busy": bool(self._busy_kind),
                "busy_kind": self._busy_kind,
                "busy_message": self._busy_message,
                "progress": {
                    "current": self._progress_current,
                    "total": self._progress_total,
                },
                "activity": list(self._activity_lines),
                "notifications": notifications,
                "help_topics": [title for title, _ in HELP_SECTIONS],
                "preview_detached": self._preview_panel_window is not None,
                "activity_detached": self._activity_window is not None,
                "updates": self._updates.state(),
            }

    def bootstrap(self) -> dict[str, Any]:
        return self._ok(state=self.get_state())

    def check_updates(self, reason: str = "manual") -> dict[str, Any]:
        if reason not in {"startup", "manual", "interval", "focus"}:
            return self._error("Invalid update check reason")
        started = self._updates.request_check(reason)
        return self._ok(started=started, update=self._updates.state())

    def download_update(self) -> dict[str, Any]:
        started = self._updates.request_download()
        return self._ok(started=started, update=self._updates.state())

    def install_update(self) -> dict[str, Any]:
        try:
            with self._lock:
                if self._busy_kind or (self._worker and self._worker.is_alive()) or self._pending_auto_artwork:
                    raise UserFacingError("Finish the active operation before installing an update.")
                if self._restarting:
                    raise UserFacingError("An update restart is already in progress.")
                self._persist_settings()
                self._updates.install()
                self._restarting = True
            def close_windows():
                for child in (self._large_preview_window, self._preview_panel_window, self._activity_window):
                    if child:
                        child.destroy()
                self._window.destroy()
            threading.Timer(0.5, close_windows).start()
            return self._ok()
        except Exception as exc:
            return self._error(str(exc))

    def report_ui_ready(self) -> dict[str, Any]:
        if not self._window_shown:
            return self._ok(healthy=False)
        if self._health_args and not self._health_confirmed:
            self._health_confirmed = acknowledge_health(*self._health_args, APP_VERSION)
        return self._ok(healthy=True)

    def set_selection(self, job_ids: list[str] | None) -> dict[str, Any]:
        with self._lock:
            known = {job.job_id for job in self.jobs}
            self.selected_ids = [str(value) for value in (job_ids or []) if str(value) in known]
            self._touch()
        return self._ok(selected_ids=list(self.selected_ids))

    # ---------- Native dialogs and filesystem helpers ----------

    def _file_dialog(
        self,
        kind: str,
        *,
        allow_multiple: bool = False,
        file_types: tuple[str, ...] = (),
        directory: str = "",
    ) -> tuple[str, ...]:
        if self._window is None:
            return ()
        try:
            import webview

            dialog_type = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
            result = self._window.create_file_dialog(
                dialog_type,
                directory=directory,
                allow_multiple=allow_multiple,
                file_types=file_types,
            )
            return tuple(str(path) for path in (result or ()))
        except Exception as exc:
            raise UserFacingError(f"The file chooser could not be opened: {exc}") from exc

    @staticmethod
    def _open_path(path: Path) -> None:
        path = _safe_resolve(path)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return
        command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def choose_retroarch(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=("RetroArch (retroarch.exe)", "Executables (*.exe)", "All files (*.*)"),
            )
            if paths:
                return self.update_settings({"retroarch_path": paths[0]})
            return self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def choose_dolphin(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=("Dolphin (Dolphin.exe)", "Executables (*.exe)", "All files (*.*)"),
            )
            if paths:
                return self.update_settings({"dolphin_path": paths[0]})
            return self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def choose_output_folder(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog("folder", directory=str(self._settings.get("output_root", "")))
            if paths:
                return self.update_settings({"output_root": paths[0]})
            return self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def open_configuration_folder(self) -> dict[str, Any]:
        try:
            path = config_dir()
            path.mkdir(parents=True, exist_ok=True)
            self._open_path(path)
            return self._ok(path=str(path))
        except Exception as exc:
            return self._error(f"The configuration folder could not be opened: {exc}")

    def open_repository_cache(self) -> dict[str, Any]:
        try:
            path = config_dir() / "repository_artwork_cache"
            path.mkdir(parents=True, exist_ok=True)
            self._open_path(path)
            return self._ok(path=str(path))
        except Exception as exc:
            return self._error(f"The repository cache could not be opened: {exc}")

    def open_output(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                path = Path(job.output_dir) if job.output_dir else None
            if path is None or not path.exists():
                raise UserFacingError("The selected game does not have an output folder yet.")
            self._open_path(path)
            return self._ok(path=str(path))
        except Exception as exc:
            return self._error(exc)

    def open_metadata(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                path = Path(job.output_dir) / "metadata.txt" if job.output_dir else None
            if path is None or not path.is_file():
                raise UserFacingError("metadata.txt is not available for the selected game yet.")
            self._open_path(path)
            return self._ok(path=str(path))
        except Exception as exc:
            return self._error(exc)

    # ---------- Settings ----------

    def update_settings(self, values: dict[str, Any] | None) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        incoming = values or {}
        previous = dict(self._settings)
        try:
            with self._lock:
                for key, value in incoming.items():
                    if key not in allowed:
                        continue
                    if key in {"warmup_seconds", "duration_seconds", "interval_seconds"}:
                        value = float(value)
                    elif key == "candidate_count":
                        value = int(value)
                    elif isinstance(DEFAULT_SETTINGS[key], bool):
                        value = value.strip().casefold() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)
                    else:
                        value = str(value)
                    self._settings[key] = value
                self._validate_general_settings()
                changed = {key for key in incoming if key in allowed and self._settings.get(key) != previous.get(key)}
                render_keys = {
                    "create_icon_image",
                    "create_tv_image",
                    "create_gamepad_image",
                    "include_banner_metadata",
                    "include_game_title",
                    "include_release_date",
                    "include_player_count",
                    "uwuvci_font_path",
                }
                if changed & render_keys:
                    for job in self._selected_jobs():
                        if self._job_has_renderable_source(job):
                            self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                            job.status = "Complete - output settings updated"
                self._persist_settings()
                self._touch()
            return self._ok(settings=dict(self._settings))
        except Exception as exc:
            with self._lock:
                self._settings = previous
            return self._error(exc)

    def _validate_general_settings(self) -> None:
        if float(self._settings["warmup_seconds"]) < 0:
            raise UserFacingError("Warm-up time cannot be negative.")
        if float(self._settings["duration_seconds"]) < 2:
            raise UserFacingError("Scan duration must be at least 2 seconds.")
        if float(self._settings["interval_seconds"]) < 0.2:
            raise UserFacingError("Sample interval must be at least 0.2 seconds.")
        if not 1 <= int(self._settings["candidate_count"]) <= 20:
            raise UserFacingError("Candidates to keep must be between 1 and 20.")
        if str(self._settings.get("screen_crop", "auto")) not in {"auto", "full", "top", "bottom", "left", "right"}:
            self._settings["screen_crop"] = "auto"

    def choose_configuration_folder(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog("folder", directory=str(config_dir()))
            if not paths:
                return self._ok(cancelled=True)
            selected = set_config_dir(Path(paths[0]), migrate=True)
            self._persist_settings()
            self.log(f"Configuration folder changed to: {selected}")
            return self._ok(path=str(selected), state=self.get_state())
        except Exception as exc:
            return self._error(exc)

    def use_default_configuration_folder(self) -> dict[str, Any]:
        try:
            selected = set_config_dir(default_config_dir(), migrate=True)
            self._persist_settings()
            self.log(f"Configuration folder restored to: {selected}")
            return self._ok(path=str(selected), state=self.get_state())
        except Exception as exc:
            return self._error(exc)

    def choose_activity_log_folder(self) -> dict[str, Any]:
        try:
            result = self.pick_activity_log_folder()
            if not result.get("ok") or result.get("cancelled"):
                return result
            return self.update_settings({"activity_log_dir": result["path"], "activity_logging_enabled": True})
        except Exception as exc:
            return self._error(exc)

    def pick_activity_log_folder(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog("folder", directory=str(self._settings.get("activity_log_dir", "")))
            return self._ok(path=paths[0]) if paths else self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def choose_artwork_font(self) -> dict[str, Any]:
        try:
            result = self.pick_artwork_font()
            if not result.get("ok") or result.get("cancelled"):
                return result
            return self.update_settings({"uwuvci_font_path": result["path"]})
        except Exception as exc:
            return self._error(exc)

    def pick_artwork_font(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=("Font files (*.otf;*.ttf)", "OpenType fonts (*.otf)", "TrueType fonts (*.ttf)"),
            )
            return self._ok(path=paths[0]) if paths else self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    # ---------- ROM list ----------

    @staticmethod
    def _collect_paths(paths: Iterable[str | Path]) -> tuple[list[Path], int]:
        collected: list[Path] = []
        skipped = 0
        seen: set[str] = set()
        for raw in paths:
            path = Path(str(raw)).expanduser()
            if path.is_dir():
                candidates = sorted(
                    (item for item in path.rglob("*") if item.is_file() and item.suffix.casefold() in ROM_EXTENSIONS),
                    key=lambda item: str(item).casefold(),
                )
                if not candidates:
                    skipped += 1
            else:
                candidates = [path]
            for candidate in candidates:
                if not candidate.is_file() or candidate.suffix.casefold() not in ROM_EXTENSIONS:
                    skipped += 1
                    continue
                key = _path_key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(candidate)
        return collected, skipped

    def choose_add_roms(self) -> dict[str, Any]:
        patterns = ";".join(f"*{extension}" for extension in sorted(ROM_EXTENSIONS))
        try:
            paths = self._file_dialog(
                "file",
                allow_multiple=True,
                file_types=(f"Supported ROMs ({patterns})", "All files (*.*)"),
            )
            return self.add_paths(list(paths)) if paths else self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def choose_add_folder(self) -> dict[str, Any]:
        try:
            paths = self._file_dialog("folder")
            return self.add_paths(list(paths)) if paths else self._ok(cancelled=True)
        except Exception as exc:
            return self._error(exc)

    def add_dropped_paths(self, paths: list[str] | None) -> dict[str, Any]:
        return self.add_paths(paths or [])

    def add_paths(self, paths: Iterable[str | Path]) -> dict[str, Any]:
        try:
            candidates, skipped = self._collect_paths(paths)
            with self._lock:
                existing = {_path_key(job.info.path) for job in self.jobs}
                added: list[GameJob] = []
                for path in candidates:
                    if _path_key(path) in existing:
                        continue
                    info = detect_rom(path)
                    if info is None:
                        skipped += 1
                        self.log(f"Skipped unsupported or ambiguous file: {path.name}")
                        continue
                    info.icon_template = default_icon_template_for_system(info.system)
                    job = GameJob(info=info, source_kind="ROM", status="Metadata queued")
                    stored = self._stored_game_boot_sound(info)
                    console = self._local_console_boot_sound(info.system) if stored is None else None
                    if stored or console:
                        job.boot_sound = stored or console
                        job.boot_sound_candidates.append(job.boot_sound)
                    self.jobs.append(job)
                    added.append(job)
                    existing.add(_path_key(path))
                    self._write_metadata_now(job)
                if added:
                    self.selected_ids = [added[0].job_id]
                    self._touch()
                    self.log(f"Added {len(added)} ROM(s). Metadata lookup started automatically.")
                elif skipped:
                    self._notify("warning", "No ROMs added", "No supported new ROM files were found in the dropped or selected items.")
            if added:
                self._start_metadata([job.job_id for job in added], automatic_artwork=True)
            return self._ok(added=len(added), skipped=skipped, selected_ids=list(self.selected_ids))
        except Exception as exc:
            return self._error(f"ROMs could not be added: {exc}")

    def toggle_capture_checked(self, job_id: str, checked: bool) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                job.capture_checked = bool(checked)
                self._touch()
            return self._ok(checked=job.capture_checked)
        except Exception as exc:
            return self._error(exc)

    def toggle_selected_capture(self) -> dict[str, Any]:
        with self._lock:
            selected = [job for job in self._selected_jobs() if job.source_kind == "ROM"]
            if not selected:
                return self._error("Select at least one ROM first.")
            checked = not all(job.capture_checked for job in selected)
            for job in selected:
                job.capture_checked = checked
            self._touch()
            return self._ok(checked=checked)

    def check_all(self) -> dict[str, Any]:
        with self._lock:
            for job in self.jobs:
                if job.source_kind == "ROM":
                    job.capture_checked = True
            self._touch()
        return self._ok()

    def clear_checks(self) -> dict[str, Any]:
        with self._lock:
            for job in self.jobs:
                job.capture_checked = False
            self._touch()
        return self._ok()

    def _ensure_removal_allowed(self) -> None:
        if self._busy_kind:
            raise UserFacingError("Wait for the current capture, metadata, or artwork operation to finish before removing ROMs.")

    def remove_job(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                self._ensure_removal_allowed()
                job = self._job(job_id)
                index = self.jobs.index(job)
                self.jobs.remove(job)
                self.selected_ids = [value for value in self.selected_ids if value != job_id]
                if not self.selected_ids and self.jobs:
                    self.selected_ids = [self.jobs[min(index, len(self.jobs) - 1)].job_id]
                self.log(f"Removed from ROM list: {job.info.display_name or job.info.path.name}. The source ROM was not deleted.")
                self._touch()
            return self._ok(removed=1, selected_ids=list(self.selected_ids))
        except Exception as exc:
            return self._error(exc)

    def remove_checked(self) -> dict[str, Any]:
        try:
            with self._lock:
                self._ensure_removal_allowed()
                removed = [job for job in self.jobs if job.source_kind == "ROM" and job.capture_checked]
                if not removed:
                    raise UserFacingError("No checked ROMs are available to remove.")
                removed_ids = {job.job_id for job in removed}
                self.jobs = [job for job in self.jobs if job.job_id not in removed_ids]
                self.selected_ids = [job_id for job_id in self.selected_ids if job_id not in removed_ids]
                if not self.selected_ids and self.jobs:
                    self.selected_ids = [self.jobs[0].job_id]
                for job in removed:
                    self.log(f"Removed from ROM list: {job.info.display_name or job.info.path.name}. The source ROM was not deleted.")
                self._touch()
            return self._ok(removed=len(removed), selected_ids=list(self.selected_ids))
        except Exception as exc:
            return self._error(exc)

    # ---------- Rendering and preview ----------

    def _export_options(self, screen_crop: str | None = None) -> ExportOptions:
        font_text = str(self._settings.get("uwuvci_font_path", "") or "").strip()
        return ExportOptions(
            output_root=Path(str(self._settings.get("output_root", "")).strip()),
            screen_crop=screen_crop if screen_crop is not None else str(self._settings.get("screen_crop", "auto")),
            font_path=Path(font_text) if font_text else None,
            create_icon=bool(self._settings.get("create_icon_image", True)),
            create_tv=bool(self._settings.get("create_tv_image", True)),
            create_gamepad=bool(self._settings.get("create_gamepad_image", True)),
            include_banner_metadata=bool(self._settings.get("include_banner_metadata", True)),
            include_game_title=bool(self._settings.get("include_game_title", True)),
            include_release_date=bool(self._settings.get("include_release_date", True)),
            include_player_count=bool(self._settings.get("include_player_count", True)),
        )

    @staticmethod
    def _validate_image(path: Path, *, banner: bool = False) -> None:
        if path.suffix.casefold() not in IMAGE_EXTENSIONS:
            raise UserFacingError("Choose a PNG, JPG, JPEG, BMP, WebP, or TGA image.")
        if not path.is_file() or path.stat().st_size <= 0:
            raise UserFacingError("The selected image is empty or unavailable.")
        if path.stat().st_size > 24 * 1024 * 1024:
            raise UserFacingError("The selected image exceeds the 24 MB safety limit.")
        try:
            with Image.open(path) as image:
                image.verify()
            if banner:
                with Image.open(path) as image:
                    rgba = image.convert("RGBA")
                    if rgba.width < 32 or rgba.height < 16 or rgba.getbbox() is None:
                        raise UserFacingError("The selected image does not contain usable image pixels.")
                    thumb = rgba.convert("RGB")
                    thumb.thumbnail((128, 128), Image.Resampling.LANCZOS)
                    stat = ImageStat.Stat(thumb)
                    dynamic = sum(max(0, high - low) for low, high in thumb.getextrema())
                    if dynamic < 18 or sum(stat.var) < 8:
                        raise UserFacingError("The selected image is too uniform to use as TV/Gamepad artwork.")
        except UserFacingError:
            raise
        except Exception as exc:
            raise UserFacingError(f"The selected image could not be decoded: {exc}") from exc

    def _available_tv_gamepad_sources(self, job: GameJob) -> list[str]:
        values = ["Selected Screenshot"]
        path = job.imported_banner_image_path
        if path and Path(path).is_file():
            try:
                self._validate_image(Path(path), banner=True)
                values.append("Custom Image")
            except UserFacingError:
                pass
        return values

    def _job_banner_sample(self, job: GameJob) -> FrameSample | None:
        if job.tv_gamepad_source != "Custom Image" or not job.imported_banner_image_path:
            return None
        path = Path(job.imported_banner_image_path)
        try:
            self._validate_image(path, banner=True)
        except UserFacingError:
            return None
        return FrameSample(path, 0.0, score=1.0)

    def _export_job(
        self,
        job: GameJob,
        sample: FrameSample | None = None,
        candidates: list[FrameSample] | None = None,
        screen_crop: str | None = None,
        options: ExportOptions | None = None,
    ) -> Path:
        use_options = options or self._export_options(screen_crop)
        if options is not None and screen_crop is not None and options.screen_crop != screen_crop:
            use_options = replace(options, screen_crop=screen_crop)
        icon_artwork = self._repository_icon_artwork(job)
        banner_artwork = self._repository_tv_gamepad_artwork(job)
        shared_source = job.repository_artwork.source_image_path if job.repository_artwork else None
        repository_source = False
        role_fallback = False
        if shared_source and Path(shared_source).is_file():
            chosen = FrameSample(Path(shared_source), 0.0, score=1.0)
            repository_source = True
            use_options = replace(
                use_options,
                screen_crop=self._screen_crop_for_source(job, chosen, "full", repository_source=True),
            )
            use_candidates = [chosen]
            banner_sample = None
        else:
            chosen = sample or job.selected
            use_candidates = candidates or job.candidates or ([chosen] if chosen else [])
            if chosen is None:
                fallback = next(
                    (
                        artwork
                        for artwork in (banner_artwork, icon_artwork)
                        if artwork is not None
                        and artwork.source_image_path is not None
                        and Path(artwork.source_image_path).is_file()
                    ),
                    None,
                )
                if fallback:
                    chosen = FrameSample(Path(fallback.source_image_path), 0.0, score=1.0)
                    use_candidates = [chosen]
                    repository_source = True
                    role_fallback = True
            choice = self._source_area_choice(job)
            if job.info.system == "Nintendo DS" and choice in {"top", "bottom"} and chosen and chosen.screen_label:
                matched = next(
                    (candidate for candidate in use_candidates if str(candidate.screen_label or "").casefold().startswith(choice)),
                    None,
                )
                if matched:
                    chosen = matched
            use_options = replace(
                use_options,
                screen_crop=self._screen_crop_for_source(
                    job,
                    chosen,
                    use_options.screen_crop,
                    repository_source=repository_source,
                ),
            )
            banner_sample = None if repository_source else self._job_banner_sample(job)
        render_options = use_options
        if role_fallback:
            render_options = replace(
                use_options,
                create_icon=use_options.create_icon and icon_artwork is not None,
                create_tv=use_options.create_tv and banner_artwork is not None,
                create_gamepad=use_options.create_gamepad and banner_artwork is not None,
            )
        if chosen is None:
            output = export_metadata_only(job.info, use_options, None)
        else:
            output = export_selected(job.info, chosen, use_candidates, render_options, banner_sample=banner_sample)
        self._apply_repository_art_outputs(job, output, use_options)
        self._apply_boot_sound_output(job, output)
        job.output_dir = output
        self._touch(job)
        return output

    def _write_metadata_now(self, job: GameJob) -> None:
        try:
            previous = job.output_dir
            if self._job_has_renderable_source(job):
                job.output_dir = self._export_job(job)
            else:
                job.output_dir = export_metadata_only(job.info, self._export_options(), None)
                self._apply_boot_sound_output(job, Path(job.output_dir))
                self._touch(job)
            if previous and previous != job.output_dir:
                try:
                    files = list(Path(previous).iterdir()) if Path(previous).exists() else []
                    if files and all(item.name == "metadata.txt" for item in files):
                        for item in files:
                            item.unlink(missing_ok=True)
                        Path(previous).rmdir()
                except OSError:
                    pass
        except Exception as exc:
            self.log(f"Could not write metadata.txt for {job.info.path.name}: {exc}")

    @staticmethod
    def _fit_child_window_size(
        source_size: tuple[int, int] | None,
        screen_size: tuple[int, int],
        *,
        fallback: tuple[int, int],
        minimum: tuple[int, int],
        chrome: tuple[int, int] = (40, 130),
    ) -> tuple[int, int]:
        """Fit a child window within the available screen with safe margins."""
        screen_width = max(360, int(screen_size[0]))
        screen_height = max(280, int(screen_size[1]))
        max_width = max(320, screen_width - 80)
        max_height = max(240, screen_height - 120)
        min_width = min(max_width, max(320, int(minimum[0])))
        min_height = min(max_height, max(240, int(minimum[1])))
        if source_size:
            desired_width = int(source_size[0]) + int(chrome[0])
            desired_height = int(source_size[1]) + int(chrome[1])
        else:
            desired_width, desired_height = fallback
        return (
            min(max_width, max(min_width, desired_width)),
            min(max_height, max(min_height, desired_height)),
        )

    def _active_webview_screen(self):
        """Return the screen containing the main window when pywebview exposes it."""
        try:
            import webview

            screens = list(webview.screens)
            if not screens:
                return None
            if self._window is None:
                return screens[0]
            center_x = int(self._window.x) + int(self._window.width) // 2
            center_y = int(self._window.y) + int(self._window.height) // 2
            return next(
                (
                    screen
                    for screen in screens
                    if screen.x <= center_x < screen.x + screen.width
                    and screen.y <= center_y < screen.y + screen.height
                ),
                screens[0],
            )
        except Exception:
            return None

    def _child_window_geometry(
        self,
        source_size: tuple[int, int] | None,
        *,
        fallback: tuple[int, int],
        minimum: tuple[int, int],
        chrome: tuple[int, int] = (40, 130),
    ) -> tuple[int, int, Any | None]:
        screen = self._active_webview_screen()
        screen_size = (int(screen.width), int(screen.height)) if screen is not None else (1920, 1080)
        width, height = self._fit_child_window_size(
            source_size,
            screen_size,
            fallback=fallback,
            minimum=minimum,
            chrome=chrome,
        )
        return width, height, screen

    def get_preview(self, job_id: str, key: str, max_width: int = 960, max_height: int = 600) -> dict[str, Any]:
        try:
            max_width = max(64, min(int(max_width), 3840))
            max_height = max(64, min(int(max_height), 2160))
            with self._lock:
                job = self._job(job_id)
                path = self._preview_paths(job).get(str(key))
                revision = job.preview_revision
            if path is None or not path.is_file():
                raise UserFacingError("This preview is not available yet.")
            stat = path.stat()
            cache_key = (_path_key(path), stat.st_mtime_ns, stat.st_size, max_width, max_height)
            cached = self._preview_cache.get(cache_key)
            if cached:
                return self._ok(**cached, revision=revision)
            with Image.open(path) as source:
                image = source.convert("RGBA")
                source_size = image.size
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
            payload = {
                "data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
                "width": image.width,
                "height": image.height,
                "source_width": source_size[0],
                "source_height": source_size[1],
                "file_name": path.name,
            }
            self._preview_cache[cache_key] = payload
            return self._ok(**payload, revision=revision)
        except Exception as exc:
            return self._error(exc)

    def open_preview_window(self, job_id: str, key: str) -> dict[str, Any]:
        """Open or update the single fit-to-window enlarged preview."""
        try:
            key = str(key)
            if key not in PREVIEW_NAMES:
                raise UserFacingError("The requested preview type is unavailable.")
            with self._lock:
                job = self._job(job_id)
                path = self._preview_paths(job).get(key)
                if path is None or not path.is_file():
                    raise UserFacingError("This preview is not available yet.")
                game_name = job.info.display_name or job.info.path.stem
            label = {
                "title": "Title Screen",
                "icon": "Icon Image",
                "tv": "TV Image",
                "gamepad": "Gamepad Image",
            }[key]
            title = f"{game_name} - {label}"
            if self._large_preview_window is not None and self._large_preview_api is not None:
                try:
                    self._large_preview_api._select(job_id, key)
                    self._large_preview_window.set_title(title)
                    self._large_preview_window.restore()
                    self._large_preview_window.show()
                    return self._ok(opened=True, existing=True)
                except Exception:
                    self._forget_child_api(self._large_preview_api)
                    self._large_preview_window = None
                    self._large_preview_api = None
            page = _web_root() / "preview_window.html"
            if not page.is_file():
                raise UserFacingError("The detached preview interface is missing from this build.")
            import webview

            child_api = PreviewWindowApi(self, job_id, key)
            self._child_apis.append(child_api)
            width, height, screen = self._child_window_geometry(
                None,
                fallback=(10000, 10000),
                minimum=(720, 480),
                chrome=(0, 0),
            )
            child = webview.create_window(
                title,
                str(page),
                js_api=child_api,
                width=width,
                height=height,
                screen=screen,
                min_size=(min(720, width), min(480, height)),
                resizable=True,
                background_color="#080d14",
                text_select=True,
            )
            child_api._window = child
            self._large_preview_window = child
            self._large_preview_api = child_api

            def on_closed():
                with self._lock:
                    if self._large_preview_window is child:
                        self._large_preview_window = None
                        self._large_preview_api = None
                self._forget_child_api(child_api)

            child.events.closed += on_closed
            return self._ok(opened=True)
        except Exception as exc:
            return self._error(exc)

    def open_preview_panel_window(self) -> dict[str, Any]:
        """Detach the complete four-image preview panel into one native window."""
        try:
            if self._preview_panel_window is not None:
                try:
                    self._preview_panel_window.restore()
                    self._preview_panel_window.show()
                    return self._ok(opened=True, existing=True)
                except Exception:
                    self._preview_panel_window = None
                    self._preview_panel_api = None
            page = _web_root() / "preview_panel_window.html"
            if not page.is_file():
                raise UserFacingError("The detached preview panel is missing from this build.")
            import webview

            child_api = PreviewPanelWindowApi(self)
            self._child_apis.append(child_api)
            width, height, screen = self._child_window_geometry(
                None,
                fallback=(1120, 780),
                minimum=(620, 420),
                chrome=(20, 100),
            )
            child = webview.create_window(
                f"{APP_NAME} - Selected Preview",
                str(page),
                js_api=child_api,
                width=width,
                height=height,
                screen=screen,
                min_size=(min(620, width), min(420, height)),
                resizable=True,
                background_color="#080d14",
                text_select=True,
            )
            child_api._window = child
            self._preview_panel_window = child
            self._preview_panel_api = child_api
            self._touch()

            def on_closed():
                with self._lock:
                    if self._preview_panel_window is child:
                        self._preview_panel_window = None
                        self._preview_panel_api = None
                        self._touch()
                self._forget_child_api(child_api)

            child.events.closed += on_closed
            return self._ok(opened=True)
        except Exception as exc:
            return self._error(exc)

    def reattach_preview_panel(self) -> dict[str, Any]:
        """Close the detached preview panel and restore its main-window dock."""
        try:
            with self._lock:
                child = self._preview_panel_window
                child_api = self._preview_panel_api
                self._preview_panel_window = None
                self._preview_panel_api = None
                self._touch()
            if child_api is not None:
                self._forget_child_api(child_api)
            if child is not None:
                child.destroy()
            return self._ok(reattached=True)
        except Exception as exc:
            return self._error(exc)

    def open_selected_framing_controls(self) -> dict[str, Any]:
        """Bring the main window forward and open framing for its selected game."""
        try:
            with self._lock:
                if len(self._selected_jobs()) != 1:
                    raise UserFacingError("Select exactly one game before adjusting its images.")
                window = self._window
            if window is None:
                raise UserFacingError("The main application window is unavailable.")
            window.restore()
            window.show()
            window.evaluate_js(
                "window.openFramingFromDetachedPreview && window.openFramingFromDetachedPreview();"
            )
            return self._ok(opened=True)
        except Exception as exc:
            return self._error(exc)

    def _forget_child_api(self, child_api: Any) -> None:
        try:
            self._child_apis.remove(child_api)
        except ValueError:
            pass

    def choose_import_screenshot(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                self._job(job_id)
            paths = self._file_dialog(
                "file",
                file_types=("Image files (*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tga)", "All files (*.*)"),
            )
            if not paths:
                return self._ok(cancelled=True)
            return self.import_screenshot(job_id, paths[0])
        except Exception as exc:
            return self._error(exc)

    def import_screenshot(self, job_id: str, path_text: str) -> dict[str, Any]:
        try:
            path = Path(path_text)
            self._validate_image(path)
            with self._lock:
                job = self._job(job_id)
                if job.source_kind != "ROM":
                    raise UserFacingError("Screenshot import requires a loaded ROM.")
                sample = FrameSample(path, 0.0, score=1.0)
                job.candidates = [sample]
                job.selected = sample
                job.image_imported = True
                previous_artwork = (
                    job.repository_artwork,
                    job.repository_icon_artwork,
                    job.repository_tv_gamepad_artwork,
                )
                job.repository_artwork = None
                job.repository_icon_artwork = None
                job.repository_tv_gamepad_artwork = None
                job.prefer_user_source = True
                job.status = "Building from imported screenshot"
                try:
                    job.output_dir = self._export_job(job, sample=sample, candidates=[sample], screen_crop="full")
                except Exception:
                    job.repository_artwork, job.repository_icon_artwork, job.repository_tv_gamepad_artwork = previous_artwork
                    raise
                job.status = "Complete - imported screenshot"
                self.log(f"Imported screenshot for {job.info.display_name or job.info.path.stem}: {path.name}")
                if any(previous_artwork):
                    self.log("The imported screenshot replaced downloaded artwork as the active source. The download remains cached.")
                self._touch(job)
            return self._ok(job_id=job_id)
        except Exception as exc:
            return self._error(exc)

    def choose_import_banner(self, job_id: str) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=("Image files (*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tga)", "All files (*.*)"),
            )
            if not paths:
                return self._ok(cancelled=True)
            return self.import_banner(job_id, paths[0])
        except Exception as exc:
            return self._error(exc)

    def import_banner(self, job_id: str, path_text: str) -> dict[str, Any]:
        try:
            path = Path(path_text)
            self._validate_image(path, banner=True)
            with self._lock:
                job = self._job(job_id)
                if job.info.system not in {"Wii", "WiiWare"}:
                    raise UserFacingError("A separate TV/Gamepad source is available only for Wii and WiiWare games.")
                job.imported_banner_image_path = path
                job.tv_gamepad_source = "Custom Image"
                job.banner_image_note = f"Custom TV/Gamepad image: {path.name}"
                if self._job_has_renderable_source(job):
                    job.output_dir = self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                    job.status = "Complete - custom TV/Gamepad image"
                else:
                    job.status = "TV/Gamepad image imported - capture or screenshot still needed"
                self.log(f"Using custom TV/Gamepad image for {job.info.display_name or job.info.path.stem}: {path.name}")
                self._touch(job)
            return self._ok(job_id=job_id)
        except Exception as exc:
            return self._error(exc)

    def update_game_details(self, job_id: str, values: dict[str, Any] | None) -> dict[str, Any]:
        try:
            values = values or {}
            with self._lock:
                job = self._job(job_id)
                title = str(values.get("name", job.info.display_name) or "").strip()
                if not title:
                    raise UserFacingError("Game Name cannot be empty.")
                previous_identity = (job.info.display_name, job.info.product_code)
                job.info.display_name = title
                job.info.release_year = str(values.get("year", job.info.release_year) or "").strip()
                job.info.players = str(values.get("players", job.info.players) or "").strip()
                job.info.product_code = str(values.get("product_code", job.info.product_code) or "").strip().upper()
                job.info.region = str(values.get("region", job.info.region) or "").strip()
                job.info.publisher = str(values.get("publisher", job.info.publisher) or "").strip()
                job.info.developer = str(values.get("developer", job.info.developer) or "").strip()
                job.info.metadata_source = "Manual"
                identity_changed = previous_identity != (job.info.display_name, job.info.product_code)
                if identity_changed:
                    job.repository_artwork_candidates = []
                    job.boot_sound_candidates = []
                    job.repository_artwork = None
                    job.repository_icon_artwork = None
                    job.repository_tv_gamepad_artwork = None
                self._write_metadata_now(job)
                job.status = "Complete - game details updated"
                self.log(f"Updated game details for {job.info.display_name}.")
                self._touch(job)
            if identity_changed and self._settings.get("automatic_repository_artwork"):
                self._start_artwork([job_id], automatic=True)
            return self._ok(job_id=job_id)
        except Exception as exc:
            return self._error(exc)

    def set_icon_template(self, job_id: str, value: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                available = {filename for _, filename in icon_template_choices(job.info.system)}
                selected = str(value or SCREENSHOT_ONLY_TEMPLATE)
                if selected not in available:
                    raise UserFacingError("The selected icon frame is not available for this console.")
                job.info.icon_template = selected
                if self._job_has_renderable_source(job):
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                else:
                    self._write_metadata_now(job)
                job.status = "Complete - icon frame updated"
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def set_tv_gamepad_source(self, job_id: str, value: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                if job.info.system not in {"Wii", "WiiWare"}:
                    raise UserFacingError("This source selector applies only to Wii and WiiWare games.")
                choice = str(value or "Selected Screenshot")
                if choice not in self._available_tv_gamepad_sources(job):
                    raise UserFacingError("The selected TV/Gamepad source is not available.")
                job.tv_gamepad_source = choice
                if self._job_has_renderable_source(job):
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = f"Complete - TV/Gamepad source: {choice}"
                self.log(f"TV/Gamepad source for {job.info.display_name or job.info.path.stem}: {choice}")
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    @staticmethod
    def _crop_state_for_info(info: RomInfo) -> dict[str, Any]:
        return {
            "source_area_selection": getattr(info, "source_area_selection", "auto"),
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

    @staticmethod
    def _bounded_int(values: dict[str, Any], key: str, low: int, high: int) -> int:
        try:
            value = int(values[key])
        except Exception as exc:
            raise UserFacingError(f"{key.replace('_', ' ').title()} must be a whole number.") from exc
        if not low <= value <= high:
            raise UserFacingError(f"{key.replace('_', ' ').title()} must be between {low} and {high}.")
        return value

    def update_framing(self, job_id: str, values: dict[str, Any] | None, rebuild: bool = True) -> dict[str, Any]:
        try:
            values = values or {}
            with self._lock:
                job = self._job(job_id)
                current = self._crop_state_for_info(job.info)
                merged = {**current, **values}
                area = str(merged.get("source_area_selection", "auto")).strip().lower()
                if area not in {"auto", "full", "top", "bottom", "left", "right"}:
                    raise UserFacingError("Source area must be Automatic, Full, Top, Bottom, Left, or Right.")
                job.info.source_area_selection = area
                for prefix in ("icon", "tv", "gamepad"):
                    setattr(job.info, f"{prefix}_focus_x", self._bounded_int(merged, f"{prefix}_x", 0, 100))
                    setattr(job.info, f"{prefix}_focus_y", self._bounded_int(merged, f"{prefix}_y", 0, 100))
                    setattr(job.info, f"{prefix}_zoom", self._bounded_int(merged, f"{prefix}_zoom", 25, 300))
                    fill_mode = str(merged.get(f"{prefix}_fill_mode", "Black"))
                    if fill_mode not in {"Black", "Blurred Image", "Solid Color"}:
                        fill_mode = "Black"
                    fill_color = str(merged.get(f"{prefix}_fill_color", "#000000"))
                    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", fill_color):
                        fill_color = "#000000"
                    setattr(job.info, f"{prefix}_fill_mode", fill_mode)
                    setattr(job.info, f"{prefix}_fill_color", fill_color)
                if rebuild and self._job_has_renderable_source(job):
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                    job.status = "Complete - crop and zoom updated"
                self._touch(job)
            return self._ok(framing=self._crop_state_for_info(job.info))
        except Exception as exc:
            return self._error(exc)

    def reset_framing(self, job_id: str) -> dict[str, Any]:
        values: dict[str, Any] = {"source_area_selection": "auto"}
        for prefix in ("icon", "tv", "gamepad"):
            values.update({
                f"{prefix}_x": 50,
                f"{prefix}_y": 50,
                f"{prefix}_zoom": 100,
                f"{prefix}_fill_mode": "Black",
                f"{prefix}_fill_color": "#000000",
            })
        return self.update_framing(job_id, values, rebuild=True)

    def update_placement(self, job_id: str, values: dict[str, Any] | None) -> dict[str, Any]:
        try:
            values = values or {}
            with self._lock:
                job = self._job(job_id)
                x = self._bounded_int(values, "x", 0, 1280)
                y = self._bounded_int(values, "y", 0, 720)
                width = self._bounded_int(values, "width", 1, 1280)
                height = self._bounded_int(values, "height", 1, 720)
                if x + width > 1280 or y + height > 720:
                    raise UserFacingError("The placement rectangle must stay within the 1280 x 720 TV canvas.")
                job.info.shot_x, job.info.shot_y = x, y
                job.info.shot_width, job.info.shot_height = width, height
                if self._job_has_renderable_source(job):
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - placement updated"
                self.log(f"Updated screenshot placement for {job.info.display_name}: X={x}, Y={y}, W={width}, H={height}")
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def reset_placement(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                x, y, width, height = default_shot_rect(job.info.system)
            return self.update_placement(job_id, {"x": x, "y": y, "width": width, "height": height})
        except Exception as exc:
            return self._error(exc)

    def build_images(self, job_ids: list[str] | None = None) -> dict[str, Any]:
        try:
            with self._lock:
                ids = list(job_ids or self.selected_ids)
                if not ids:
                    ids = [job.job_id for job in self.jobs]
                jobs = [self._job(job_id) for job_id in ids]
                ready = [job for job in jobs if self._job_has_renderable_source(job)]
                if not ready:
                    raise UserFacingError("Select a game with a capture, imported screenshot, or downloaded artwork source first.")
                built = 0
                for job in ready:
                    try:
                        self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                        job.status = "Complete - images rebuilt"
                        built += 1
                    except Exception as exc:
                        job.status = f"Error - {exc}"
                        self.log(f"ERROR rebuilding {job.info.display_name}: {exc}")
                self.log(f"Rebuilt artwork for {built} game(s).")
                self._touch()
            return self._ok(built=built)
        except Exception as exc:
            return self._error(exc)

    # ---------- Metadata ----------

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

    def _start_worker(self, kind: str, message: str, target, *args) -> None:
        with self._lock:
            if self._restarting:
                raise UserFacingError("The application is restarting for an update.")
            if self._worker and self._worker.is_alive():
                raise UserFacingError(f"Wait for the current {self._busy_kind or 'background'} operation to finish.")
            self._busy_kind = kind
            self._busy_message = message
            self._progress_current = 0
            self._progress_total = max(1, len(args[0])) if args and isinstance(args[0], list) else 1
            self._touch()

            def wrapped():
                try:
                    target(*args)
                except Exception as exc:
                    with self._lock:
                        self.log(f"{kind.title()} failed: {exc}")
                        self._notify("error", f"{kind.title()} failed", str(exc))
                finally:
                    with self._lock:
                        self._busy_kind = ""
                        self._busy_message = ""
                        self._progress_current = 0
                        self._progress_total = 0
                        self._worker = None
                        self._touch()
                    self._run_pending_auto_artwork()

            self._worker = threading.Thread(target=wrapped, daemon=True, name=f"vc-{kind}")
            self._worker.start()

    def _start_metadata(self, job_ids: list[str], automatic_artwork: bool = False) -> None:
        with self._lock:
            valid = [job_id for job_id in job_ids if any(job.job_id == job_id and job.source_kind == "ROM" for job in self.jobs)]
            if automatic_artwork:
                self._pending_auto_artwork.extend(job_id for job_id in valid if job_id not in self._pending_auto_artwork)
        if not valid:
            return
        self._start_worker("metadata", "Fetching game metadata", self._metadata_worker, valid)

    def fetch_metadata(self, job_ids: list[str] | None = None) -> dict[str, Any]:
        try:
            with self._lock:
                ids = list(job_ids or self.selected_ids)
                if not ids:
                    ids = [job.job_id for job in self.jobs if job.source_kind == "ROM"]
            self._start_metadata(ids, automatic_artwork=bool(self._settings.get("automatic_repository_artwork")))
            return self._ok(started=True)
        except Exception as exc:
            return self._error(exc)

    def _metadata_worker(self, job_ids: list[str]) -> None:
        settings = LibretroSettings()
        for index, job_id in enumerate(job_ids):
            try:
                with self._lock:
                    job = self._job(job_id)
                    job.status = "Fetching metadata"
                    info = job.info
                    self._busy_message = f"Fetching metadata: {info.path.name}"
                    self._touch(job)
                self.log(f"Metadata: {info.path.name} - checking public metadata databases...")
                metadata = lookup_libretro(
                    info.path,
                    info.system,
                    info.region,
                    info.product_code,
                    settings,
                    use_cache=True,
                    title_hint=info.display_name or info.title or info.path.stem,
                )
                with self._lock:
                    job = self._job(job_id)
                    self._apply_metadata(job.info, metadata)
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                    detail = ", ".join(
                        value for value in (
                            metadata.release_year,
                            f"{metadata.players} player(s)" if metadata.players else "",
                        ) if value
                    )
                    job.status = f"Metadata ready{f' - {detail}' if detail else ''}"
                    self._progress_current = index + 1
                    self._touch(job)
                self.log(f"Metadata matched: {job.info.display_name}")
            except Exception as exc:
                with self._lock:
                    try:
                        job = self._job(job_id)
                        self._write_metadata_now(job)
                        job.status = "Metadata unavailable - fallback saved"
                        self._touch(job)
                        name = job.info.path.name
                    except Exception:
                        name = job_id
                    self._progress_current = index + 1
                self.log(f"Metadata lookup failed for {name}: {exc}")

    def update_metadata_database(self) -> dict[str, Any]:
        try:
            with self._lock:
                systems = sorted({job.info.system for job in self.jobs if job.source_kind == "ROM"})
            if not systems:
                raise UserFacingError("Add at least one ROM first.")
            self._start_worker("metadata", "Updating metadata databases", self._database_worker, systems)
            return self._ok(started=True)
        except Exception as exc:
            return self._error(exc)

    def _database_worker(self, systems: list[str]) -> None:
        try:
            self.log("Updating public Libretro metadata database files...")
            result = update_database_for_systems(systems, LibretroSettings())
            self.log(result.replace("\n", " | "))
            self._notify("success", "Metadata database", result)
        except Exception as exc:
            self.log(f"Metadata database update failed: {exc}")
            self._notify("error", "Metadata database", str(exc))

    # ---------- Repository artwork ----------

    @staticmethod
    def _repository_title_candidates(job: GameJob) -> list[str]:
        values: list[str] = []
        for raw in (job.info.display_name, job.info.title, job.info.path.stem):
            title = str(raw or "").strip()
            if title and title.casefold() not in {value.casefold() for value in values}:
                values.append(title)
        return values

    def _run_pending_auto_artwork(self) -> None:
        with self._lock:
            if not self._settings.get("automatic_repository_artwork") or not self._pending_auto_artwork:
                self._pending_auto_artwork.clear()
                return
            pending = list(dict.fromkeys(self._pending_auto_artwork))
            self._pending_auto_artwork.clear()
        try:
            self._start_artwork(pending, automatic=True)
        except Exception as exc:
            self.log(f"Automatic artwork search could not start: {exc}")

    def search_artwork(self, job_id: str) -> dict[str, Any]:
        try:
            self._start_artwork([job_id], automatic=False, sound_only=False)
            return self._ok(started=True)
        except Exception as exc:
            return self._error(exc)

    def search_boot_sounds(self, job_id: str) -> dict[str, Any]:
        try:
            self._start_artwork([job_id], automatic=False, sound_only=True)
            return self._ok(started=True)
        except Exception as exc:
            return self._error(exc)

    def _start_artwork(self, job_ids: list[str], *, automatic: bool, sound_only: bool = False) -> None:
        with self._lock:
            jobs = [self._job(job_id) for job_id in job_ids]
            jobs = [job for job in jobs if job.source_kind == "ROM"]
            if automatic:
                jobs = [job for job in jobs if not self._job_has_repository_artwork(job) or job.boot_sound is None]
            if not jobs:
                return
            for job in jobs:
                if sound_only and not system_has_boot_sound_repository(job.info.system):
                    raise UserFacingError(
                        f"No online boot-sound source is configured for {job.info.system}. Use Set Console Sound instead."
                    )
                if not sound_only and not system_has_repository(job.info.system):
                    raise UserFacingError(f"No artwork source is configured for {job.info.system}.")
                titles = self._repository_title_candidates(job)
                if not sound_only and not product_code_candidates(job.info.product_code) and (
                    not self._settings.get("repository_title_fallback") or not titles
                ):
                    raise UserFacingError(
                        "This game has neither a usable product code nor an enabled title fallback. Edit its details or enable title fallbacks."
                    )
            ids = [job.job_id for job in jobs]
        label = "Searching boot-sound sources" if sound_only else "Searching artwork and boot-sound sources"
        self._start_worker("artwork", label, self._artwork_worker, ids, automatic, sound_only)

    def _artwork_worker(self, job_ids: list[str], automatic: bool, sound_only: bool) -> None:
        cache_root = config_dir() / "repository_artwork_cache"
        for index, job_id in enumerate(job_ids):
            try:
                with self._lock:
                    job = self._job(job_id)
                    title = job.info.display_name or job.info.path.stem
                    job.status = "Searching boot-sound sources" if sound_only else "Searching artwork and boot-sound sources"
                    self._busy_message = f"Searching sources: {title}"
                    self._touch(job)
                self.log(f"Source search: checking {'boot sounds' if sound_only else 'artwork and boot sounds'} for {title}...")

                sounds: list[BootSound] = []
                if system_has_boot_sound_repository(job.info.system):
                    try:
                        sounds = find_and_download_boot_sounds(job.info.system, job.info.product_code, cache_root)
                        if sounds:
                            with self._lock:
                                self._receive_boot_sound_candidates(job, sounds, sound_only=sound_only)
                    except Exception as exc:
                        self.log(f"Boot-sound lookup failed for {title}: {exc}")
                if sound_only:
                    with self._lock:
                        if not sounds:
                            job.status = "Boot sound not found"
                            self.log(f"No per-game or online {job.info.system} startup sound was found.")
                        self._progress_current = index + 1
                        self._touch(job)
                    continue

                candidates = find_and_download_artwork_candidates(
                    job.info.system,
                    job.info.product_code,
                    cache_root,
                    title_candidates=self._repository_title_candidates(job),
                    region=job.info.region,
                    allow_title_fallback=bool(self._settings.get("repository_title_fallback")),
                    search_libretro=bool(self._settings.get("libretro_artwork_enabled")),
                    search_gamecube_collection=bool(self._settings.get("gamecube_artwork_enabled")),
                    include_uncertain_matches=bool(self._settings.get("repository_uncertain_matches")),
                )
                with self._lock:
                    if not candidates:
                        job.status = "Repository artwork not found"
                        self.log(
                            f"No sufficiently confident artwork match was found for {job.info.system}. Exact ID and enabled title evidence were checked."
                        )
                    else:
                        job.repository_artwork_candidates = list(candidates)
                        trusted = candidates[0].match_method == "Exact product code" or candidates[0].confidence >= 0.86
                        if automatic and not trusted:
                            job.status = f"Artwork found - review {len(candidates)} possible match(es)"
                            self.log(
                                f"Artwork search retained {len(candidates)} possible match option(s) for {title}. Nothing was applied automatically."
                            )
                        elif automatic:
                            self._accept_repository_artwork(job, candidates[0], assignment="all", automatic=True)
                        elif len(candidates) == 1 and trusted:
                            self._accept_repository_artwork(job, candidates[0], assignment="all", automatic=False)
                        else:
                            job.status = f"Artwork found - review {len(candidates)} matches"
                            self.log(f"Artwork search retained {len(candidates)} match options for {title}. Review and choose the preferred source.")
                    self._progress_current = index + 1
                    self._touch(job)
            except RepositoryArtworkError as exc:
                with self._lock:
                    try:
                        job = self._job(job_id)
                        job.status = "Repository artwork lookup failed"
                        self._touch(job)
                    except Exception:
                        pass
                    self._progress_current = index + 1
                self.log(f"Repository artwork lookup failed: {exc}")
                if not automatic:
                    self._notify("error", "Artwork lookup", str(exc))
            except Exception as exc:
                with self._lock:
                    try:
                        job = self._job(job_id)
                        job.status = f"Error - {exc}"
                        self._touch(job)
                    except Exception:
                        pass
                    self._progress_current = index + 1
                self.log(f"Artwork lookup failed: {exc}")
                if not automatic:
                    self._notify("error", "Artwork lookup", str(exc))

    def _receive_boot_sound_candidates(self, job: GameJob, candidates: list[BootSound], *, sound_only: bool) -> None:
        job.boot_sound_candidates = list(candidates)
        preserve = job.boot_sound is not None and (
            job.boot_sound.provider_name in {"Manual file", "Stored game sound", "Local console sound"}
            or job.boot_sound.sound_kind.startswith("Built-in")
        )
        if not preserve:
            self._activate_boot_sound(job, candidates[0], log_change=False)
            self.log(
                f"Boot-sound search retained {len(candidates)} option(s) for {job.info.display_name or job.info.path.stem}. Applied {candidates[0].display_label}."
            )
        else:
            self.log(
                f"Boot-sound search found {len(candidates)} option(s) for {job.info.display_name or job.info.path.stem}; the stored or local selection remains active."
            )
        if sound_only:
            job.status = f"Complete - {len(candidates)} boot-sound option(s)"
        self._touch(job)

    def assign_artwork(self, job_id: str, candidate_index: int, assignment: str = "all") -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                index = int(candidate_index)
                if not 0 <= index < len(job.repository_artwork_candidates):
                    raise UserFacingError("The selected artwork match is no longer available.")
                artwork = job.repository_artwork_candidates[index]
                self._accept_repository_artwork(job, artwork, assignment=assignment, automatic=False)
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def _accept_repository_artwork(
        self,
        job: GameJob,
        artwork: RepositoryArtwork,
        *,
        assignment: str,
        automatic: bool,
    ) -> None:
        if automatic and job.prefer_user_source:
            self.log(
                f"Automatic artwork was not reapplied for {job.info.display_name or job.info.path.stem} because a captured or imported screenshot is active."
            )
            return
        assignment = assignment if assignment in {"all", "icon", "tv_gamepad"} else "all"
        available = set(artwork.available_outputs)
        if assignment == "icon" and "Icon" not in available:
            raise UserFacingError("This match does not contain artwork that can be used for Icon Image.")
        if assignment == "tv_gamepad" and not ({"TV", "Gamepad"} & available):
            raise UserFacingError("This match does not contain artwork that can be used for TV or Gamepad Image.")
        previous = (
            job.repository_artwork,
            job.repository_icon_artwork,
            job.repository_tv_gamepad_artwork,
            job.prefer_user_source,
        )
        current_icon = self._repository_icon_artwork(job)
        current_banners = self._repository_tv_gamepad_artwork(job)
        if assignment == "all":
            has_icon = "Icon" in available
            has_banners = bool({"TV", "Gamepad"} & available)
            if has_icon and has_banners:
                job.repository_artwork = artwork
                job.repository_icon_artwork = artwork
                job.repository_tv_gamepad_artwork = artwork
            else:
                job.repository_artwork = None
                job.repository_icon_artwork = artwork if has_icon else current_icon
                job.repository_tv_gamepad_artwork = artwork if has_banners else current_banners
        elif assignment == "icon":
            job.repository_artwork = None
            job.repository_icon_artwork = artwork
            job.repository_tv_gamepad_artwork = current_banners
        else:
            job.repository_artwork = None
            job.repository_icon_artwork = current_icon
            job.repository_tv_gamepad_artwork = artwork
        job.prefer_user_source = False
        try:
            self._export_job(job, screen_crop=self._recommended_screen_crop(job))
        except Exception:
            (
                job.repository_artwork,
                job.repository_icon_artwork,
                job.repository_tv_gamepad_artwork,
                job.prefer_user_source,
            ) = previous
            raise
        label = {
            "all": "all enabled image outputs",
            "icon": "Icon Image",
            "tv_gamepad": "TV and Gamepad Images",
        }[assignment]
        job.status = f"Complete - artwork assigned to {label}"
        self.log(
            f"Repository artwork matched {job.info.display_name or job.info.path.stem}: {artwork.provider_name}, {artwork.match_method.lower()}. Assigned to {label}."
        )
        self._touch(job)

    def clear_repository_artwork(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                job.repository_artwork = None
                job.repository_icon_artwork = None
                job.repository_tv_gamepad_artwork = None
                job.prefer_user_source = True
                if job.selected is not None:
                    self._export_job(job, screen_crop=self._recommended_screen_crop(job))
                else:
                    job.output_dir = export_metadata_only(job.info, self._export_options(), None)
                    for filename in ("Icon Image.png", "TV Image.png", "Gamepad Image.png"):
                        (Path(job.output_dir) / filename).unlink(missing_ok=True)
                job.status = "Ready - using generated artwork"
                self.log(f"Repository artwork cleared for {job.info.display_name or job.info.path.stem}; generated artwork is active.")
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    @staticmethod
    def _artwork_asset_path(artwork: RepositoryArtwork, asset: str) -> Path | None:
        mapping = {
            "source": artwork.source_image_path,
            "icon": artwork.icon_path,
            "tv": artwork.tv_path,
            "gamepad": artwork.gamepad_path,
        }
        selected = mapping.get(str(asset))
        if selected and Path(selected).is_file():
            return Path(selected)
        for fallback in (artwork.source_image_path, artwork.tv_path, artwork.gamepad_path, artwork.icon_path):
            if fallback and Path(fallback).is_file():
                return Path(fallback)
        return None

    def get_artwork_preview(
        self,
        job_id: str,
        candidate_index: int,
        asset: str = "source",
        max_width: int = 1000,
        max_height: int = 650,
    ) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                index = int(candidate_index)
                if not 0 <= index < len(job.repository_artwork_candidates):
                    raise UserFacingError("The selected artwork match is unavailable.")
                path = self._artwork_asset_path(job.repository_artwork_candidates[index], asset)
            if path is None:
                raise UserFacingError("This artwork asset is unavailable.")
            return self._image_data(path, max_width=max_width, max_height=max_height)
        except Exception as exc:
            return self._error(exc)

    def _image_data(self, path: Path, *, max_width: int, max_height: int) -> dict[str, Any]:
        max_width = max(64, min(int(max_width), 1920))
        max_height = max(64, min(int(max_height), 1200))
        with Image.open(path) as source:
            image = source.convert("RGBA")
            source_width, source_height = image.size
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
        return self._ok(
            data_url="data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
            width=image.width,
            height=image.height,
            source_width=source_width,
            source_height=source_height,
            file_name=path.name,
        )

    def _apply_repository_art_outputs(self, job: GameJob, output: Path, options: ExportOptions) -> list[Path]:
        icon_artwork = self._repository_icon_artwork(job)
        banner_artwork = self._repository_tv_gamepad_artwork(job)
        if not icon_artwork and not banner_artwork:
            return []
        for filename, enabled in (
            ("Icon Image.png", options.create_icon),
            ("TV Image.png", options.create_tv),
            ("Gamepad Image.png", options.create_gamepad),
        ):
            if not enabled:
                (output / filename).unlink(missing_ok=True)
        written: list[Path] = []
        if icon_artwork and options.create_icon:
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
                written.append(render_icon_from_source(source, job.info, source_options, output / "Icon Image.png"))
            else:
                written.extend(apply_repository_artwork(
                    icon_artwork,
                    output,
                    create_icon=True,
                    create_tv=False,
                    create_gamepad=False,
                    preserve_disabled=True,
                ))
        if banner_artwork and (options.create_tv or options.create_gamepad):
            if banner_artwork.source_image_path and Path(banner_artwork.source_image_path).is_file():
                source = Path(banner_artwork.source_image_path)
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
                    banner_artwork,
                    output,
                    create_icon=False,
                    create_tv=options.create_tv,
                    create_gamepad=options.create_gamepad,
                    preserve_disabled=True,
                ))
        self._write_source_notes(job, output)
        return written

    @staticmethod
    def _write_source_notes(job: GameJob, output: Path) -> None:
        icon_artwork = WebUIApi._repository_icon_artwork(job)
        banner_artwork = WebUIApi._repository_tv_gamepad_artwork(job)
        metadata_path = output / "metadata.txt"
        if not metadata_path.exists():
            return
        try:
            text = metadata_path.read_text(encoding="utf-8")
            for marker in ("Repository Artwork\n------------------\n", "Boot Sound\n----------\n"):
                if marker in text:
                    text = text.split(marker, 1)[0].rstrip()
            notes: list[str] = []
            assignments: list[tuple[str, RepositoryArtwork]] = []
            if icon_artwork is not None and icon_artwork == banner_artwork:
                assignments.append(("Icon Image, TV Image, and Gamepad Image", icon_artwork))
            else:
                if icon_artwork:
                    assignments.append(("Icon Image", icon_artwork))
                if banner_artwork:
                    assignments.append(("TV Image and Gamepad Image", banner_artwork))
            if assignments:
                lines = ["Repository Artwork", "------------------"]
                for outputs, artwork in assignments:
                    lines.extend((
                        "",
                        f"Assigned Outputs: {outputs}",
                        f"Source: {artwork.provider_name}",
                        f"Repository: {artwork.provider_url}",
                        f"Match Method: {artwork.match_method}",
                        f"Matched Title: {artwork.matched_title or 'Not applicable'}",
                        f"Source Image Type: {artwork.source_image_kind or 'Finished Wii U artwork'}",
                        f"Confidence: {artwork.confidence:.0%}",
                        f"Product Code: {artwork.product_code or 'Not available'}",
                    ))
                lines.extend(("", "Notice: Community-hosted artwork was downloaded on demand. Review the source repository terms before redistribution."))
                notes.append("\n".join(lines))
            if job.boot_sound:
                sound = job.boot_sound
                lines = [
                    "Boot Sound",
                    "----------",
                    f"Source: {sound.provider_name}",
                    f"Type: {sound.sound_kind}",
                    f"Selected File: {sound.path.name}",
                    "Output File: bootSound.wav",
                    "Conversion: Automatically converted to WAV when selected or exported",
                ]
                if sound.provider_url:
                    lines.append(f"Repository: {sound.provider_url}")
                lines.append("Notice: Confirm that you have permission to redistribute downloaded or manually selected audio.")
                notes.append("\n".join(lines))
            suffix = "" if not notes else "\n\n" + "\n\n".join(notes)
            metadata_path.write_text(text.rstrip() + suffix + "\n", encoding="utf-8")
        except OSError:
            pass

    # ---------- Boot sounds ----------

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
            digest = hashlib.sha256(info.path.name.casefold().encode("utf-8", errors="ignore")).hexdigest()[:10]
            identity_key = f"{identity_key}_{digest}"
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
        try:
            usable = path.is_file() and path.stat().st_size > 44
        except OSError:
            usable = False
        if not usable:
            return None
        return BootSound(
            provider_name="Local console sound",
            provider_url="",
            system=system,
            path=path,
            label=f"{system} startup sound",
            sound_kind="Local console default",
        )

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
        if job.boot_sound is None:
            self._write_source_notes(job, output)
            return None
        source = Path(job.boot_sound.path)
        if not source.is_file() or source.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            raise UserFacingError("The selected boot-sound file is missing or unsupported.")
        output.mkdir(parents=True, exist_ok=True)
        destination = output / "bootSound.wav"
        convert_boot_sound_to_wav(source, destination)
        self._remove_boot_sound_outputs(output, keep=destination)
        self._write_source_notes(job, output)
        return destination

    def _activate_boot_sound(self, job: GameJob, sound: BootSound, *, log_change: bool = True) -> None:
        if not sound.path.is_file() or sound.path.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            raise UserFacingError("The selected boot-sound file is missing or unsupported.")
        previous = job.boot_sound
        job.boot_sound = sound
        if all(_path_key(candidate.path) != _path_key(sound.path) for candidate in job.boot_sound_candidates):
            job.boot_sound_candidates.insert(0, sound)
        try:
            if not job.output_dir:
                self._write_metadata_now(job)
            if not job.output_dir:
                raise BootSoundConversionError("The output folder for the converted boot sound is unavailable.")
            self._apply_boot_sound_output(job, Path(job.output_dir))
        except Exception:
            job.boot_sound = previous
            raise
        if log_change:
            self.log(
                f"Boot sound selected for {job.info.display_name or job.info.path.stem}: {sound.sound_kind}, {sound.provider_name}, {sound.path.name}; converted to bootSound.wav."
            )
        self._touch(job)

    def select_boot_sound(self, job_id: str, token: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                _, mapping = self._sound_options(job)
                sound = mapping.get(str(token))
                if sound is None:
                    raise UserFacingError("The selected boot-sound option is no longer available.")
                self._activate_boot_sound(job, sound)
                job.status = f"Complete - boot sound: {sound.sound_kind.lower()}"
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    @staticmethod
    def _validate_sound(path: Path) -> None:
        if path.suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            raise UserFacingError("Choose a WAV, MP3, or BTSND file.")
        if not path.is_file() or path.stat().st_size <= 0:
            raise UserFacingError("The selected sound is empty or unavailable.")
        if path.stat().st_size > 24 * 1024 * 1024:
            raise UserFacingError("The selected boot sound exceeds the 24 MB safety limit.")

    def choose_game_boot_sound(self, job_id: str) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=(
                    "Supported boot sounds (*.wav;*.mp3;*.btsnd)",
                    "WAV audio (*.wav)",
                    "MP3 audio (*.mp3)",
                    "Wii U boot sound (*.btsnd)",
                ),
            )
            if not paths:
                return self._ok(cancelled=True)
            return self.import_game_boot_sound(job_id, paths[0])
        except Exception as exc:
            return self._error(exc)

    def import_game_boot_sound(self, job_id: str, path_text: str) -> dict[str, Any]:
        try:
            source = Path(path_text)
            self._validate_sound(source)
            with self._lock:
                job = self._job(job_id)
                destination = self._game_boot_sound_path(job.info)
                convert_boot_sound_to_wav(source, destination)
                sound = BootSound(
                    provider_name="Stored game sound",
                    provider_url="",
                    system=job.info.system,
                    path=destination,
                    label=source.stem,
                    sound_kind="Stored per-game",
                    product_code=job.info.product_code,
                )
                self._activate_boot_sound(job, sound)
                job.status = "Complete - stored per-game boot sound"
                self.log(
                    f"Imported {source.name} for {job.info.display_name or job.info.path.stem}; the persistent copy is stored at {destination}."
                )
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def choose_console_boot_sound(self, job_id: str) -> dict[str, Any]:
        try:
            paths = self._file_dialog(
                "file",
                file_types=(
                    "Supported boot sounds (*.wav;*.mp3;*.btsnd)",
                    "WAV audio (*.wav)",
                    "MP3 audio (*.mp3)",
                    "Wii U boot sound (*.btsnd)",
                ),
            )
            if not paths:
                return self._ok(cancelled=True)
            return self.set_console_boot_sound(job_id, paths[0])
        except Exception as exc:
            return self._error(exc)

    def set_console_boot_sound(self, job_id: str, path_text: str) -> dict[str, Any]:
        try:
            source = Path(path_text)
            self._validate_sound(source)
            with self._lock:
                selected_job = self._job(job_id)
                destination = self._console_boot_sound_path(selected_job.info.system)
                convert_boot_sound_to_wav(source, destination)
                sound = self._local_console_boot_sound(selected_job.info.system)
                if sound is None:
                    raise BootSoundConversionError("The console startup sound could not be stored.")
                updated = 0
                for job in self.jobs:
                    if job.info.system != selected_job.info.system:
                        continue
                    job.boot_sound_candidates = [
                        candidate for candidate in job.boot_sound_candidates
                        if candidate.provider_name != "Local console sound" and _path_key(candidate.path) != _path_key(destination)
                    ]
                    job.boot_sound_candidates.insert(0, sound)
                    if job is selected_job or job.boot_sound is None or job.boot_sound.sound_kind in {"Console default", "Local console default"}:
                        self._activate_boot_sound(job, sound, log_change=False)
                        job.status = f"Complete - {job.info.system} console startup sound"
                        updated += 1
                self.log(
                    f"Stored {selected_job.info.system} console startup sound from {source.name}. Activated it for {updated} compatible loaded game(s)."
                )
                self._touch()
            return self._ok(updated=updated)
        except Exception as exc:
            return self._error(exc)

    def clear_boot_sound(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                active = job.boot_sound
                removed_stored = False
                if active and active.provider_name == "Stored game sound":
                    expected = self._game_boot_sound_path(job.info)
                    if _path_key(active.path) == _path_key(expected):
                        expected.unlink(missing_ok=True)
                        removed_stored = True
                        job.boot_sound_candidates = [candidate for candidate in job.boot_sound_candidates if _path_key(candidate.path) != _path_key(expected)]
                job.boot_sound = None
                if job.output_dir:
                    self._remove_boot_sound_outputs(Path(job.output_dir))
                    self._write_source_notes(job, Path(job.output_dir))
                job.status = "Ready - no boot sound"
                self.log(
                    f"Boot sound cleared for {job.info.display_name or job.info.path.stem}."
                    + (" The stored configuration copy was removed." if removed_stored else "")
                )
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def get_boot_sound_audio(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                if job.boot_sound is None:
                    raise UserFacingError("Choose or search for a boot sound first.")
                if not job.output_dir:
                    self._write_metadata_now(job)
                destination = self._apply_boot_sound_output(job, Path(job.output_dir)) if job.output_dir else None
                sound_label = job.boot_sound.display_label
                source_name = job.boot_sound.path.name
            if destination is None or not destination.is_file():
                raise UserFacingError("The converted bootSound.wav file is unavailable.")
            data = destination.read_bytes()
            if len(data) > 32 * 1024 * 1024:
                raise UserFacingError("The converted preview exceeds the playback safety limit.")
            self.log(f"Playing active boot sound for {job.info.display_name or job.info.path.stem}: {sound_label}.")
            return self._ok(
                data_url="data:audio/wav;base64," + base64.b64encode(data).decode("ascii"),
                label=sound_label,
                source_file=source_name,
            )
        except Exception as exc:
            return self._error(exc)

    def open_more_sounds(self, job_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                url = myinstants_search_url(job.info.system)
            webbrowser.open_new_tab(url)
            self.log(f"Opened MyInstants sound search for {job.info.system}.")
            return self._ok(url=url)
        except Exception as exc:
            return self._error(exc)

    # ---------- Capture and candidate review ----------

    def _capture_targets(self) -> list[GameJob]:
        selected_roms = [job for job in self._selected_jobs() if job.source_kind == "ROM"]
        if len(selected_roms) == 1:
            return selected_roms
        checked = [job for job in self.jobs if job.source_kind == "ROM" and job.capture_checked]
        return checked or selected_roms

    def _validate_capture(self, jobs: list[GameJob]) -> EmulatorSettings:
        if not jobs:
            raise UserFacingError("Check at least one ROM, or select exactly one ROM to capture it by itself.")
        self._validate_general_settings()
        regular = [job for job in jobs if job.info.system not in {"GameCube", "Wii", "WiiWare"}]
        retro_text = str(self._settings.get("retroarch_path", "") or "").strip()
        dolphin_text = str(self._settings.get("dolphin_path", "") or "").strip()
        retro = Path(retro_text) if retro_text else None
        dolphin = Path(dolphin_text) if dolphin_text else None
        if regular and (retro is None or not retro.exists()):
            raise UserFacingError("Configure a valid RetroArch.exe path.")
        dolphin_jobs = [job for job in jobs if job.info.system in {"GameCube", "Wii", "WiiWare"}]
        if dolphin_jobs:
            standalone_ready = bool(dolphin and dolphin.exists())
            core = discover_core(retro, dolphin_jobs[0].info.system) if retro and retro.exists() else None
            core_ready = bool(core and dolphin_system_files_ready(retro))
            if not standalone_ready and not core_ready:
                raise UserFacingError("Configure Dolphin.exe or a working RetroArch Dolphin core with its required system files.")
        output = str(self._settings.get("output_root", "") or "").strip()
        if not output:
            raise UserFacingError("Choose an export folder.")
        return EmulatorSettings(
            retroarch_path=retro,
            dolphin_path=dolphin,
            warmup_seconds=float(self._settings["warmup_seconds"]),
            duration_seconds=float(self._settings["duration_seconds"]),
            interval_seconds=float(self._settings["interval_seconds"]),
        )

    def start_capture(self) -> dict[str, Any]:
        try:
            with self._lock:
                jobs = self._capture_targets()
                settings = self._validate_capture(jobs)
                ids = [job.job_id for job in jobs]
                self._cancel_event.clear()
            self._start_worker("capture", "Capturing ROM screens", self._capture_worker, ids, settings)
            return self._ok(started=True, count=len(ids))
        except Exception as exc:
            return self._error(exc)

    def stop_capture(self) -> dict[str, Any]:
        with self._lock:
            if self._busy_kind != "capture":
                return self._error("No capture is currently running.")
            self._cancel_event.set()
            self._busy_message = "Stopping after current captured frames are finalized"
            self._touch()
        self.log("Stop requested. Captured frames already collected will be analyzed and exported.")
        return self._ok()

    def _finalize_capture(self, job: GameJob, samples: list[FrameSample], session: Path) -> list[FrameSample]:
        if not samples:
            raise EmulatorError("No captured frames are available to finalize.")
        options = self._export_options()
        count = int(self._settings["candidate_count"])
        if job.info.system == "Nintendo DS" and options.screen_crop == "auto":
            candidates = select_nds_candidates(samples, session / "nds_single_screens", count)
            options = replace(options, screen_crop="full")
        else:
            candidates = select_candidates(samples, count)
        if not candidates:
            raise EmulatorError("No usable title-screen candidates were found.")
        job.candidates = candidates
        job.selected = candidates[0]
        job.image_imported = False
        previous = (
            job.repository_artwork,
            job.repository_icon_artwork,
            job.repository_tv_gamepad_artwork,
            job.prefer_user_source,
        )
        job.repository_artwork = None
        job.repository_icon_artwork = None
        job.repository_tv_gamepad_artwork = None
        job.prefer_user_source = True
        try:
            self._export_job(job, sample=job.selected, candidates=candidates, options=options)
        except Exception:
            (
                job.repository_artwork,
                job.repository_icon_artwork,
                job.repository_tv_gamepad_artwork,
                job.prefer_user_source,
            ) = previous
            raise
        return candidates

    def _capture_worker(self, job_ids: list[str], settings: EmulatorSettings) -> None:
        for index, job_id in enumerate(job_ids):
            if self._cancel_event.is_set():
                break
            try:
                with self._lock:
                    job = self._job(job_id)
                    job.status = "Starting capture"
                    self._busy_message = f"Capturing {job.info.display_name or job.info.path.name}"
                    self._touch(job)
                self.log(f"[{index + 1}/{len(job_ids)}] {job.info.path.name} - {job.info.system}")
                session = Path(tempfile.mkdtemp(prefix="vc_title_capture_"))
                job.session_dir = session
                samples = capture_game(
                    job.info,
                    settings,
                    session,
                    log=self.log,
                    cancelled=self._cancel_event.is_set,
                )
                with self._lock:
                    job.status = f"Finalizing {len(samples)} captured frames"
                    self._touch(job)
                    candidates = self._finalize_capture(job, samples, session)
                    job.status = (
                        f"Stopped - {len(candidates)} candidates retained"
                        if self._cancel_event.is_set()
                        else f"Complete - {len(candidates)} candidates"
                    )
                    self._progress_current = index + 1
                    self._touch(job)
                self.log(f"Selected {job.selected.timestamp:.1f}s frame, score {job.selected.score:.3f}")
                self.log(f"Exported to: {job.output_dir}")
            except CaptureCancelled:
                with self._lock:
                    try:
                        job = self._job(job_id)
                        job.status = "Cancelled - no usable frames"
                        self._touch(job)
                    except Exception:
                        pass
                self.log("Capture was cancelled before usable frames were available.")
                break
            except Exception as exc:
                with self._lock:
                    try:
                        job = self._job(job_id)
                        job.status = f"Error - {exc}"
                        self._touch(job)
                    except Exception:
                        pass
                    self._progress_current = index + 1
                self.log(f"ERROR: {exc}")
            if self._cancel_event.is_set():
                self.log("Batch stopped. Captures from the current game remain available for review and re-export.")
                break

    def get_candidate_preview(self, job_id: str, candidate_index: int, max_width: int = 1000, max_height: int = 650) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                index = int(candidate_index)
                if not 0 <= index < len(job.candidates):
                    raise UserFacingError("The selected captured frame is unavailable.")
                candidate = job.candidates[index]
                path = Path(candidate.path)
            payload = self._image_data(path, max_width=max_width, max_height=max_height)
            payload.update({
                "candidate_index": index,
                "timestamp": candidate.timestamp,
                "score": candidate.score,
                "screen_label": candidate.screen_label,
                "selected": candidate is job.selected,
            })
            return payload
        except Exception as exc:
            return self._error(exc)

    def choose_candidate(self, job_id: str, candidate_index: int) -> dict[str, Any]:
        try:
            with self._lock:
                job = self._job(job_id)
                index = int(candidate_index)
                if not 0 <= index < len(job.candidates):
                    raise UserFacingError("The selected captured frame is unavailable.")
                job.selected = job.candidates[index]
                job.image_imported = False
                job.repository_artwork = None
                job.repository_icon_artwork = None
                job.repository_tv_gamepad_artwork = None
                job.prefer_user_source = True
                self._export_job(job, sample=job.selected, candidates=job.candidates, screen_crop=self._recommended_screen_crop(job))
                job.status = "Complete - selected frame re-exported"
                self.log(f"Selected a different captured frame for {job.info.display_name or job.info.path.stem} and rebuilt its artwork.")
                self._touch(job)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    # ---------- Help and lifecycle ----------

    def get_help_topic(self, title: str) -> dict[str, Any]:
        body = HELP_BY_TITLE.get(str(title))
        if body is None:
            return self._error("The requested documentation topic is unavailable.")
        return self._ok(title=str(title), body=body)

    def open_activity_window(self) -> dict[str, Any]:
        """Open the session activity stream in a separate native window."""
        try:
            if self._activity_window is not None:
                try:
                    self._activity_window.restore()
                    self._activity_window.show()
                    return self._ok(opened=True, existing=True)
                except Exception:
                    self._activity_window = None
            page = _web_root() / "activity_window.html"
            if not page.is_file():
                raise UserFacingError("The detached activity interface is missing from this build.")
            import webview

            child_api = ActivityWindowApi(self)
            self._child_apis.append(child_api)
            width, height, screen = self._child_window_geometry(
                None,
                fallback=(920, 560),
                minimum=(520, 320),
                chrome=(20, 90),
            )
            child = webview.create_window(
                f"{APP_NAME} - Activity",
                str(page),
                js_api=child_api,
                width=width,
                height=height,
                screen=screen,
                min_size=(min(520, width), min(320, height)),
                resizable=True,
                background_color="#080d14",
                text_select=True,
            )
            child_api._window = child
            self._activity_window = child
            self._touch()

            def on_closed():
                with self._lock:
                    if self._activity_window is child:
                        self._activity_window = None
                        self._touch()
                self._forget_child_api(child_api)

            child.events.closed += on_closed
            return self._ok(opened=True)
        except Exception as exc:
            return self._error(exc)

    def reattach_activity_window(self) -> dict[str, Any]:
        """Close the detached Activity window and restore the embedded panel."""
        try:
            with self._lock:
                child = self._activity_window
                self._activity_window = None
                self._touch()
            if child is not None:
                child.destroy()
            return self._ok(reattached=True)
        except Exception as exc:
            return self._error(exc)

    def clear_activity(self) -> dict[str, Any]:
        with self._lock:
            self._activity_lines.clear()
            self._log_revision += 1
            self._touch()
        return self._ok()

    def bind_native_drop(self) -> None:
        """Bind pywebview's full-path drop event after the DOM is ready."""
        if self._window is None:
            return
        try:
            from webview.dom import DOMEventHandler

            def on_drop(event):
                paths: list[str] = []
                for item in ((event or {}).get("dataTransfer", {}).get("files", []) or []):
                    path = item.get("pywebviewFullPath") or item.get("path")
                    if path:
                        paths.append(str(path))
                if paths:
                    self.add_dropped_paths(paths)

            body = self._window.dom.body
            body.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)
            body.events.dragover += DOMEventHandler(lambda event: None, prevent_default=True, debounce=75)
            self.log("Native ROM file and folder drop support is active.")
        except Exception as exc:
            self.log(f"Native drop integration was unavailable: {exc}. Add ROMs and Add Folder remain available.")

    def shutdown(self) -> None:
        self._cancel_event.set()


class PreviewWindowApi:
    """Narrow bridge for a detached image preview."""

    def __init__(self, parent: WebUIApi, job_id: str, key: str):
        # Keep native controller references private for the same reason as the
        # main API's native window reference. Only this class's public methods
        # should be exposed to the detached preview page.
        self._parent = parent
        self._job_id = job_id
        self._key = key
        self._view_revision = 0
        self._window = None

    def _select(self, job_id: str, key: str) -> None:
        with self._parent._lock:
            self._job_id = str(job_id)
            self._key = str(key)
            self._view_revision += 1

    def get_state(self) -> dict[str, Any]:
        try:
            with self._parent._lock:
                job = self._parent._job(self._job_id)
                label = {
                    "title": "Title Screen",
                    "icon": "Icon Image",
                    "tv": "TV Image",
                    "gamepad": "Gamepad Image",
                }[self._key]
                name = job.info.display_name or job.info.path.stem
                revision = f"{job.preview_revision}:{self._view_revision}"
                available = bool(self._parent._preview_paths(job).get(self._key))
            return self._parent._ok(
                game_name=name,
                label=label,
                revision=revision,
                available=available,
            )
        except Exception as exc:
            return self._parent._error(exc)

    def get_preview(self, max_width: int = 2200, max_height: int = 1400) -> dict[str, Any]:
        with self._parent._lock:
            job_id = self._job_id
            key = self._key
        return self._parent.get_preview(job_id, key, max_width, max_height)

    def close(self) -> dict[str, Any]:
        try:
            window = self._window
            if window is not None:
                window.destroy()
            return self._parent._ok(closed=True)
        except Exception as exc:
            return self._parent._error(exc)


class PreviewPanelWindowApi:
    """Bridge for the detachable four-image Selected Preview panel."""

    def __init__(self, parent: WebUIApi):
        self._parent = parent
        self._window = None

    def get_state(self) -> dict[str, Any]:
        try:
            with self._parent._lock:
                selected = self._parent._selected_jobs()
                job = selected[0] if len(selected) == 1 else None
                if job is None:
                    return self._parent._ok(
                        app_name=APP_NAME,
                        version=APP_VERSION,
                        revision=self._parent._state_revision,
                        job_id="",
                        game_name="Select one game",
                        preview_revision=-1,
                        preview_available={key: False for key in PREVIEW_NAMES},
                    )
                previews = self._parent._preview_paths(job)
                return self._parent._ok(
                    app_name=APP_NAME,
                    version=APP_VERSION,
                    revision=self._parent._state_revision,
                    job_id=job.job_id,
                    game_name=job.info.display_name or job.info.path.stem,
                    preview_revision=job.preview_revision,
                    preview_available={key: bool(path) for key, path in previews.items()},
                )
        except Exception as exc:
            return self._parent._error(exc)

    def get_preview(
        self,
        job_id: str,
        key: str,
        max_width: int = 1200,
        max_height: int = 760,
    ) -> dict[str, Any]:
        return self._parent.get_preview(job_id, key, max_width, max_height)

    def open_large_preview(self, job_id: str, key: str) -> dict[str, Any]:
        return self._parent.open_preview_window(job_id, key)

    def adjust_framing(self) -> dict[str, Any]:
        return self._parent.open_selected_framing_controls()

    def reattach(self) -> dict[str, Any]:
        return self._parent.reattach_preview_panel()


class ActivityWindowApi:
    """Narrow bridge for the detached activity window."""

    def __init__(self, parent: WebUIApi):
        self._parent = parent
        self._window = None

    def get_state(self) -> dict[str, Any]:
        with self._parent._lock:
            return self._parent._ok(
                app_name=APP_NAME,
                version=APP_VERSION,
                revision=self._parent._log_revision,
                activity=list(self._parent._activity_lines),
                busy=bool(self._parent._busy_kind),
                busy_message=self._parent._busy_message,
            )

    def clear_activity(self) -> dict[str, Any]:
        return self._parent.clear_activity()

    def reattach(self) -> dict[str, Any]:
        return self._parent.reattach_activity_window()


def run() -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "pywebview is required for the WebUI. Run Setup_and_Run.bat or install the packages from requirements.txt."
        ) from exc

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WiiUVC.ImageBuilder.WebUI")
        except Exception:
            pass

    root = _web_root()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"The WebUI files are missing: {index}")

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    api = WebUIApi()
    if len(sys.argv) == 4 and sys.argv[1] == "--studio-update-health":
        api._health_args = (sys.argv[2], sys.argv[3])
    icon = _resource_root() / "assets" / "vc_title_extractor.ico"
    window = webview.create_window(
        f"{APP_NAME} v{APP_VERSION}",
        str(index),
        js_api=api,
        width=1440,
        height=940,
        min_size=(1040, 720),
        resizable=True,
        background_color="#0b1018",
        text_select=True,
    )
    api._window = window
    window.events.shown += lambda: setattr(api, "_window_shown", True)
    window.events.loaded += lambda: api.bind_native_drop()
    window.events.closed += lambda: api.shutdown()
    storage = config_dir() / "webview_storage"
    storage.mkdir(parents=True, exist_ok=True)
    start_options: dict[str, Any] = {
        "private_mode": True,
        "storage_path": str(storage),
        "icon": str(icon) if icon.is_file() else None,
    }
    if os.name == "nt":
        start_options["gui"] = "edgechromium"
    webview.start(**start_options)
