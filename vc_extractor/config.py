from __future__ import annotations

from pathlib import Path
import json
import os
import shutil


APP_NAME = "Wii U VC Image Studio"


def default_config_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home()))
        return root / "Wii_U_VC_Image_Builder"
    return Path.home() / ".config" / "wii_u_vc_image_builder"


def _location_path() -> Path:
    return default_config_dir() / "config_location.json"


def config_dir() -> Path:
    pointer = _location_path()
    try:
        if pointer.exists():
            data = json.loads(pointer.read_text(encoding="utf-8"))
            selected = Path(str(data.get("path", ""))).expanduser()
            if selected.is_absolute():
                return selected
    except Exception:
        pass
    return default_config_dir()


def set_config_dir(selected: Path, migrate: bool = True) -> Path:
    selected = selected.expanduser().resolve()
    current = config_dir().resolve()
    if selected != current and selected.is_relative_to(current):
        raise ValueError("The new configuration folder cannot be inside the current configuration folder.")
    selected.mkdir(parents=True, exist_ok=True)

    if migrate and selected != current and current.exists():
        for source in current.iterdir():
            if source == _location_path():
                continue
            destination = selected / source.name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, destination)

    pointer = _location_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    if selected == default_config_dir().resolve():
        pointer.unlink(missing_ok=True)
    else:
        pointer.write_text(json.dumps({"path": str(selected)}, indent=2), encoding="utf-8")
    return selected


def _legacy_config_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home()))
        return root / "VC_Title_Screen_Extractor" / "settings.json"
    return Path.home() / ".config" / "vc_title_screen_extractor" / "settings.json"


def config_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    for p in (config_path(), _legacy_config_path()):
        try:
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_settings(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
