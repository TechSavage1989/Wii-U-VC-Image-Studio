from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
import json
import os
import re
import sys
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError


GITHUB_API_ROOT = "https://api.github.com/repos"
USER_AGENT = "Wii-U-VC-Image-Builder/5.06.0"
MAX_ARTWORK_BYTES = 24 * 1024 * 1024
MAX_INDEX_BYTES = 48 * 1024 * 1024
TREE_INDEX_MAX_AGE_SECONDS = 24 * 60 * 60
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp"}
BOOT_SOUND_EXTENSIONS = {".wav", ".mp3", ".btsnd"}


BUNDLED_BOOT_SOUND_PROVIDER = "Built-in sound library"
BUNDLED_BOOT_SOUND_CATALOG = {}

MYINSTANTS_SEARCH_TERMS = {
    "Nintendo DS": "nintendo ds startup",
    "Game Boy Advance": "game boy advance startup",
    "Game Boy Color": "game boy color startup",
    "Game Boy": "nintendo game boy startup",
    "NES": "nes startup",
    "SNES": "snes startup",
    "Nintendo 64": "n64 startup",
    "GameCube": "gamecube startup",
    "Wii": "nintendo wii startup",
    "WiiWare": "nintendo wii startup",
    "MSX": "msx startup",
    "TurboGrafx-CD / PC Engine CD": "pc engine cd startup",
    "Genesis / Mega Drive": "sega genesis startup",
    "Master System": "sega master system startup",
    "Game Gear": "sega game gear startup",
    "TurboGrafx-16 / PC Engine": "turbografx 16 startup",
}


# Libretro maintains separate thumbnail repositories for each playlist/system.
# These repositories provide title screens, gameplay captures, covers, and
# logos rather than finished Wii U assets. The application feeds a downloaded
# source image through its normal Wii U artwork renderer.
LIBRETRO_THUMBNAIL_REPOSITORIES = {
    "Nintendo DS": "libretro-thumbnails/Nintendo_-_Nintendo_DS",
    "Game Boy Advance": "libretro-thumbnails/Nintendo_-_Game_Boy_Advance",
    "Game Boy Color": "libretro-thumbnails/Nintendo_-_Game_Boy_Color",
    "Game Boy": "libretro-thumbnails/Nintendo_-_Game_Boy",
    "NES": "libretro-thumbnails/Nintendo_-_Nintendo_Entertainment_System",
    "SNES": "libretro-thumbnails/Nintendo_-_Super_Nintendo_Entertainment_System",
    "Nintendo 64": "libretro-thumbnails/Nintendo_-_Nintendo_64",
    "GameCube": "libretro-thumbnails/Nintendo_-_GameCube",
    "Wii": "libretro-thumbnails/Nintendo_-_Wii",
    # WiiWare is represented within the Wii playlist collection when present.
    "WiiWare": "libretro-thumbnails/Nintendo_-_Wii",
    "MSX": "libretro-thumbnails/Microsoft_-_MSX",
    "TurboGrafx-CD / PC Engine CD": "libretro-thumbnails/NEC_-_PC_Engine_CD_-_TurboGrafx-CD",
    "Genesis / Mega Drive": "libretro-thumbnails/Sega_-_Mega_Drive_-_Genesis",
    "Master System": "libretro-thumbnails/Sega_-_Master_System_-_Mark_III",
    "Game Gear": "libretro-thumbnails/Sega_-_Game_Gear",
    "TurboGrafx-16 / PC Engine": "libretro-thumbnails/NEC_-_PC_Engine_-_TurboGrafx_16",
}

LIBRETRO_ART_KIND_PRIORITY = {
    "Named_Titles": 4,
    "Named_Snaps": 3,
    "Named_Boxarts": 2,
    "Named_Logos": 1,
}

GAMECUBE_ARTWORK_REPOSITORY = "reddragon105/gamecube-inject-artwork"
GAMECUBE_ARTWORK_URL = "https://github.com/reddragon105/gamecube-inject-artwork"


@dataclass(frozen=True)
class ArtworkProvider:
    name: str
    repository: str
    repository_url: str
    branch: str
    folders: dict[str, str]
    uses_image_subfolder: bool = False

    @property
    def cache_key(self) -> str:
        return re.sub(r"[^a-z0-9]+", "_", self.repository.lower()).strip("_")

    def directory(self, system: str, product_code: str) -> str | None:
        folder = self.folders.get(system)
        if not folder:
            return None
        middle = "/image" if self.uses_image_subfolder else ""
        return f"{folder}{middle}/{product_code}"


COMMON_FOLDERS = {
    "Nintendo DS": "nds",
    "Game Boy Advance": "gba",
    "Game Boy Color": "gbc",
    "NES": "nes",
    "SNES": "snes",
    "Nintendo 64": "n64",
    "GameCube": "gcn",
    "Wii": "wii",
    "MSX": "msx",
    "TurboGrafx-16 / PC Engine": "tg16",
}


PROVIDERS = (
    ArtworkProvider(
        name="UWUVCI Community Images",
        repository="UWUVCI-PRIME/UWUVCI-IMAGES",
        repository_url="https://github.com/UWUVCI-PRIME/UWUVCI-IMAGES",
        branch="master",
        folders=dict(COMMON_FOLDERS),
    ),
    ArtworkProvider(
        name="Cucholix WiiVC BIS",
        repository="cucholix/wiivc-bis",
        repository_url="https://github.com/cucholix/wiivc-bis",
        branch="master",
        folders={
            **COMMON_FOLDERS,
            "WiiWare": "wiiware",
            "TurboGrafx-CD / PC Engine CD": "pce",
        },
        uses_image_subfolder=True,
    ),
)


