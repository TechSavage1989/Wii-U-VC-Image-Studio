from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
import zipfile
import xml.etree.ElementTree as ET

from .config import config_dir


RAW_ROOT = "https://raw.githubusercontent.com/libretro/libretro-database/master"
USER_AGENT = "Wii-U-VC-Image-Builder/5.06.0"

SYSTEM_DATABASE_NAMES = {
    "Nintendo DS": "Nintendo - Nintendo DS",
    "Game Boy Advance": "Nintendo - Game Boy Advance",
    "Game Boy Color": "Nintendo - Game Boy Color",
    "Game Boy": "Nintendo - Game Boy",
    "NES": "Nintendo - Nintendo Entertainment System",
    "SNES": "Nintendo - Super Nintendo Entertainment System",
    "Nintendo 64": "Nintendo - Nintendo 64",
    "GameCube": "Nintendo - GameCube",
    "Wii": "Nintendo - Wii",
    "WiiWare": "Nintendo - Wii (Digital)",
    "MSX": "Microsoft - MSX",
    "TurboGrafx-CD / PC Engine CD": "NEC - PC Engine CD - TurboGrafx-CD",
    "Genesis / Mega Drive": "Sega - Mega Drive - Genesis",
    "Master System": "Sega - Master System - Mark III",
    "Game Gear": "Sega - Game Gear",
    "TurboGrafx-16 / PC Engine": "NEC - PC Engine - TurboGrafx 16",
}

# Metadata DATs are intentionally optional. Libretro's coverage differs by system.
OPTIONAL_METADATA_FOLDERS = (
    "releaseyear",
    "maxusers",
    "developer",
    "publisher",
)

NOINTRO_SYSTEMS = {
    "Nintendo DS", "Game Boy Advance", "Game Boy Color", "Game Boy", "NES", "SNES",
    "Nintendo 64", "MSX", "Genesis / Mega Drive", "Master System", "Game Gear",
    "TurboGrafx-16 / PC Engine",
}
REDUMP_SYSTEMS = {"TurboGrafx-CD / PC Engine CD"}

GAMETDB_URLS = {
    "Nintendo DS": "https://www.gametdb.com/dstdb.zip?LANG=EN",
    "GameCube": "https://www.gametdb.com/wiitdb.zip?LANG=EN",
    "Wii": "https://www.gametdb.com/wiitdb.zip?LANG=EN",
    "WiiWare": "https://www.gametdb.com/wiitdb.zip?LANG=EN",
}

_GAMETDB_INDEX_CACHE: dict[tuple[str, float], dict[str, dict[str, str]]] = {}
METADATA_CACHE_SCHEMA = 2


@dataclass
class LibretroSettings:
    timeout: float = 25.0
    cache_days: int = 14

    @property
    def configured(self) -> bool:
        # Public GitHub-hosted database. No account or API key is required.
        return True


@dataclass
class RomHashes:
    crc32: str
    md5: str
    sha1: str
    size: int


@dataclass
class GameMetadata:
    title: str = ""
    release_date: str = ""
    release_year: str = ""
    players: str = ""
    publisher: str = ""
    developer: str = ""
    game_id: str = ""
    rom_id: str = ""
    source: str = "Libretro Database"
    crc32: str = ""
    md5: str = ""
    sha1: str = ""


def compute_hashes(path: Path, chunk_size: int = 4 * 1024 * 1024) -> RomHashes:
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return RomHashes(
        f"{crc & 0xFFFFFFFF:08X}",
        md5.hexdigest().upper(),
        sha1.hexdigest().upper(),
        size,
    )


def _hash_bytes(data: bytes) -> RomHashes:
    return RomHashes(
        f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
        hashlib.md5(data).hexdigest().upper(),
        hashlib.sha1(data).hexdigest().upper(),
        len(data),
    )


