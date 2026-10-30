from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .repository_art import BootSound, RepositoryArtwork
from .roms import RomInfo
from .scoring import FrameSample


@dataclass
class GameJob:
    """UI-independent state for one loaded game.

    The stable job_id is used by the WebUI so rows remain addressable while
    metadata, artwork, and capture workers update the list in the background.
    """

    info: RomInfo
    source_kind: str = "ROM"
    status: str = "Ready"
    candidates: list[FrameSample] = field(default_factory=list)
    selected: FrameSample | None = None
    output_dir: Path | None = None
    session_dir: Path | None = None
    image_imported: bool = False
    tv_gamepad_source: str = "Selected Screenshot"
    imported_banner_image_path: Path | None = None
    banner_image_note: str = ""
    capture_checked: bool = True
    repository_artwork: RepositoryArtwork | None = None
    repository_icon_artwork: RepositoryArtwork | None = None
    repository_tv_gamepad_artwork: RepositoryArtwork | None = None
    repository_artwork_candidates: list[RepositoryArtwork] = field(default_factory=list)
    boot_sound: BootSound | None = None
    boot_sound_candidates: list[BootSound] = field(default_factory=list)
    prefer_user_source: bool = False
    job_id: str = field(default_factory=lambda: uuid4().hex)
    preview_revision: int = 0