@dataclass(frozen=True)
class RepositoryArtwork:
    provider_name: str
    provider_url: str
    product_code: str
    system: str
    icon_path: Path | None = None
    tv_path: Path | None = None
    gamepad_path: Path | None = None
    source_image_path: Path | None = None
    source_image_kind: str = ""
    matched_title: str = ""
    match_method: str = "Exact product code"
    confidence: float = 1.0
    from_cache: bool = False

    @property
    def available_outputs(self) -> tuple[str, ...]:
        if self.source_image_path:
            return ("Icon", "TV", "Gamepad")
        outputs = []
        if self.icon_path:
            outputs.append("Icon")
        if self.tv_path:
            outputs.append("TV")
        if self.gamepad_path or self.tv_path:
            outputs.append("Gamepad")
        return tuple(outputs)


@dataclass(frozen=True)
class BootSound:
    provider_name: str
    provider_url: str
    system: str
    path: Path
    label: str
    sound_kind: str = "Console default"
    product_code: str = ""
    from_cache: bool = False

    @property
    def display_label(self) -> str:
        cache_text = " (cached)" if self.from_cache else ""
        if self.sound_kind == "Console default":
            kind = f"{self.system} console startup"
        elif self.sound_kind == "Local console default":
            kind = f"{self.system} console startup (local)"
        else:
            kind = self.sound_kind
        return f"{kind}: {self.label} - {self.provider_name}{cache_text}"


def bundled_boot_sound_root() -> Path:
    """Return the source or PyInstaller location of bundled audio assets."""

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundle_root / "assets" / "boot_sounds"


def built_in_boot_sounds(system: str) -> list[BootSound]:
    """Return the offline sound choices packaged for a console.

    Only the two original generic chimes are bundled. Console recordings found
    through third-party sound sites are imported into the user's configuration
    directory instead of being redistributed in the application package.
    """

    system_name = str(system or "").strip()
    entries = list(BUNDLED_BOOT_SOUND_CATALOG.get(system_name, ()))
    entries.extend((
        ("generic_retro_startup.wav", "Generic retro startup (original)", "Built-in generic startup"),
        ("generic_modern_startup.wav", "Generic modern startup (original)", "Built-in generic startup"),
    ))
    sounds: list[BootSound] = []
    root = bundled_boot_sound_root()
    for filename, label, sound_kind in entries:
        path = root / filename
        try:
            usable = path.is_file() and path.stat().st_size > 44
        except OSError:
            usable = False
        if not usable:
            continue
        sounds.append(BootSound(
            provider_name=BUNDLED_BOOT_SOUND_PROVIDER,
            provider_url="",
            system=system_name,
            path=path,
            label=label,
            sound_kind=sound_kind,
        ))
    return sounds


def myinstants_search_url(system: str) -> str:
    """Return a browser search for additional user-selected sound downloads."""

    system_name = str(system or "").strip()
    query = MYINSTANTS_SEARCH_TERMS.get(system_name, f"{system_name} startup".strip())
    return f"https://www.myinstants.com/en/search/?name={quote_plus(query)}"


class RepositoryArtworkError(RuntimeError):
    pass


def product_code_candidates(value: str) -> list[str]:
    """Return conservative exact directory-name candidates from a product code."""
    raw = str(value or "").upper().strip()
    candidates: list[str] = []

    def add(candidate: str) -> None:
        candidate = re.sub(r"[^A-Z0-9]", "", candidate)
        if 4 <= len(candidate) <= 8 and candidate not in candidates:
            candidates.append(candidate)

    # Normal ROM/disc identifiers arrive as one four-to-six character value.
    # Conventional decorated forms such as AGB-A2CE-USA are split instead of
    # concatenated, because repository directory names use the inner code.
    if "-" in raw or "_" in raw:
        for token in re.findall(r"[A-Z0-9]+", raw):
            add(token)
    else:
        add(raw)
    return candidates


def system_has_repository(system: str) -> bool:
    return (
        any(system in provider.folders for provider in PROVIDERS)
        or system in LIBRETRO_THUMBNAIL_REPOSITORIES
        or system == "GameCube"
    )


def system_has_boot_sound_repository(system: str) -> bool:
    cucholix = next((provider for provider in PROVIDERS if provider.repository == "cucholix/wiivc-bis"), None)
    return bool((cucholix and system in cucholix.folders) or system == "GameCube")