def compute_normalized_hash_variants(path: Path, system: str) -> list[RomHashes]:
    """Return header/byte-order normalized hashes used by common preservation DATs."""
    variants: list[RomHashes] = []
    try:
        size = path.stat().st_size
        # Cartridge formats are small enough to normalize in memory. Avoid doing
        # this for disc/container formats.
        if size > 128 * 1024 * 1024:
            return variants
        data = path.read_bytes()
    except Exception:
        return variants

    if system == "NES" and len(data) > 16 and data[:4] == b"NES\x1a":
        trainer = 512 if (data[6] & 0x04) else 0
        start = 16 + trainer
        if start < len(data):
            variants.append(_hash_bytes(data[start:]))
    elif system == "SNES" and len(data) > 512 and len(data) % 0x8000 == 512:
        variants.append(_hash_bytes(data[512:]))
    elif system == "Nintendo 64" and len(data) >= 4:
        magic = data[:4]
        normalized = None
        if magic == bytes.fromhex("37804012"):
            b = bytearray(data)
            for i in range(0, len(b) - 1, 2):
                b[i], b[i + 1] = b[i + 1], b[i]
            normalized = bytes(b)
        elif magic == bytes.fromhex("40123780"):
            b = bytearray(data)
            for i in range(0, len(b) - 3, 4):
                b[i:i + 4] = reversed(b[i:i + 4])
            normalized = bytes(b)
        if normalized is not None:
            variants.append(_hash_bytes(normalized))

    # De-duplicate variants that happen to equal one another.
    seen = set()
    unique = []
    for hashes in variants:
        key = (hashes.sha1, hashes.md5, hashes.crc32, hashes.size)
        if key not in seen:
            seen.add(key)
            unique.append(hashes)
    return unique


def _primary_database_candidates(system: str) -> list[str]:
    db_name = SYSTEM_DATABASE_NAMES.get(system)
    if not db_name:
        raise RuntimeError(f"Libretro database mapping is not configured for {system}.")
    if system == "GameCube":
        return [f"dat/{db_name}.dat"]
    candidates: list[str] = []
    if system in NOINTRO_SYSTEMS:
        candidates.append(f"metadat/no-intro/{db_name}.dat")
    if system in REDUMP_SYSTEMS:
        candidates.extend([f"metadat/redump/{db_name}.dat", f"metadat/tosec/{db_name}.dat"])
    # libretro-dats supplements the upstream sets and is particularly useful
    # for headered NES files, translations, revisions, and other variants.
    candidates.append(f"metadat/libretro-dats/{db_name}.dat")
    # Last-resort source folders are cheap to probe because 404s are optional.
    if system not in NOINTRO_SYSTEMS:
        candidates.append(f"metadat/no-intro/{db_name}.dat")
    if system not in REDUMP_SYSTEMS:
        candidates.extend([f"metadat/redump/{db_name}.dat", f"metadat/tosec/{db_name}.dat"])
    result = []
    seen = set()
    for value in candidates:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _lookup_index_hash_variants(index: dict[str, dict[str, str]], hashes_list: list[RomHashes], product_code: str = "") -> tuple[dict[str, str] | None, RomHashes | None]:
    for hashes in hashes_list:
        entry = _lookup_index(index, hashes, product_code)
        if entry:
            return entry, hashes
    return None, None


def _cache_path() -> Path:
    return config_dir() / "metadata_cache.json"


def _database_cache_dir() -> Path:
    p = config_dir() / "libretro_database"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_cache() -> dict[str, dict[str, Any]]:
    p = _cache_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def cached_metadata(sha1: str, max_age_days: int = 14) -> GameMetadata | None:
    raw = load_cache().get(sha1.upper())
    if not isinstance(raw, dict):
        return None
    if int(raw.get("_schema", 0) or 0) < METADATA_CACHE_SCHEMA:
        return None
    cached_at = float(raw.get("_cached_at", 0) or 0)
    if cached_at and (time.time() - cached_at) > max(1, max_age_days) * 86400.0:
        return None
    try:
        return GameMetadata(**{k: raw.get(k, "") for k in GameMetadata.__dataclass_fields__.keys()})
    except Exception:
        return None


