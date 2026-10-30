from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import struct


@dataclass
class RomInfo:
    path: Path
    system: str
    display_name: str
    product_code: str = ""
    region: str = ""
    title: str = ""
    backend: str = "RetroArch"
    notes: str = ""
    release_year: str = ""
    players: str = ""
    publisher: str = ""
    developer: str = ""
    metadata_source: str = ""
    metadata_game_id: str = ""
    metadata_rom_id: str = ""
    metadata_sha1: str = ""
    shot_x: int = 0
    shot_y: int = 0
    shot_width: int = 0
    shot_height: int = 0
    icon_template: str = ""
    icon_focus_x: int = 50
    icon_focus_y: int = 50
    icon_zoom: int = 100
    icon_fill_mode: str = "Black"
    icon_fill_color: str = "#000000"
    tv_focus_x: int = 50
    tv_focus_y: int = 50
    tv_zoom: int = 100
    tv_fill_mode: str = "Black"
    tv_fill_color: str = "#000000"
    gamepad_focus_x: int = 50
    gamepad_focus_y: int = 50
    gamepad_zoom: int = 100
    gamepad_fill_mode: str = "Black"
    gamepad_fill_color: str = "#000000"
    source_area_selection: str = "auto"


SYSTEM_DIRS = {
    "Nintendo DS": "nds",
    "Game Boy Advance": "gba",
    "Game Boy Color": "gbc",
    "Game Boy": "gb",
    "NES": "nes",
    "SNES": "snes",
    "Nintendo 64": "n64",
    "GameCube": "gcn",
    "Wii": "wii",
    "WiiWare": "wiiware",
    "MSX": "msx",
    "TurboGrafx-CD / PC Engine CD": "tgcd",
    "Genesis / Mega Drive": "genesis",
    "Master System": "sms",
    "Game Gear": "gamegear",
    "TurboGrafx-16 / PC Engine": "tg16",
}


ROM_EXTENSIONS = {
    ".nds", ".gba", ".gb", ".gbc", ".nes", ".sfc", ".smc", ".n64", ".z64", ".v64",
    ".gcm", ".iso", ".rvz", ".ciso", ".wbfs", ".wad",
    ".rom", ".mx1", ".mx2", ".cue",
    ".md", ".gen", ".smd", ".sms", ".gg", ".pce",
}


REGION_MAP_NINTENDO = {
    "E": "USA",
    "P": "Europe",
    "J": "Japan",
    "D": "Germany",
    "F": "France",
    "I": "Italy",
    "S": "Spain",
    "K": "Korea",
    "A": "World",
}


def _clean_ascii(data: bytes) -> str:
    return data.decode("ascii", errors="ignore").replace("\x00", " ").strip()


def _safe_code(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "", value or "")
    return value.upper()