def _request_json(url: str):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_INDEX_BYTES:
                raise RepositoryArtworkError("GitHub's artwork index exceeded the 48 MB safety limit.")
            data = response.read(MAX_INDEX_BYTES + 1)
            if len(data) > MAX_INDEX_BYTES:
                raise RepositoryArtworkError("GitHub's artwork index exceeded the 48 MB safety limit.")
            return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in {403, 429}:
            raise RepositoryArtworkError(
                "GitHub temporarily refused the artwork request or its unauthenticated rate limit was reached. Try again later."
            ) from exc
        raise RepositoryArtworkError(f"GitHub artwork lookup failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RepositoryArtworkError(f"Could not reach GitHub for artwork lookup: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepositoryArtworkError("GitHub returned an unreadable artwork directory response.") from exc


def _request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=25) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ARTWORK_BYTES:
                raise RepositoryArtworkError("The repository file exceeded the 24 MB safety limit.")
            chunks = []
            total = 0
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARTWORK_BYTES:
                    raise RepositoryArtworkError("The repository file exceeded the 24 MB safety limit.")
                chunks.append(chunk)
            return b"".join(chunks)
    except RepositoryArtworkError:
        raise
    except HTTPError as exc:
        raise RepositoryArtworkError(f"Repository download failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RepositoryArtworkError(f"Could not download the repository file: {exc}") from exc


def _request_github_file_bytes(repository: str, branch: str, path: str) -> bytes:
    """Use GitHub's media API as a fallback when a raw-content response is not an image."""

    encoded_path = quote(path, safe="/")
    encoded_branch = quote(branch, safe="")
    url = f"{GITHUB_API_ROOT}/{repository}/contents/{encoded_path}?ref={encoded_branch}"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ARTWORK_BYTES:
                raise RepositoryArtworkError("The repository file exceeded the 24 MB safety limit.")
            data = response.read(MAX_ARTWORK_BYTES + 1)
            if len(data) > MAX_ARTWORK_BYTES:
                raise RepositoryArtworkError("The repository file exceeded the 24 MB safety limit.")
            return data
    except RepositoryArtworkError:
        raise
    except HTTPError as exc:
        raise RepositoryArtworkError(f"GitHub media download failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RepositoryArtworkError(f"Could not download the repository image through GitHub's media API: {exc}") from exc


def _directory_entries(provider: ArtworkProvider, directory: str):
    encoded_path = quote(directory, safe="/")
    encoded_branch = quote(provider.branch, safe="")
    url = f"{GITHUB_API_ROOT}/{provider.repository}/contents/{encoded_path}?ref={encoded_branch}"
    payload = _request_json(url)
    return payload if isinstance(payload, list) else None


def _tree_index_path(cache_root: Path, provider: ArtworkProvider) -> Path:
    return cache_root / "repository_indexes" / f"{provider.cache_key}.json"


def _read_tree_index(cache_root: Path, provider: ArtworkProvider):
    path = _tree_index_path(cache_root, provider)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        directories = payload.get("directories")
        refreshed = float(payload.get("refreshed") or 0)
        if isinstance(directories, dict):
            return directories, refreshed
    except Exception:
        pass
    return None, 0.0


def _write_tree_index(cache_root: Path, provider: ArtworkProvider, directories: dict) -> None:
    path = _tree_index_path(cache_root, provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repository": provider.repository,
        "branch": provider.branch,
        "refreshed": time.time(),
        "directories": directories,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _download_url_for_tree_path(provider: ArtworkProvider, path: str) -> str:
    encoded_path = quote(path, safe="/")
    encoded_branch = quote(provider.branch, safe="")
    return f"https://raw.githubusercontent.com/{provider.repository}/{encoded_branch}/{encoded_path}"


def _provider_tree_index(cache_root: Path, provider: ArtworkProvider) -> dict | None:
    """Load one compact provider index so large ROM batches do not exhaust API limits."""
    cached, refreshed = _read_tree_index(cache_root, provider)
    if cached is not None and time.time() - refreshed <= TREE_INDEX_MAX_AGE_SECONDS:
        return cached

    encoded_branch = quote(provider.branch, safe="")
    url = f"{GITHUB_API_ROOT}/{provider.repository}/git/trees/{encoded_branch}?recursive=1"
    try:
        payload = _request_json(url)
    except RepositoryArtworkError:
        if cached is not None:
            return cached
        raise

    if not isinstance(payload, dict) or payload.get("truncated") is True or not isinstance(payload.get("tree"), list):
        # The exact-directory Contents endpoint remains a safe fallback when a
        # repository is too large for GitHub's recursive tree response.
        return cached

    directories: dict[str, list[dict[str, str]]] = {}
    accepted_stems = {"icontex", "boottvtex", "bootdrctex"}
    for item in payload["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        name = Path(path).name
        if Path(name).suffix.lower() not in IMAGE_EXTENSIONS or Path(name).stem.casefold() not in accepted_stems:
            continue
        directory = str(Path(path).parent).replace("\\", "/")
        directories.setdefault(directory, []).append({
            "type": "file",
            "name": name,
            "download_url": _download_url_for_tree_path(provider, path),
        })

    _write_tree_index(cache_root, provider, directories)
    return directories


def _image_entry(entries, stem: str):
    wanted = stem.casefold()
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        name = str(entry.get("name") or "")
        if Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if Path(name).stem.casefold() != wanted:
            continue
        download_url = str(entry.get("download_url") or "").strip()
        if download_url.startswith("https://"):
            return download_url
    return None


def _valid_cached_image(path: Path | None) -> bool:
    if not path or not path.is_file():
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _cached_match(cache_directory: Path) -> RepositoryArtwork | None:
    manifest_path = cache_directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
        icon = cache_directory / "icon.png" if manifest.get("icon") else None
        tv = cache_directory / "tv.png" if manifest.get("tv") else None
        gamepad = cache_directory / "gamepad.png" if manifest.get("gamepad") else None
        source_image = cache_directory / "source.png" if manifest.get("source_image") else None
        icon = icon if _valid_cached_image(icon) else None
        tv = tv if _valid_cached_image(tv) else None
        gamepad = gamepad if _valid_cached_image(gamepad) else None
        source_image = source_image if _valid_cached_image(source_image) else None
        if not (icon or tv or gamepad or source_image):
            return None
        match_method = str(manifest.get("match_method") or "Exact product code")
        confidence_value = manifest.get("confidence")
        confidence = float(confidence_value) if confidence_value is not None else (1.0 if match_method == "Exact product code" else 0.0)
        return RepositoryArtwork(
            provider_name=str(manifest.get("provider_name") or "Community repository"),
            provider_url=str(manifest.get("provider_url") or ""),
            product_code=str(manifest.get("product_code") or ""),
            system=str(manifest.get("system") or ""),
            icon_path=icon,
            tv_path=tv,
            gamepad_path=gamepad,
            source_image_path=source_image,
            source_image_kind=str(manifest.get("source_image_kind") or ""),
            matched_title=str(manifest.get("matched_title") or ""),
            match_method=match_method,
            confidence=confidence,
            from_cache=True,
        )
    except Exception:
        return None


def _decode_and_save(data: bytes, destination: Path) -> None:
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
            image = source.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RepositoryArtworkError("The repository returned a file that is not usable image artwork.") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        image.save(temporary, format="PNG", optimize=True)
        temporary.replace(destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _download_source_image(
    entry: dict[str, str],
    repository: str,
    branch: str,
    destination: Path,
) -> None:
    """Download and validate an image, retrying through GitHub's media API."""

    first_error: RepositoryArtworkError | None = None
    try:
        _decode_and_save(_request_bytes(entry["download_url"]), destination)
        return
    except RepositoryArtworkError as exc:
        first_error = exc

    try:
        data = _request_github_file_bytes(repository, branch, str(entry.get("path") or ""))
        _decode_and_save(data, destination)
    except RepositoryArtworkError as exc:
        name = str(entry.get("name") or entry.get("path") or "repository file")
        raise RepositoryArtworkError(
            f"{name} exists in the repository but could not be decoded as an image after two download methods. "
            f"First attempt: {first_error}. Fallback: {exc}"
        ) from exc


def _download_match(
    provider: ArtworkProvider,
    system: str,
    product_code: str,
    entries,
    cache_root: Path,
) -> RepositoryArtwork | None:
    urls = {
        "icon": _image_entry(entries, "iconTex"),
        "tv": _image_entry(entries, "bootTvTex"),
        "gamepad": _image_entry(entries, "bootDrcTex"),
    }
    if not any(urls.values()):
        return None

    cache_directory = cache_root / provider.cache_key / re.sub(r"[^a-z0-9]+", "_", system.lower()).strip("_") / product_code
    paths: dict[str, Path | None] = {"icon": None, "tv": None, "gamepad": None}
    for kind, url in urls.items():
        if not url:
            continue
        destination = cache_directory / f"{kind}.png"
        _decode_and_save(_request_bytes(url), destination)
        paths[kind] = destination

    manifest = {
        "provider_name": provider.name,
        "provider_url": provider.repository_url,
        "product_code": product_code,
        "system": system,
        "icon": bool(paths["icon"]),
        "tv": bool(paths["tv"]),
        "gamepad": bool(paths["gamepad"]),
        "match_method": "Exact product code",
        "confidence": 1.0,
        "notice": "Community-hosted artwork downloaded on demand. Review the source repository terms before redistribution.",
    }
    cache_directory.mkdir(parents=True, exist_ok=True)
    (cache_directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return RepositoryArtwork(
        provider_name=provider.name,
        provider_url=provider.repository_url,
        product_code=product_code,
        system=system,
        icon_path=paths["icon"],
        tv_path=paths["tv"],
        gamepad_path=paths["gamepad"],
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").casefold()).strip("_") or "unknown"


def _source_index_path(cache_root: Path, repository: str) -> Path:
    return cache_root / "repository_indexes" / f"source_{_slug(repository)}.json"


def _read_source_index(cache_root: Path, repository: str):
    path = _source_index_path(cache_root, repository)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries")
        refreshed = float(payload.get("refreshed") or 0)
        if isinstance(entries, list):
            return entries, refreshed
    except Exception:
        pass
    return None, 0.0


def _write_source_index(cache_root: Path, repository: str, branch: str, entries: list[dict[str, str]]) -> None:
    path = _source_index_path(cache_root, repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repository": repository,
        "branch": branch,
        "refreshed": time.time(),
        "entries": entries,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _download_url(repository: str, branch: str, path: str) -> str:
    encoded_path = quote(path, safe="/")
    encoded_branch = quote(branch, safe="")
    return f"https://raw.githubusercontent.com/{repository}/{encoded_branch}/{encoded_path}"


def _repository_source_entries(
    cache_root: Path,
    repository: str,
    branch: str,
    accept,
) -> list[dict[str, str]]:
    cached, refreshed = _read_source_index(cache_root, repository)
    if cached is not None and time.time() - refreshed <= TREE_INDEX_MAX_AGE_SECONDS:
        return cached

    encoded_branch = quote(branch, safe="")
    url = f"{GITHUB_API_ROOT}/{repository}/git/trees/{encoded_branch}?recursive=1"
    try:
        payload = _request_json(url)
    except RepositoryArtworkError:
        if cached is not None:
            return cached
        raise

    if not isinstance(payload, dict) or payload.get("truncated") is True or not isinstance(payload.get("tree"), list):
        return cached or []

    entries: list[dict[str, str]] = []
    for item in payload["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if Path(path).suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        extra = accept(path)
        if not isinstance(extra, dict):
            continue
        entries.append({
            "path": path,
            "name": Path(path).name,
            "download_url": _download_url(repository, branch, path),
            **{str(key): str(value) for key, value in extra.items()},
        })
    _write_source_index(cache_root, repository, branch, entries)
    return entries


def _audio_index_path(cache_root: Path, repository: str) -> Path:
    return cache_root / "repository_indexes" / f"audio_{_slug(repository)}.json"


def _read_audio_index(cache_root: Path, repository: str):
    path = _audio_index_path(cache_root, repository)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries")
        refreshed = float(payload.get("refreshed") or 0)
        if isinstance(entries, list):
            return entries, refreshed
    except Exception:
        pass
    return None, 0.0


def _write_audio_index(cache_root: Path, repository: str, branch: str, entries: list[dict[str, str]]) -> None:
    path = _audio_index_path(cache_root, repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repository": repository,
        "branch": branch,
        "refreshed": time.time(),
        "entries": entries,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _repository_audio_entries(cache_root: Path, repository: str, branch: str) -> list[dict[str, str]]:
    cached, refreshed = _read_audio_index(cache_root, repository)
    if cached is not None and time.time() - refreshed <= TREE_INDEX_MAX_AGE_SECONDS:
        return cached

    encoded_branch = quote(branch, safe="")
    url = f"{GITHUB_API_ROOT}/{repository}/git/trees/{encoded_branch}?recursive=1"
    try:
        payload = _request_json(url)
    except RepositoryArtworkError:
        if cached is not None:
            return cached
        raise
    if not isinstance(payload, dict) or payload.get("truncated") is True or not isinstance(payload.get("tree"), list):
        return cached or []

    entries: list[dict[str, str]] = []
    for item in payload["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if Path(path).suffix.casefold() not in BOOT_SOUND_EXTENSIONS:
            continue
        entries.append({
            "path": path,
            "name": Path(path).name,
            "download_url": _download_url(repository, branch, path),
        })
    _write_audio_index(cache_root, repository, branch, entries)
    return entries


_REGION_GROUPS = {
    "usa": {"usa", "united states", "north america"},
    "europe": {"europe", "european", "pal", "eur", "germany", "france", "spain", "italy", "netherlands"},
    "japan": {"japan", "japanese", "jpn"},
    "korea": {"korea", "korean", "kor"},
    "australia": {"australia", "australian", "aus"},
    "world": {"world", "global"},
}


def _canonical_region(value: str) -> str:
    normalized = re.sub(r"[^a-z]+", " ", (value or "").casefold()).strip()
    for canonical, aliases in _REGION_GROUPS.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return ""


def _regions_in_title(value: str) -> set[str]:
    # Libretro region labels are normally parenthesized or bracketed. Limiting
    # detection to those tags prevents ordinary title words from being
    # mistaken for a region.
    groups = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", value or "")
    found: set[str] = set()
    for pair in groups:
        normalized = re.sub(r"[^a-z]+", " ", " ".join(pair).casefold())
        for canonical, aliases in _REGION_GROUPS.items():
            if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases | {canonical}):
                found.add(canonical)
    return found


def _metadata_group(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", (value or "").casefold()).strip()
    if not normalized:
        return False
    for canonical, aliases in _REGION_GROUPS.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases | {canonical}):
            return True
    if re.fullmatch(r"(?:[a-z]{2})(?:,[a-z]{2})*", normalized):
        return True
    return bool(re.match(
        r"^(?:rev(?:ision)?\b|v\d|version\b|beta\b|proto(?:type)?\b|demo\b|sample\b|"
        r"unl(?:icensed)?\b|aftermarket\b|pirate\b|virtual console\b|wii virtual console\b|"
        r"switch online\b|kiosk\b|alt\b|program\b|disc\s*\d+\b|disk\s*\d+\b|\d{4}-\d{2}-\d{2}$)",
        normalized,
    ))


def _strip_title_tags(value: str) -> str:
    raw = str(value or "").strip()
    suffix = Path(raw).suffix.casefold()
    if suffix in IMAGE_EXTENSIONS:
        raw = raw[: -len(suffix)].rstrip()
    raw = re.sub(r"(?i)\s+(?:banner|icon)$", "", raw).strip()

    def remove_group(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        return "" if _metadata_group(content) else match.group(0)

    previous = None
    while raw != previous:
        previous = raw
        raw = re.sub(r"\(([^()]*)\)", remove_group, raw)
        raw = re.sub(r"\[([^\[\]]*)\]", remove_group, raw)
    return re.sub(r"\s+", " ", raw).strip(" -_.")


def normalized_title_key(value: str) -> str:
    raw = _strip_title_tags(value)
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", errors="ignore").decode("ascii")
    raw = raw.casefold().replace("&", " and ")
    if raw.endswith(", the"):
        raw = "the " + raw[:-5]
    elif raw.endswith(", a"):
        raw = "a " + raw[:-3]
    elif raw.endswith(", an"):
        raw = "an " + raw[:-4]
    raw = re.sub(r"^(?:the|a|an)\s+", "", raw)
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) < 4:
        return 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    union = left_tokens | right_tokens
    token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
    score = sequence * 0.72 + token_score * 0.28
    if len(left_tokens) >= 2 and len(right_tokens) >= 2 and (left in right or right in left):
        score = max(score, 0.88)
    return min(1.0, score)


def _select_title_match(
    entries: list[dict[str, str]],
    title_candidates: list[str],
    region: str,
    *,
    allow_uncertain: bool = False,
) -> tuple[dict[str, str], str, float, str] | None:
    queries: list[str] = []
    for title in title_candidates:
        key = normalized_title_key(title)
        if key and key not in queries:
            queries.append(key)
    if not queries:
        return None

    wanted_region = _canonical_region(region)
    ranked: list[tuple[float, float, str, dict[str, str], str, str]] = []
    for entry in entries:
        raw_title = str(entry.get("match_title") or Path(str(entry.get("name") or "")).stem)
        entry_key = normalized_title_key(raw_title)
        if not entry_key:
            continue
        best_similarity = max(_title_similarity(query, entry_key) for query in queries)
        if best_similarity < (0.68 if allow_uncertain else 0.84):
            continue
        regions = _regions_in_title(raw_title)
        region_adjustment = 0.0
        if wanted_region and regions:
            region_adjustment = 0.045 if wanted_region in regions else -0.075
        kind = str(entry.get("art_kind") or "")
        kind_priority = LIBRETRO_ART_KIND_PRIORITY.get(kind, 0)
        rank = best_similarity + region_adjustment + kind_priority * 0.004
        confidence = max(0.0, min(1.0, best_similarity + max(0.0, region_adjustment) * 0.5))
        ranked.append((rank, confidence, entry_key, entry, raw_title, wanted_region if wanted_region in regions else ""))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], str(item[3].get("path") or "").casefold()))
    best = ranked[0]
    if best[1] < (0.68 if allow_uncertain else 0.86):
        return None

    # Approximate matches must lead the next different game by a useful margin.
    # Region variants of the same game do not count as ambiguity.
    if not allow_uncertain and best[1] < 0.995:
        runner_up = next((item for item in ranked[1:] if item[2] != best[2]), None)
        if runner_up is not None and best[0] - runner_up[0] < 0.045:
            return None

    if best[1] < 0.86:
        method = "Possible title match - manual review required"
    elif best[1] >= 0.995:
        method = "Exact normalized title and region" if best[5] else "Exact normalized title"
    else:
        method = "Approximate title and region" if best[5] else "Approximate title"
    return best[3], best[4], best[1], method


def _select_title_matches(
    entries: list[dict[str, str]],
    title_candidates: list[str],
    region: str,
    *,
    allow_uncertain: bool = False,
) -> list[tuple[dict[str, str], str, float, str]]:
    """Return the safest matching game's best file for every artwork kind."""
    primary = _select_title_match(
        entries,
        title_candidates,
        region,
        allow_uncertain=allow_uncertain,
    )
    if primary is None:
        return []
    target_key = normalized_title_key(primary[1])
    matching_game = [
        entry
        for entry in entries
        if normalized_title_key(str(entry.get("match_title") or Path(str(entry.get("name") or "")).stem))
        == target_key
    ]
    kinds = sorted(
        {str(entry.get("art_kind") or "Artwork") for entry in matching_game},
        key=lambda kind: (-LIBRETRO_ART_KIND_PRIORITY.get(kind, 0), kind.casefold()),
    )
    selected: list[tuple[dict[str, str], str, float, str]] = []
    for kind in kinds:
        candidate = _select_title_match(
            [entry for entry in matching_game if str(entry.get("art_kind") or "Artwork") == kind],
            title_candidates,
            region,
            allow_uncertain=allow_uncertain,
        )
        if candidate is not None:
            selected.append(candidate)
    return selected


def _source_cache_directory(
    cache_root: Path,
    provider_key: str,
    system: str,
    matched_title: str,
    region: str,
) -> Path:
    key = normalized_title_key(matched_title) or _slug(matched_title)
    suffix = _canonical_region(region)
    if suffix:
        key = f"{key}_{suffix}"
    return cache_root / _slug(provider_key) / _slug(system) / _slug(key)


def _download_libretro_artworks(
    system: str,
    title_candidates: list[str],
    region: str,
    product_code: str,
    cache_root: Path,
    *,
    include_uncertain_matches: bool = False,
) -> list[RepositoryArtwork]:
    repository = LIBRETRO_THUMBNAIL_REPOSITORIES.get(system)
    if not repository:
        return []

    def accept(path: str):
        parts = path.replace("\\", "/").split("/")
        if len(parts) != 2 or parts[0] not in LIBRETRO_ART_KIND_PRIORITY:
            return None
        return {"art_kind": parts[0], "match_title": Path(parts[1]).stem}

    entries = _repository_source_entries(cache_root, repository, "master", accept)
    results: list[RepositoryArtwork] = []
    download_errors: list[str] = []
    for entry, matched_title, confidence, method in _select_title_matches(
        entries,
        title_candidates,
        region,
        allow_uncertain=include_uncertain_matches,
    ):
        kind = str(entry.get("art_kind") or "")
        kind_label = {
            "Named_Titles": "Title screen",
            "Named_Snaps": "Gameplay screenshot",
            "Named_Boxarts": "Box art",
            "Named_Logos": "Game logo",
        }.get(kind, "Artwork")
        cache_directory = _source_cache_directory(
            cache_root,
            f"{repository}/{kind}",
            system,
            matched_title,
            region,
        )
        cached = _cached_match(cache_directory)
        if cached:
            results.append(cached)
            continue

        source_path = cache_directory / "source.png"
        try:
            _download_source_image(entry, repository, "master", source_path)
        except RepositoryArtworkError as exc:
            download_errors.append(f"{kind_label}: {exc}")
            continue
        manifest = {
            "provider_name": "Libretro Thumbnails",
            "provider_url": f"https://github.com/{repository}",
            "product_code": product_code,
            "system": system,
            "icon": False,
            "tv": False,
            "gamepad": False,
            "source_image": True,
            "source_image_kind": kind_label,
            "matched_title": matched_title,
            "match_method": method,
            "confidence": round(confidence, 4),
            "notice": "Community-hosted source artwork downloaded on demand and rendered by the application.",
        }
        cache_directory.mkdir(parents=True, exist_ok=True)
        (cache_directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        results.append(RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url=f"https://github.com/{repository}",
            product_code=product_code,
            system=system,
            source_image_path=source_path,
            source_image_kind=kind_label,
            matched_title=matched_title,
            match_method=method,
            confidence=confidence,
        ))
    if not results and download_errors:
        raise RepositoryArtworkError("; ".join(download_errors))
    return results


def _download_libretro_artwork(
    system: str,
    title_candidates: list[str],
    region: str,
    product_code: str,
    cache_root: Path,
) -> RepositoryArtwork | None:
    results = _download_libretro_artworks(system, title_candidates, region, product_code, cache_root)
    return results[0] if results else None


def _download_gamecube_collection_artwork(
    title_candidates: list[str],
    region: str,
    product_code: str,
    cache_root: Path,
) -> RepositoryArtwork | None:
    def accept(path: str):
        normalized = path.replace("\\", "/")
        if not normalized.casefold().startswith("artwork/"):
            return None
        stem = Path(path).stem
        match = re.match(r"(?is)^(.+?)\s+(banner|icon)$", stem)
        if not match:
            return None
        return {"art_kind": match.group(2).title(), "match_title": match.group(1).strip()}

    entries = _repository_source_entries(cache_root, GAMECUBE_ARTWORK_REPOSITORY, "master", accept)
    selected = _select_title_match(entries, title_candidates, region)
    if not selected:
        return None
    chosen, matched_title, confidence, method = selected
    matched_key = normalized_title_key(matched_title)
    siblings = [entry for entry in entries if normalized_title_key(entry.get("match_title", "")) == matched_key]
    icon_entry = next((entry for entry in siblings if entry.get("art_kind") == "Icon"), None)
    banner_entry = next((entry for entry in siblings if entry.get("art_kind") == "Banner"), None)
    if not (icon_entry or banner_entry):
        return None

    cache_directory = _source_cache_directory(
        cache_root, GAMECUBE_ARTWORK_REPOSITORY, "GameCube", matched_title, region
    )
    cached = _cached_match(cache_directory)
    if cached:
        return cached

    icon_path = cache_directory / "icon.png" if icon_entry else None
    tv_path = cache_directory / "tv.png" if banner_entry else None
    if icon_entry and icon_path:
        _decode_and_save(_request_bytes(icon_entry["download_url"]), icon_path)
    if banner_entry and tv_path:
        _decode_and_save(_request_bytes(banner_entry["download_url"]), tv_path)
    manifest = {
        "provider_name": "GameCube Inject Artwork",
        "provider_url": GAMECUBE_ARTWORK_URL,
        "product_code": product_code,
        "system": "GameCube",
        "icon": bool(icon_path),
        "tv": bool(tv_path),
        "gamepad": False,
        "source_image": False,
        "source_image_kind": "Finished Wii U icon and banner",
        "matched_title": matched_title,
        "match_method": method,
        "confidence": round(confidence, 4),
        "notice": "Community-hosted artwork downloaded on demand. Review source terms before redistribution.",
    }
    cache_directory.mkdir(parents=True, exist_ok=True)
    (cache_directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return RepositoryArtwork(
        provider_name="GameCube Inject Artwork",
        provider_url=GAMECUBE_ARTWORK_URL,
        product_code=product_code,
        system="GameCube",
        icon_path=icon_path,
        tv_path=tv_path,
        source_image_kind="Finished Wii U icon and banner",
        matched_title=matched_title,
        match_method=method,
        confidence=confidence,
    )


def find_and_download_artwork_candidates(
    system: str,
    product_code: str,
    cache_root: Path,
    *,
    title_candidates: list[str] | tuple[str, ...] = (),
    region: str = "",
    allow_title_fallback: bool = True,
    search_libretro: bool = True,
    search_gamecube_collection: bool = True,
    include_uncertain_matches: bool = False,
    max_results: int = 12,
) -> list[RepositoryArtwork]:
    """Collect safe artwork matches while preserving best-first source priority."""
    codes = product_code_candidates(product_code)
    if not system_has_repository(system) or max_results < 1:
        return []

    errors: list[str] = []
    results: list[RepositoryArtwork] = []

    def add(match: RepositoryArtwork | None) -> bool:
        if match is None:
            return False
        identity = (
            match.provider_name,
            str(match.icon_path or ""),
            str(match.tv_path or ""),
            str(match.gamepad_path or ""),
            str(match.source_image_path or ""),
            match.source_image_kind,
        )
        existing = {
            (
                item.provider_name,
                str(item.icon_path or ""),
                str(item.tv_path or ""),
                str(item.gamepad_path or ""),
                str(item.source_image_path or ""),
                item.source_image_kind,
            )
            for item in results
        }
        if identity not in existing:
            results.append(match)
        return len(results) >= max_results

    if codes:
        for provider in PROVIDERS:
            if system not in provider.folders:
                continue
            try:
                tree_index = _provider_tree_index(cache_root, provider)
            except RepositoryArtworkError as exc:
                tree_index = None
                errors.append(f"{provider.name}: {exc}")
            for code in codes:
                cache_directory = cache_root / provider.cache_key / _slug(system) / code
                cached = _cached_match(cache_directory)
                if cached:
                    if add(cached):
                        return results
                    continue

                directory = provider.directory(system, code)
                if not directory:
                    continue
                try:
                    entries = tree_index.get(directory, []) if tree_index is not None else _directory_entries(provider, directory)
                    if not entries:
                        continue
                    match = _download_match(provider, system, code, entries, cache_root)
                    if add(match):
                        return results
                except RepositoryArtworkError as exc:
                    errors.append(f"{provider.name}: {exc}")

    titles = [str(value).strip() for value in title_candidates if str(value).strip()]
    if allow_title_fallback and titles:
        if system == "GameCube" and search_gamecube_collection:
            try:
                match = _download_gamecube_collection_artwork(titles, region, product_code, cache_root)
                if add(match):
                    return results
            except RepositoryArtworkError as exc:
                errors.append(f"GameCube Inject Artwork: {exc}")
        if search_libretro and system in LIBRETRO_THUMBNAIL_REPOSITORIES:
            try:
                for match in _download_libretro_artworks(
                    system,
                    titles,
                    region,
                    product_code,
                    cache_root,
                    include_uncertain_matches=include_uncertain_matches,
                ):
                    if add(match):
                        return results
            except RepositoryArtworkError as exc:
                errors.append(f"Libretro Thumbnails: {exc}")

    if not results and errors:
        raise RepositoryArtworkError("; ".join(errors))
    return results


def find_and_download_artwork(
    system: str,
    product_code: str,
    cache_root: Path,
    *,
    title_candidates: list[str] | tuple[str, ...] = (),
    region: str = "",
    allow_title_fallback: bool = True,
    search_libretro: bool = True,
    search_gamecube_collection: bool = True,
) -> RepositoryArtwork | None:
    """Compatibility wrapper returning only the highest-priority match."""
    matches = find_and_download_artwork_candidates(
        system,
        product_code,
        cache_root,
        title_candidates=title_candidates,
        region=region,
        allow_title_fallback=allow_title_fallback,
        search_libretro=search_libretro,
        search_gamecube_collection=search_gamecube_collection,
        max_results=1,
    )
    return matches[0] if matches else None


def _download_boot_sound_entry(
    entry: dict[str, str],
    *,
    cache_root: Path,
    provider_name: str,
    provider_url: str,
    provider_key: str,
    system: str,
    sound_kind: str,
    product_code: str = "",
) -> BootSound:
    source_name = str(entry.get("name") or Path(str(entry.get("path") or "bootSound.btsnd")).name)
    suffix = Path(source_name).suffix.casefold()
    if suffix not in BOOT_SOUND_EXTENSIONS:
        raise RepositoryArtworkError("The repository sound format is not supported.")
    path_key = _slug(str(entry.get("path") or source_name))
    destination = (
        cache_root
        / "boot_sounds"
        / _slug(provider_key)
        / _slug(system)
        / _slug(sound_kind)
        / f"{path_key}{suffix}"
    )
    from_cache = destination.is_file() and destination.stat().st_size > 0
    if not from_cache:
        data = _request_bytes(str(entry.get("download_url") or ""))
        if not data:
            raise RepositoryArtworkError("The repository returned an empty boot-sound file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(destination)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    label = Path(str(entry.get("path") or source_name)).stem.replace("_", " ").replace("-", " ")
    label = re.sub(r"\s+", " ", label).strip() or "Boot sound"
    if sound_kind == "Console default" and label.casefold() == "bootsound":
        label = f"{system} startup sound"
    return BootSound(
        provider_name=provider_name,
        provider_url=provider_url,
        system=system,
        path=destination,
        label=label,
        sound_kind=sound_kind,
        product_code=product_code,
        from_cache=from_cache,
    )


def find_and_download_boot_sounds(
    system: str,
    product_code: str,
    cache_root: Path,
    *,
    max_results: int = 8,
) -> list[BootSound]:
    """Download exact per-game sounds first, then safe console defaults when available."""
    if max_results < 1 or not system_has_boot_sound_repository(system):
        return []
    codes = product_code_candidates(product_code)
    results: list[BootSound] = []
    errors: list[str] = []

    def add_entries(
        entries: list[dict[str, str]],
        *,
        provider_name: str,
        provider_url: str,
        provider_key: str,
        sound_kind: str,
        matched_code: str = "",
    ) -> None:
        for entry in entries:
            try:
                sound = _download_boot_sound_entry(
                    entry,
                    cache_root=cache_root,
                    provider_name=provider_name,
                    provider_url=provider_url,
                    provider_key=provider_key,
                    system=system,
                    sound_kind=sound_kind,
                    product_code=matched_code,
                )
            except RepositoryArtworkError as exc:
                errors.append(f"{provider_name}: {exc}")
                continue
            if all(item.path != sound.path for item in results):
                results.append(sound)
            if len(results) >= max_results:
                return

    cucholix = next((provider for provider in PROVIDERS if provider.repository == "cucholix/wiivc-bis"), None)
    if cucholix and system in cucholix.folders:
        try:
            entries = _repository_audio_entries(cache_root, cucholix.repository, cucholix.branch)
            folder = cucholix.folders[system].strip("/")
            sound_prefix = f"{folder}/sound/"
            system_entries = [
                entry
                for entry in entries
                if str(entry.get("path") or "").replace("\\", "/").casefold().startswith(sound_prefix.casefold())
            ]
            per_game: list[tuple[str, dict[str, str]]] = []
            generic: list[dict[str, str]] = []
            for entry in system_entries:
                path = str(entry.get("path") or "").replace("\\", "/")
                normalized_parts = {
                    re.sub(r"[^a-z0-9]", "", part.casefold())
                    for part in Path(path).parts
                }
                matched_code = next(
                    (
                        code
                        for code in codes
                        if any(
                            re.sub(r"[^a-z0-9]", "", code.casefold()) == part
                            or re.sub(r"[^a-z0-9]", "", code.casefold()) in part
                            for part in normalized_parts
                        )
                    ),
                    "",
                )
                if matched_code:
                    per_game.append((matched_code, entry))
                    continue
                relative = path[len(sound_prefix):] if path.casefold().startswith(sound_prefix.casefold()) else path
                relative_parts = relative.split("/")
                if len(relative_parts) == 1 or relative_parts[0].casefold() in {"default", "generic", "boot", "gcnboot"}:
                    generic.append(entry)
            per_game.sort(key=lambda item: str(item[1].get("path") or "").casefold())
            generic.sort(key=lambda item: (Path(str(item.get("path") or "")).name.casefold() != "bootsound.btsnd", str(item.get("path") or "").casefold()))
            for code in codes:
                add_entries(
                    [entry for matched, entry in per_game if matched == code],
                    provider_name=cucholix.name,
                    provider_url=cucholix.repository_url,
                    provider_key=cucholix.repository,
                    sound_kind="Per-game",
                    matched_code=code,
                )
                if len(results) >= max_results:
                    return results
            add_entries(
                generic,
                provider_name=cucholix.name,
                provider_url=cucholix.repository_url,
                provider_key=cucholix.repository,
                sound_kind="Console default",
            )
        except RepositoryArtworkError as exc:
            errors.append(f"{cucholix.name}: {exc}")

    if system == "GameCube" and len(results) < max_results:
        try:
            entries = _repository_audio_entries(cache_root, GAMECUBE_ARTWORK_REPOSITORY, "master")
            generic = [
                entry
                for entry in entries
                if Path(str(entry.get("path") or "")).name.casefold()
                in {"bootsound_gc.wav", "bootsound_gc_antifade.wav"}
            ]
            generic.sort(key=lambda entry: "antifade" in Path(str(entry.get("path") or "")).stem.casefold())
            add_entries(
                generic,
                provider_name="GameCube Inject Artwork",
                provider_url=GAMECUBE_ARTWORK_URL,
                provider_key=GAMECUBE_ARTWORK_REPOSITORY,
                sound_kind="Console default",
            )
        except RepositoryArtworkError as exc:
            errors.append(f"GameCube Inject Artwork: {exc}")

    if not results and errors:
        raise RepositoryArtworkError("; ".join(errors))
    return results


def _save_exact_image(source_path: Path, destination: Path, size: tuple[int, int]) -> None:
    with Image.open(source_path) as source:
        source.load()
        has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
        image = source.convert("RGBA" if has_alpha else "RGB")
        if image.size != size:
            image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        image.save(temporary, format="PNG", optimize=True)
        temporary.replace(destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def apply_repository_artwork(
    artwork: RepositoryArtwork,
    output_directory: Path,
    *,
    create_icon: bool = True,
    create_tv: bool = True,
    create_gamepad: bool = True,
    preserve_disabled: bool = False,
) -> list[Path]:
    """Write repository art to the application's standard output filenames."""
    outputs = {
        "icon": output_directory / "Icon Image.png",
        "tv": output_directory / "TV Image.png",
        "gamepad": output_directory / "Gamepad Image.png",
    }
    enabled = {"icon": create_icon, "tv": create_tv, "gamepad": create_gamepad}
    for kind, is_enabled in enabled.items():
        if not is_enabled and not preserve_disabled:
            try:
                outputs[kind].unlink(missing_ok=True)
            except OSError:
                pass

    written: list[Path] = []
    if create_icon and artwork.icon_path:
        _save_exact_image(artwork.icon_path, outputs["icon"], (128, 128))
        written.append(outputs["icon"])
    if create_tv and artwork.tv_path:
        _save_exact_image(artwork.tv_path, outputs["tv"], (1280, 720))
        written.append(outputs["tv"])
    gamepad_source = artwork.gamepad_path or artwork.tv_path
    if create_gamepad and gamepad_source:
        _save_exact_image(gamepad_source, outputs["gamepad"], (854, 480))
        written.append(outputs["gamepad"])
    return written