def cache_metadata(metadata: GameMetadata) -> None:
    if not metadata.sha1:
        return
    cache = load_cache()
    payload = asdict(metadata)
    payload["_schema"] = METADATA_CACHE_SCHEMA
    payload["_cached_at"] = time.time()
    cache[metadata.sha1.upper()] = payload
    save_cache(cache)


def _safe_cache_name(relative_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", relative_path).strip("_")


def _raw_url(relative_path: str) -> str:
    quoted = "/".join(urllib.parse.quote(part) for part in relative_path.split("/"))
    return f"{RAW_ROOT}/{quoted}"


def _download_dat(relative_path: str, settings: LibretroSettings, force: bool = False, optional: bool = False) -> Path | None:
    cache_file = _database_cache_dir() / _safe_cache_name(relative_path)
    if cache_file.exists() and not force:
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400.0
        if age_days <= max(1, settings.cache_days):
            return cache_file

    req = urllib.request.Request(_raw_url(relative_path), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and optional:
            return cache_file if cache_file.exists() else None
        raise RuntimeError(f"Libretro database download failed ({exc.code}) for {relative_path}.") from exc
    except Exception as exc:
        if cache_file.exists():
            return cache_file
        if optional:
            return None
        raise RuntimeError(f"Libretro database download failed for {relative_path}: {exc}") from exc

    if not data:
        if optional:
            return cache_file if cache_file.exists() else None
        raise RuntimeError(f"Libretro database returned an empty file for {relative_path}.")
    cache_file.write_bytes(data)
    return cache_file


def _database_paths(system: str) -> tuple[str, list[str]]:
    db_name = SYSTEM_DATABASE_NAMES.get(system)
    if not db_name:
        raise RuntimeError(f"Libretro database mapping is not configured for {system}.")

    if system == "GameCube":
        # Libretro's customized GameCube DAT already contains GameTDB-derived title,
        # developer, publisher, release date and users fields, keyed by disc serial.
        primary = f"dat/{db_name}.dat"
        return primary, []

    primary = f"metadat/no-intro/{db_name}.dat"
    optional = [f"metadat/{folder}/{db_name}.dat" for folder in OPTIONAL_METADATA_FOLDERS]
    return primary, optional


def update_database_for_system(system: str, settings: LibretroSettings | None = None) -> str:
    settings = settings or LibretroSettings()
    if system in {"Wii", "WiiWare"}:
        gtdb = _download_gametdb(system, settings, force=True)
        if gtdb is None:
            raise RuntimeError(f"GameTDB metadata is unavailable for {system}.")
        return f"Updated {system} metadata (GameTDB XML)."
    primary_candidates = _primary_database_candidates(system)
    available = 0
    for rel in primary_candidates:
        path = _download_dat(rel, settings, force=True, optional=True)
        if path is not None:
            available += 1
    if available == 0:
        raise RuntimeError(f"No Libretro identification database is available for {system}.")
    _, optional_paths = _database_paths(system)
    for rel in optional_paths:
        path = _download_dat(rel, settings, force=True, optional=True)
        if path is not None:
            available += 1
    sources = [f"{available} Libretro file{'s' if available != 1 else ''}"]
    if system in GAMETDB_URLS:
        gtdb = _download_gametdb(system, settings, force=True)
        if gtdb is not None:
            sources.append("GameTDB XML")
    return f"Updated {system} metadata ({', '.join(sources)})."


def update_database_for_systems(systems: list[str], settings: LibretroSettings | None = None) -> str:
    settings = settings or LibretroSettings()
    messages = []
    seen = set()
    for system in systems:
        if system in seen:
            continue
        seen.add(system)
        try:
            messages.append(update_database_for_system(system, settings))
        except Exception as exc:
            messages.append(f"{system}: {exc}")
    return "\n".join(messages) if messages else "No supported systems were selected."


def _paren_delta_outside_quotes(line: str) -> int:
    depth = 0
    in_quote = False
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_quote:
            escaped = True
            continue
        if ch == '"':
            in_quote = not in_quote
            continue
        if not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
    return depth


def _extract_game_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if current is None:
            if re.match(r"^game\s*\(", stripped, flags=re.I):
                current = [line]
                depth = _paren_delta_outside_quotes(line)
                if depth <= 0:
                    blocks.append("\n".join(current))
                    current = None
            continue
        current.append(line)
        depth += _paren_delta_outside_quotes(line)
        if depth <= 0:
            blocks.append("\n".join(current))
            current = None
            depth = 0
    return blocks


def _field(block: str, key: str) -> str:
    # Accept quoted and unquoted scalar values used by libretro DATs.
    pattern = rf"(?mi)^\s*{re.escape(key)}\s+(?:\"((?:\\.|[^\"])*)\"|([^\s()]+))"
    match = re.search(pattern, block)
    if not match:
        return ""
    value = match.group(1) if match.group(1) is not None else match.group(2)
    return (value or "").replace('\\"', '"').strip()


def _hash_field(block: str, key: str) -> str:
    match = re.search(rf"(?i)\b{re.escape(key)}\s+([A-Fa-f0-9]+)", block)
    return match.group(1).upper() if match else ""


def _entry_from_block(block: str) -> dict[str, str]:
    return {
        "name": _field(block, "name"),
        "comment": _field(block, "comment"),
        "serial": _field(block, "serial").upper(),
        "developer": _field(block, "developer"),
        "publisher": _field(block, "publisher"),
        "releaseyear": _field(block, "releaseyear"),
        "releasemonth": _field(block, "releasemonth"),
        "releaseday": _field(block, "releaseday"),
        "users": _field(block, "users") or _field(block, "maxusers"),
        "region": _field(block, "region"),
        "crc": _hash_field(block, "crc"),
        "md5": _hash_field(block, "md5"),
        "sha1": _hash_field(block, "sha1"),
    }


def _metadata_title_key(value: str) -> str:
    base = _strip_database_tags(value or "")
    base = base.casefold().replace("_", " ")
    base = re.sub(r"^(the|a|an)\s+", "", base)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _metadata_entries_compatible(a: dict[str, str], b: dict[str, str]) -> bool:
    for field in ("releaseyear", "users", "developer", "publisher"):
        av = (a.get(field, "") or "").strip().casefold()
        bv = (b.get(field, "") or "").strip().casefold()
        if av and bv and av != bv:
            return False
    return True


_PARSED_INDEX_CACHE: dict[tuple[str, float], dict[str, dict[str, str]]] = {}


def _index_dat(path: Path) -> dict[str, dict[str, str]]:
    key = (str(path), path.stat().st_mtime)
    cached = _PARSED_INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    text = path.read_text(encoding="utf-8", errors="replace")
    index: dict[str, dict[str, str]] = {}
    for block in _extract_game_blocks(text):
        entry = _entry_from_block(block)
        for field in ("sha1", "md5", "crc", "serial"):
            value = entry.get(field, "").upper()
            if value and f"{field}:{value}" not in index:
                index[f"{field}:{value}"] = entry
        for raw_title in (entry.get("name", ""), entry.get("comment", "")):
            raw_title = (raw_title or "").strip()
            if not raw_title:
                continue
            index.setdefault(f"title:{raw_title.casefold()}", entry)
            base_value = _metadata_title_key(raw_title)
            if base_value:
                base_key = f"titlebase:{base_value}"
                existing = index.get(base_key)
                if existing is None:
                    index[base_key] = entry
                elif existing.get("_ambiguous") != "1" and not _metadata_entries_compatible(existing, entry):
                    index[base_key] = {"_ambiguous": "1"}
    _PARSED_INDEX_CACHE.clear()
    _PARSED_INDEX_CACHE[key] = index
    return index


def _lookup_index(index: dict[str, dict[str, str]], hashes: RomHashes, product_code: str = "") -> dict[str, str] | None:
    code = (product_code or "").strip().upper()
    candidates = []
    if code:
        candidates.append(f"serial:{code}")
    candidates.extend([
        f"sha1:{hashes.sha1.upper()}",
        f"md5:{hashes.md5.upper()}",
        f"crc:{hashes.crc32.upper()}",
    ])
    for candidate in candidates:
        entry = index.get(candidate)
        if entry:
            return entry
    return None


def _strip_database_tags(title: str) -> str:
    raw = (title or "").strip()
    # Remove common DAT suffixes while retaining meaningful title parentheses.
    tag_re = re.compile(
        r"^(?:USA|Europe|Japan|World|Australia|Canada|Korea|China|Germany|France|Spain|Italy|"
        r"En|Fr|De|Es|It|Ja|Ko|Zh|Rev\s*[^)]*|Beta|Proto|Sample|Demo|Virtual Console|"
        r"Wii Virtual Console|Switch Online|Unl|Pirate|Aftermarket)(?:,.*)?$",
        flags=re.I,
    )

    def repl(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        return "" if tag_re.match(content) else match.group(0)

    raw = re.sub(r"\(([^()]*)\)", repl, raw)
    raw = re.sub(r"\[([^\[\]]*)\]", repl, raw)
    raw = re.sub(r"\s+", " ", raw).strip(" -_.")
    return raw


def _release_date(entry: dict[str, str]) -> tuple[str, str]:
    year = entry.get("releaseyear", "").strip()
    month = entry.get("releasemonth", "").strip()
    day = entry.get("releaseday", "").strip()
    if not year:
        return "", ""
    date = year
    if month.isdigit():
        date += f"-{int(month):02d}"
        if day.isdigit():
            date += f"-{int(day):02d}"
    return date, year



def _gametdb_cache_dir() -> Path:
    p = config_dir() / "gametdb"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download_gametdb(system: str, settings: LibretroSettings, force: bool = False) -> Path | None:
    url = GAMETDB_URLS.get(system)
    if not url:
        return None
    stem = "dstdb" if system == "Nintendo DS" else "wiitdb"
    xml_file = _gametdb_cache_dir() / f"{stem}.xml"
    if xml_file.exists() and not force:
        age_days = (time.time() - xml_file.stat().st_mtime) / 86400.0
        if age_days <= max(1, settings.cache_days):
            return xml_file
    req = urllib.request.Request(url, headers={"User-Agent": "Wii-U-VC-Image-Builder/3.4"})
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as response:
            data = response.read()
        if not data:
            return xml_file if xml_file.exists() else None
        zip_path = _gametdb_cache_dir() / f"{stem}.zip"
        zip_path.write_bytes(data)
        with zipfile.ZipFile(zip_path, "r") as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                return xml_file if xml_file.exists() else None
            xml_file.write_bytes(zf.read(xml_names[0]))
        return xml_file
    except Exception:
        return xml_file if xml_file.exists() else None


def _gametdb_index(path: Path) -> dict[str, dict[str, str]]:
    key = (str(path), path.stat().st_mtime)
    cached = _GAMETDB_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    index: dict[str, dict[str, str]] = {}
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return index
    for game in root.findall(".//game"):
        game_id = (game.findtext("id") or "").strip().upper()
        title = ""
        for locale in game.findall("locale"):
            if (locale.get("lang") or "").upper() == "EN":
                title = (locale.findtext("title") or "").strip()
                break
        if not title:
            title = (game.get("name") or "").strip()
        date = game.find("date")
        year = (date.get("year") or "").strip() if date is not None else ""
        month = (date.get("month") or "").strip() if date is not None else ""
        day = (date.get("day") or "").strip() if date is not None else ""
        input_node = game.find("input")
        player_values: list[int] = []
        if input_node is not None:
            for attr, raw in input_node.attrib.items():
                raw = str(raw).strip()
                if "players" in attr.lower() and raw.isdigit():
                    player_values.append(int(raw))
        players = str(max(player_values)) if player_values else ""
        entry = {
            "id": game_id,
            "title": title,
            "releaseyear": year,
            "releasemonth": month,
            "releaseday": day,
            "players": players,
            "developer": (game.findtext("developer") or "").strip(),
            "publisher": (game.findtext("publisher") or "").strip(),
        }
        if game_id:
            index[f"id:{game_id}"] = entry
        rom = game.find("rom")
        if rom is not None:
            for attr in ("crc", "md5", "sha1"):
                value = (rom.get(attr) or "").strip().upper()
                if value:
                    index[f"{attr}:{value}"] = entry
    _GAMETDB_INDEX_CACHE.clear()
    _GAMETDB_INDEX_CACHE[key] = index
    return index


def _lookup_gametdb(system: str, product_code: str, hashes: RomHashes | None, settings: LibretroSettings) -> dict[str, str] | None:
    path = _download_gametdb(system, settings, force=False)
    if path is None:
        return None
    index = _gametdb_index(path)
    code = (product_code or "").strip().upper()
    candidates: list[str] = []
    if code:
        candidates.append(f"id:{code}")
    if hashes is not None:
        candidates.extend([f"sha1:{hashes.sha1}", f"md5:{hashes.md5}", f"crc:{hashes.crc32}"])
    for key in candidates:
        entry = index.get(key)
        if entry:
            return entry
    return None


def _apply_gametdb_fallback(metadata: GameMetadata, system: str, product_code: str, hashes: RomHashes, settings: LibretroSettings) -> GameMetadata:
    entry = _lookup_gametdb(system, product_code, hashes, settings)
    if not entry:
        return metadata
    supplemented = False
    if not metadata.title and entry.get("title"):
        metadata.title = entry["title"]
        supplemented = True
    if not metadata.release_year and entry.get("releaseyear"):
        metadata.release_year = entry["releaseyear"]
        metadata.release_date = metadata.release_year
        month = entry.get("releasemonth", "")
        day = entry.get("releaseday", "")
        if month.isdigit():
            metadata.release_date += f"-{int(month):02d}"
            if day.isdigit():
                metadata.release_date += f"-{int(day):02d}"
        supplemented = True
    if not metadata.players and entry.get("players"):
        metadata.players = entry["players"]
        supplemented = True
    if not metadata.developer and entry.get("developer"):
        metadata.developer = entry["developer"]
        supplemented = True
    if not metadata.publisher and entry.get("publisher"):
        metadata.publisher = entry["publisher"]
        supplemented = True
    if supplemented:
        metadata.source = "Libretro Database + GameTDB" if metadata.source.startswith("Libretro") else "GameTDB"
    return metadata


def _lookup_optional_title(index: dict[str, dict[str, str]], primary_entry: dict[str, str]) -> dict[str, str] | None:
    for raw_title in (primary_entry.get("name", ""), primary_entry.get("comment", "")):
        raw_title = (raw_title or "").strip()
        if not raw_title:
            continue
        exact = index.get(f"title:{raw_title.casefold()}")
        if exact and exact.get("_ambiguous") != "1":
            return exact
        base_value = _metadata_title_key(raw_title)
        if base_value:
            base = index.get(f"titlebase:{base_value}")
            if base and base.get("_ambiguous") != "1":
                return base
    return None



def _lookup_title_or_serial_without_hash(index: dict[str, dict[str, str]], product_code: str, title_hint: str) -> dict[str, str] | None:
    code = (product_code or "").strip().upper()
    if code:
        entry = index.get(f"serial:{code}")
        if entry:
            return entry
    hint = (title_hint or "").strip()
    if hint:
        variants = []
        stripped = _strip_database_tags(hint)
        variants.extend([hint, stripped])
        if ", The" in stripped:
            variants.append("The " + stripped.replace(", The", ""))
        if stripped.lower().startswith("the "):
            variants.append(stripped[4:] + ", The")
        seen = set()
        for raw in variants:
            raw = (raw or "").strip()
            if not raw or raw.casefold() in seen:
                continue
            seen.add(raw.casefold())
            exact = index.get(f"title:{raw.casefold()}")
            if exact and exact.get("_ambiguous") != "1":
                return exact
            key = _metadata_title_key(raw)
            if key:
                base = index.get(f"titlebase:{key}")
                if base and base.get("_ambiguous") != "1":
                    return base
    return None


def _metadata_from_index_entry(entry: dict[str, str], path: Path, product_code: str, source: str = "Libretro Database") -> GameMetadata:
    release_date, release_year = _release_date(entry)
    return GameMetadata(
        title=_strip_database_tags(entry.get("name") or entry.get("comment") or path.stem),
        release_date=release_date,
        release_year=release_year,
        players=entry.get("users", "").strip(),
        publisher=entry.get("publisher", "").strip(),
        developer=entry.get("developer", "").strip(),
        game_id=entry.get("serial", "").strip() or product_code,
        rom_id=entry.get("serial", "").strip() or product_code,
        source=source,
    )


def lookup_libretro(
    path: Path,
    system: str,
    region: str = "",
    product_code: str = "",
    settings: LibretroSettings | None = None,
    use_cache: bool = True,
    title_hint: str = "",
) -> GameMetadata:
    settings = settings or LibretroSettings()
    # Disc/channel systems have stable Nintendo IDs. Prefer those before hashing
    # multi-gigabyte images. This makes metadata lookup immediate for Wii/GCN.
    if system in {"Wii", "WiiWare"} and product_code:
        game_entry = _lookup_gametdb(system, product_code, None, settings)
        if game_entry:
            year = game_entry.get("releaseyear", "")
            metadata = GameMetadata(
                title=game_entry.get("title", "") or path.stem, release_year=year, release_date=year,
                players=game_entry.get("players", ""), publisher=game_entry.get("publisher", ""),
                developer=game_entry.get("developer", ""), game_id=game_entry.get("id", "") or product_code,
                rom_id=product_code, source="GameTDB", sha1="", md5="", crc32="",
            )
            return metadata
        # If GameTDB does not know this ID, continue through Libretro's other
        # source DATs instead of failing metadata collection outright.
    hashes = compute_hashes(path)
    hash_variants = [hashes] + compute_normalized_hash_variants(path, system)
    if use_cache:
        cached = cached_metadata(hashes.sha1, settings.cache_days)
        if cached and (cached.source.startswith("Libretro") or cached.source.startswith("GameTDB")):
            if cached.release_year and cached.players:
                return cached
            # Continue through the current metadata files instead of returning
            # an older partial cache record with a missing year or player count.

    entry = None
    matched_hashes = None
    matched_source = "Libretro Database"
    primary_index = None
    for primary_rel in _primary_database_candidates(system):
        primary_path = _download_dat(primary_rel, settings, force=False, optional=True)
        if primary_path is None:
            continue
        index = _index_dat(primary_path)
        candidate, candidate_hashes = _lookup_index_hash_variants(index, hash_variants, product_code)
        if not candidate:
            # A conservative normalized-title match catches good dumps whose
            # container/header CRC differs from the preservation DAT.
            candidate = _lookup_title_or_serial_without_hash(index, product_code, title_hint or path.stem)
        if candidate:
            entry = candidate
            matched_hashes = candidate_hashes or hashes
            primary_index = index
            if "/libretro-dats/" in primary_rel:
                matched_source = "Libretro Database (libretro-dats)"
            elif "/redump/" in primary_rel:
                matched_source = "Libretro Database (Redump)"
            elif "/tosec/" in primary_rel:
                matched_source = "Libretro Database (TOSEC)"
            break

    if not entry:
        game_entry = _lookup_gametdb(system, product_code, hashes, settings)
        if game_entry:
            year = game_entry.get("releaseyear", "")
            metadata = GameMetadata(
                title=game_entry.get("title", "") or path.stem,
                release_year=year,
                release_date=year,
                players=game_entry.get("players", ""),
                publisher=game_entry.get("publisher", ""),
                developer=game_entry.get("developer", ""),
                game_id=game_entry.get("id", "") or product_code,
                rom_id=product_code, source="GameTDB",
                crc32=hashes.crc32, md5=hashes.md5, sha1=hashes.sha1,
            )
            cache_metadata(metadata)
            return metadata
        # Final metadata-only title fallback: even when a ROM revision is absent
        # from the identification DAT, the optional year/users/developer/publisher
        # DATs can still provide useful metadata by an unambiguous title match.
        title_entry = {"name": title_hint or path.stem, "comment": title_hint or path.stem, "crc": ""}
        merged = dict(title_entry)
        found_optional = False
        _, optional_rels = _database_paths(system)
        for rel in optional_rels:
            optional_path = _download_dat(rel, settings, force=False, optional=True)
            if optional_path is None:
                continue
            optional_index = _index_dat(optional_path)
            meta_entry = _lookup_optional_title(optional_index, title_entry)
            if not meta_entry:
                continue
            found_optional = True
            for field in ("developer", "publisher", "releaseyear", "releasemonth", "releaseday", "users"):
                if meta_entry.get(field):
                    merged[field] = meta_entry[field]
        if found_optional:
            release_date, release_year = _release_date(merged)
            metadata = GameMetadata(
                title=_strip_database_tags(title_hint or path.stem),
                release_date=release_date, release_year=release_year,
                players=merged.get("users", "").strip(), publisher=merged.get("publisher", "").strip(),
                developer=merged.get("developer", "").strip(), source="Libretro Metadata (title match)",
                crc32=hashes.crc32, md5=hashes.md5, sha1=hashes.sha1,
            )
            cache_metadata(metadata)
            return metadata
        raise RuntimeError("The ROM was not found by hash, normalized hash, product code, or unambiguous title in the available metadata databases.")

    merged = dict(entry)
    matched_hashes = matched_hashes or hashes
    match_crc = entry.get("crc", "") or matched_hashes.crc32
    _, optional_rels = _database_paths(system)
    for rel in optional_rels:
        optional_path = _download_dat(rel, settings, force=False, optional=True)
        if optional_path is None:
            continue
        optional_index = _index_dat(optional_path)
        meta_entry = optional_index.get(f"crc:{match_crc.upper()}")
        if not meta_entry and product_code:
            meta_entry = optional_index.get(f"serial:{product_code.upper()}")
        if not meta_entry:
            meta_entry = _lookup_optional_title(optional_index, entry)
        if not meta_entry:
            continue
        for field in ("developer", "publisher", "releaseyear", "releasemonth", "releaseday", "users"):
            if meta_entry.get(field):
                merged[field] = meta_entry[field]

    title = _strip_database_tags(merged.get("name") or merged.get("comment") or path.stem)
    release_date, release_year = _release_date(merged)
    metadata = GameMetadata(
        title=title,
        release_date=release_date,
        release_year=release_year,
        players=merged.get("users", "").strip(),
        publisher=merged.get("publisher", "").strip(),
        developer=merged.get("developer", "").strip(),
        game_id=merged.get("serial", "").strip() or merged.get("crc", "").strip(),
        rom_id=merged.get("serial", "").strip(),
        source=matched_source,
        crc32=hashes.crc32,
        md5=hashes.md5,
        sha1=hashes.sha1,
    )
    if not metadata.release_year or not metadata.players or not metadata.developer or not metadata.publisher:
        metadata = _apply_gametdb_fallback(metadata, system, product_code, hashes, settings)
    cache_metadata(metadata)
    return metadata


def database_status(system: str, settings: LibretroSettings | None = None) -> str:
    settings = settings or LibretroSettings()
    primary_count = 0
    for rel in _primary_database_candidates(system):
        if _download_dat(rel, settings, force=False, optional=True) is not None:
            primary_count += 1
    _, optional = _database_paths(system)
    optional_count = 0
    for rel in optional:
        if _download_dat(rel, settings, force=False, optional=True) is not None:
            optional_count += 1
    name = SYSTEM_DATABASE_NAMES.get(system, system)
    return f"Libretro database ready for {name}. {primary_count} identification DAT(s) plus {optional_count} metadata DAT(s) available."