def _read(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as f:
        f.seek(offset)
        return f.read(size)


def _detect_nds(path: Path) -> RomInfo:
    header = _read(path, 0, 0x200)
    title = _clean_ascii(header[0x00:0x0C])
    code = _clean_ascii(header[0x0C:0x10])
    region = REGION_MAP_NINTENDO.get(code[-1:].upper(), "") if len(code) == 4 else ""
    return RomInfo(path, "Nintendo DS", path.stem, _safe_code(code), region, title or path.stem, "RetroArch")


def _detect_gba(path: Path) -> RomInfo:
    header = _read(path, 0, 0xC0)
    title = _clean_ascii(header[0xA0:0xAC])
    code = _clean_ascii(header[0xAC:0xB0])
    region = REGION_MAP_NINTENDO.get(code[-1:].upper(), "") if len(code) == 4 else ""
    return RomInfo(path, "Game Boy Advance", path.stem, _safe_code(code), region, title or path.stem, "RetroArch")


def _detect_gb(path: Path) -> RomInfo:
    header = _read(path, 0, 0x150)
    if len(header) < 0x150:
        system = "Game Boy Color" if path.suffix.lower() == ".gbc" else "Game Boy"
        return RomInfo(path, system, path.stem, "", "", path.stem, "RetroArch", "Short or unusual cartridge header")
    cgb_flag = header[0x143]
    system = "Game Boy Color" if path.suffix.lower() == ".gbc" or cgb_flag in (0x80, 0xC0) else "Game Boy"
    # CGB-compatible titles use bytes 0x134-0x13E, older titles can use 0x134-0x143.
    raw_title = header[0x134:0x143]
    title = _clean_ascii(raw_title).strip()
    return RomInfo(path, system, path.stem, "", "", title or path.stem, "RetroArch")


def _detect_nes(path: Path) -> RomInfo:
    magic = _read(path, 0, 4)
    notes = "iNES/NES 2.0 header detected" if magic == b"NES\x1a" else "Header not recognized; classified from extension"
    return RomInfo(path, "NES", path.stem, "", "", path.stem, "RetroArch", notes)


def _snes_title_candidate(path: Path, base: int) -> tuple[str, int]:
    try:
        data = _read(path, base, 0x40)
    except OSError:
        return "", -999
    if len(data) < 0x20:
        return "", -999
    title_bytes = data[:21]
    title = _clean_ascii(title_bytes)
    score = sum(2 if 32 <= b <= 126 else -3 for b in title_bytes)
    # Map mode at +0x15 is usually one of a small set of values.
    map_mode = data[0x15] if len(data) > 0x15 else 0
    if map_mode in {0x20, 0x21, 0x22, 0x23, 0x25, 0x30, 0x31, 0x32, 0x35}:
        score += 8
    return title, score


def _detect_snes(path: Path) -> RomInfo:
    size = path.stat().st_size
    header_adjust = 512 if size % 0x8000 == 512 else 0
    candidates = []
    for base in (0x7FC0 + header_adjust, 0xFFC0 + header_adjust, 0x40FFC0 + header_adjust):
        title, score = _snes_title_candidate(path, base)
        candidates.append((score, title))
    candidates.sort(reverse=True)
    title = candidates[0][1] if candidates and candidates[0][0] > 0 else path.stem
    return RomInfo(path, "SNES", path.stem, "", "", title or path.stem, "RetroArch")


def _detect_n64(path: Path) -> RomInfo:
    data = _read(path, 0, 0x40)
    title = path.stem
    if len(data) >= 0x34:
        # The internal name is at 0x20-0x33 after byte-order normalization. We leave it alone if the dump is swapped.
        magic = data[:4]
        if magic == bytes.fromhex("80371240"):
            title = _clean_ascii(data[0x20:0x34]) or path.stem
    return RomInfo(path, "Nintendo 64", path.stem, "", "", title, "RetroArch")


WII_DISC_MAGIC = 0x5D1C9EA3
GAMECUBE_DISC_MAGIC = 0xC2339F3D


def _disc_header(path: Path, offset: int = 0) -> bytes:
    return _read(path, offset, 0x440)


def _disc_system_from_header(data: bytes) -> str:
    if len(data) >= 0x20:
        try:
            if struct.unpack(">I", data[0x18:0x1C])[0] == WII_DISC_MAGIC:
                return "Wii"
            if struct.unpack(">I", data[0x1C:0x20])[0] == GAMECUBE_DISC_MAGIC:
                return "GameCube"
        except struct.error:
            pass
    return ""


def _disc_identity(data: bytes, fallback_title: str) -> tuple[str, str, str]:
    code = _clean_ascii(data[:6]) if len(data) >= 6 else ""
    region = REGION_MAP_NINTENDO.get(code[3:4].upper(), "") if len(code) >= 4 else ""
    title = fallback_title
    if len(data) > 0x20:
        internal_bytes = data[0x20:0x420].split(b"\x00", 1)[0]
        internal = _clean_ascii(internal_bytes).strip()
        if internal:
            title = internal
    return _safe_code(code), region, title


def _detect_gamecube(path: Path, data: bytes | None = None) -> RomInfo:
    data = data if data is not None else (_disc_header(path) if path.suffix.lower() in {".iso", ".gcm"} else b"")
    code, region, title = _disc_identity(data, path.stem) if data else ("", "", path.stem)
    notes = "Compressed GameCube image; product code may need manual entry" if path.suffix.lower() in {".rvz", ".ciso"} and not code else ""
    return RomInfo(path, "GameCube", path.stem, code, region, title or path.stem, "RetroArch", notes)


def _detect_wii(path: Path, data: bytes | None = None) -> RomInfo:
    data = data if data is not None else _disc_header(path)
    code, region, title = _disc_identity(data, path.stem)
    return RomInfo(path, "Wii", path.stem, code, region, title or path.stem, "RetroArch")


def _wbfs_disc_header(path: Path) -> bytes:
    header = _read(path, 0, 0x10000)
    if len(header) < 13 or header[:4] != b"WBFS":
        return b""
    try:
        hd_sector_shift = header[8]
        wbfs_sector_shift = header[9]
        hd_sector_size = 1 << hd_sector_shift
        if len(header) < hd_sector_size:
            header = _read(path, 0, hd_sector_size)
        disc_table = header[12:hd_sector_size]
        slot = next((value for value in disc_table if value), 0)
        if not slot:
            return b""
        disc_offset = slot << wbfs_sector_shift
        return _read(path, disc_offset, 0x440)
    except Exception:
        return b""


def _detect_wbfs(path: Path) -> RomInfo:
    data = _wbfs_disc_header(path)
    if data:
        system = _disc_system_from_header(data)
        if system == "GameCube":
            return _detect_gamecube(path, data)
        return _detect_wii(path, data)
    return RomInfo(path, "Wii", path.stem, "", "", path.stem, "RetroArch", "WBFS disc ID could not be read; metadata may require manual entry")


def _detect_wiiware(path: Path) -> RomInfo:
    return RomInfo(path, "WiiWare", path.stem, "", "", path.stem, "RetroArch", "WAD channel package; title metadata may require manual correction")



def detect_rom(path: str | Path) -> RomInfo | None:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in ROM_EXTENSIONS:
        return None
    try:
        if ext == ".nds":
            return _detect_nds(path)
        if ext == ".gba":
            return _detect_gba(path)
        if ext in {".gb", ".gbc"}:
            return _detect_gb(path)
        if ext == ".nes":
            return _detect_nes(path)
        if ext in {".sfc", ".smc"}:
            return _detect_snes(path)
        if ext in {".n64", ".z64", ".v64"}:
            return _detect_n64(path)
        if ext == ".gcm":
            return _detect_gamecube(path)
        if ext == ".iso":
            data = _disc_header(path)
            system = _disc_system_from_header(data)
            if system == "GameCube":
                return _detect_gamecube(path, data)
            if system == "Wii":
                return _detect_wii(path, data)
            return None
        if ext == ".wbfs":
            return _detect_wbfs(path)
        if ext in {".rvz", ".ciso"}:
            # Both Wii and GameCube can use these Dolphin formats. Without
            # decoding their container metadata, preserve the existing GCN
            # behavior and let the user correct the system if required later.
            return _detect_gamecube(path)
        if ext == ".wad":
            return _detect_wiiware(path)
        if ext in {".rom", ".mx1", ".mx2"}:
            return RomInfo(path, "MSX", path.stem, "", "", path.stem, "RetroArch")
        if ext == ".cue":
            return RomInfo(path, "TurboGrafx-CD / PC Engine CD", path.stem, "", "", path.stem, "RetroArch")
        if ext in {".md", ".gen", ".smd"}:
            return RomInfo(path, "Genesis / Mega Drive", path.stem, "", "", path.stem, "RetroArch")
        if ext == ".sms":
            return RomInfo(path, "Master System", path.stem, "", "", path.stem, "RetroArch")
        if ext == ".gg":
            return RomInfo(path, "Game Gear", path.stem, "", "", path.stem, "RetroArch")
        if ext == ".pce":
            return RomInfo(path, "TurboGrafx-16 / PC Engine", path.stem, "", "", path.stem, "RetroArch")
    except (OSError, ValueError, struct.error) as exc:
        return RomInfo(path, "Unknown", path.stem, "", "", path.stem, "RetroArch", f"Header read error: {exc}")
    return None


def system_folder(system: str) -> str:
    return SYSTEM_DIRS.get(system, re.sub(r"[^a-z0-9]+", "_", system.lower()).strip("_"))


def _safe_folder_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[<>:\"/\|?*]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "game"


def default_output_key(info: RomInfo) -> str:
    base_title = (info.display_name or info.path.stem or info.title or "game").strip()
    base_title = _safe_folder_name(base_title)
    if info.product_code:
        code = _safe_code(info.product_code)
        return _safe_folder_name(f"{base_title} [{code}]")
    return base_title
