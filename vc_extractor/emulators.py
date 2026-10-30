from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import socket
import subprocess
import tempfile
import time
from typing import Callable

from PIL import Image, ImageGrab, ImageStat

from .roms import RomInfo
from .scoring import FrameSample


CORE_CANDIDATES = {
    "Nintendo DS": ["melondsds_libretro.dll", "melonds_libretro.dll", "desmume_libretro.dll"],
    "Game Boy Advance": ["mgba_libretro.dll", "vbam_libretro.dll", "vba_next_libretro.dll"],
    # Use the GBA-capable cores for the entire Game Boy family so GB and GBC
    # titles can be captured through the same emulators the user already has
    # installed for GBA.
    "Game Boy Color": ["mgba_libretro.dll", "vbam_libretro.dll", "vba_next_libretro.dll"],
    "Game Boy": ["mgba_libretro.dll", "vbam_libretro.dll", "vba_next_libretro.dll"],
    "NES": ["mesen_libretro.dll", "nestopia_libretro.dll", "fceumm_libretro.dll"],
    "SNES": ["snes9x_libretro.dll", "bsnes_libretro.dll", "mesen-s_libretro.dll"],
    "Nintendo 64": ["mupen64plus_next_libretro.dll", "parallel_n64_libretro.dll"],
    "GameCube": ["dolphin_libretro.dll"],
    "Wii": ["dolphin_libretro.dll"],
    "WiiWare": ["dolphin_libretro.dll"],
    "Genesis / Mega Drive": ["genesis_plus_gx_libretro.dll", "picodrive_libretro.dll"],
    "Master System": ["genesis_plus_gx_libretro.dll", "picodrive_libretro.dll"],
    "Game Gear": ["genesis_plus_gx_libretro.dll", "picodrive_libretro.dll"],
    "TurboGrafx-16 / PC Engine": ["mednafen_pce_fast_libretro.dll", "mednafen_pce_libretro.dll"],
    "TurboGrafx-CD / PC Engine CD": ["mednafen_pce_fast_libretro.dll", "mednafen_pce_libretro.dll"],
    "MSX": ["bluemsx_libretro.dll", "fmsx_libretro.dll"],
}


@dataclass
class EmulatorSettings:
    retroarch_path: Path | None = None
    dolphin_path: Path | None = None
    core_overrides: dict[str, str] | None = None
    warmup_seconds: float = 4.0
    duration_seconds: float = 45.0
    interval_seconds: float = 0.75
    mute_retroarch: bool = True


class CaptureCancelled(Exception):
    pass


class EmulatorError(RuntimeError):
    pass


def discover_core(retroarch_path: Path, system: str, override: str = "") -> Path | None:
    if override:
        p = Path(override)
        if p.exists():
            return p
    cores_dir = retroarch_path.parent / "cores"
    for name in CORE_CANDIDATES.get(system, []):
        candidate = cores_dir / name
        if candidate.exists():
            return candidate
    return None




def retroarch_system_directory(retroarch_path: Path) -> Path:
    """Resolve RetroArch's System/BIOS directory from retroarch.cfg when possible."""
    retroarch_path = Path(retroarch_path)
    cfg_path = retroarch_path.parent / "retroarch.cfg"
    if cfg_path.exists():
        try:
            for raw in cfg_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line.startswith("system_directory") or "=" not in line:
                    continue
                value = line.split("=", 1)[1].strip().strip('"').strip()
                if not value or value.lower() in {"default", "null"}:
                    break
                value = value.replace("/", os.sep)
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = retroarch_path.parent / candidate
                return candidate
        except Exception:
            pass
    return retroarch_path.parent / "system"


def dolphin_sys_directory(retroarch_path: Path) -> Path:
    return retroarch_system_directory(retroarch_path) / "dolphin-emu" / "Sys"


def dolphin_system_files_ready(retroarch_path: Path) -> bool:
    sys_dir = dolphin_sys_directory(retroarch_path)
    try:
        return sys_dir.is_dir() and any(sys_dir.iterdir())
    except OSError:
        return False


def _cfg_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _send_retroarch_command(command: str, host="127.0.0.1", port=55355) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.4)
    try:
        sock.sendto(command.strip().encode("utf-8"), (host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _wait_for_new_png(folder: Path, known: set[Path], timeout=2.0) -> Path | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = set(folder.glob("*.png"))
        new = [p for p in current - known if p.is_file()]
        if new:
            return max(new, key=lambda p: p.stat().st_mtime)
        time.sleep(0.05)
    return None


def capture_retroarch(
    info: RomInfo,
    settings: EmulatorSettings,
    session_dir: Path,
    log: Callable[[str], None] = print,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[FrameSample]:
    retroarch = Path(settings.retroarch_path or "")
    if not retroarch.exists():
        raise EmulatorError("RetroArch executable is not configured or does not exist.")
    override = (settings.core_overrides or {}).get(info.system, "")
    core = discover_core(retroarch, info.system, override)
    if not core:
        names = ", ".join(CORE_CANDIDATES.get(info.system, [])) or "a compatible core"
        raise EmulatorError(f"No RetroArch core found for {info.system}. Expected one of: {names}")

    shots = session_dir / "retroarch_shots"
    shots.mkdir(parents=True, exist_ok=True)
    nci_port = _free_udp_port()
    cfg = session_dir / "vc_extractor_retroarch.cfg"
    extra_video_cfg = []
    if info.system in {"GameCube", "Wii", "WiiWare"}:
        # Dolphin's libretro documentation recommends OpenGL as the compatibility
        # fallback when other graphics APIs produce rendering problems. Apply it
        # only to this temporary capture session.
        extra_video_cfg.append('video_driver = "gl"')

    cfg.write_text(
        "\n".join([
            'network_cmd_enable = "true"',
            f'network_cmd_port = "{nci_port}"',
            f'screenshot_directory = "{_cfg_quote(shots)}"',
            'video_fullscreen = "false"',
            'video_windowed_fullscreen = "false"',
            'savestate_auto_save = "false"',
            'savestate_auto_load = "false"',
            'pause_nonactive = "false"',
            # Screenshot commands normally display an OSD notification. Repeated
            # automated captures can therefore record the previous notification.
            # Disable screenshot and general OSD notifications for this temporary
            # capture session so generated frames contain only game video.
            'notification_show_screenshot = "false"',
            'menu_enable_widgets = "false"',
            'video_font_enable = "false"',
        ] + extra_video_cfg + (['audio_mute_enable = "true"'] if settings.mute_retroarch else [])),
        encoding="utf-8",
    )

    cmd = [str(retroarch), "-L", str(core), str(info.path), "--appendconfig", str(cfg)]
    log(f"Launching RetroArch core: {core.name}")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(cmd, cwd=str(retroarch.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
    samples: list[FrameSample] = []
    try:
        start = time.monotonic()
        while time.monotonic() - start < settings.warmup_seconds:
            if cancelled():
                # Stop means keep anything usable. During warm-up there may not
                # be a scheduled sample yet, so make one best-effort capture
                # before closing RetroArch.
                before = set(shots.glob("*.png"))
                _send_retroarch_command("SCREENSHOT", port=nci_port)
                new_path = _wait_for_new_png(shots, before, timeout=1.0)
                if new_path:
                    stable = session_dir / "frame_0001.png"
                    try:
                        with Image.open(new_path) as im:
                            im.convert("RGB").save(stable)
                        samples.append(FrameSample(stable, max(0.0, time.monotonic() - start)))
                    except Exception:
                        pass
                if samples:
                    log(f"Stop requested during warm-up. Keeping {len(samples)} captured frame(s).")
                    return samples
                raise CaptureCancelled()
            if proc.poll() is not None:
                raise EmulatorError(f"RetroArch exited early with code {proc.returncode}.")
            time.sleep(0.1)

        known = set(shots.glob("*.png"))
        capture_start = time.monotonic()
        next_capture = 0.0
        index = 0
        nci_failures = 0
        while True:
            if cancelled():
                log(f"Stop requested. Keeping {len(samples)} captured frame(s) for analysis.")
                break
            elapsed = time.monotonic() - capture_start
            if elapsed > settings.duration_seconds:
                log(f"Capture duration reached ({settings.duration_seconds:.1f}s). Finalizing {len(samples)} frame(s).")
                break
            if proc.poll() is not None:
                break
            if elapsed >= next_capture:
                index += 1
                before = set(shots.glob("*.png"))
                _send_retroarch_command("SCREENSHOT", port=nci_port)
                new_path = _wait_for_new_png(shots, before, timeout=min(1.5, max(0.5, settings.interval_seconds)))
                if new_path:
                    stable = session_dir / f"frame_{index:04d}.png"
                    try:
                        with Image.open(new_path) as im:
                            im.convert("RGB").save(stable)
                        samples.append(FrameSample(stable, elapsed))
                        known.add(new_path)
                    except Exception:
                        pass
                else:
                    nci_failures += 1
                    # Fallback: capture the visible RetroArch client area on Windows. This also handles cores
                    # whose screenshot command is unavailable or delayed.
                    fallback_saved = False
                    if os.name == "nt":
                        try:
                            hwnd = _find_window_for_pid(proc.pid)
                            if hwnd:
                                image = _capture_window(hwnd)
                                stable = session_dir / f"frame_{index:04d}.png"
                                image.save(stable)
                                samples.append(FrameSample(stable, elapsed))
                                fallback_saved = True
                        except Exception:
                            fallback_saved = False
                    if nci_failures == 2 and not fallback_saved:
                        log("RetroArch screenshot command has not produced images yet. Continuing to retry.")
                next_capture += settings.interval_seconds
            else:
                time.sleep(min(0.05, max(0.01, next_capture - elapsed)))
        if not samples:
            raise EmulatorError(
                "RetroArch launched, but no screenshots were produced. Ensure Network Commands are not blocked and the core renders normally."
            )
        return samples
    finally:
        _send_retroarch_command("QUIT", port=nci_port)
        try:
            proc.wait(timeout=2.5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


# Windows window capture helpers for Dolphin. These are loaded lazily so the package can be tested on non-Windows hosts.
def _find_window_for_pid(pid: int) -> int | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @EnumWindowsProc
    def enum_proc(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    if not found:
        return None
    # Prefer the largest visible client window.
    def area(hwnd):
        rect = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        return 0
    return max(found, key=area)


def _client_bbox(hwnd: int) -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    left, top = int(pt.x), int(pt.y)
    right = left + int(rect.right - rect.left)
    bottom = top + int(rect.bottom - rect.top)
    if right - left < 160 or bottom - top < 120:
        return None
    return left, top, right, bottom


def _bring_foreground(hwnd: int) -> None:
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _capture_window(hwnd: int) -> Image.Image:
    bbox = _client_bbox(hwnd)
    if not bbox:
        raise EmulatorError("Could not determine the Dolphin render window size.")
    _bring_foreground(hwnd)
    time.sleep(0.03)
    return ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")


def _samples_are_effectively_black(samples: list[FrameSample]) -> bool:
    if not samples:
        return True
    checked = 0
    black = 0
    step = max(1, len(samples) // 8)
    for sample in samples[::step][:8]:
        try:
            with Image.open(sample.path) as im:
                gray = im.convert("L")
                gray.thumbnail((96, 96), Image.Resampling.BILINEAR)
                stat = ImageStat.Stat(gray)
                mean = float(stat.mean[0]) if stat.mean else 0.0
                checked += 1
                if mean < 6.0:
                    black += 1
        except Exception:
            continue
    return checked > 0 and black == checked


def capture_dolphin(
    info: RomInfo,
    settings: EmulatorSettings,
    session_dir: Path,
    log: Callable[[str], None] = print,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[FrameSample]:
    if os.name != "nt":
        raise EmulatorError("Dolphin window capture is currently implemented for Windows only.")
    dolphin = Path(settings.dolphin_path or "")
    if not dolphin.exists():
        raise EmulatorError("Dolphin executable is not configured or does not exist.")
    cmd = [str(dolphin), "--exec=" + str(info.path), "--batch"]
    log("Launching Dolphin")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(cmd, cwd=str(dolphin.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
    samples: list[FrameSample] = []
    hwnd = None
    try:
        deadline = time.monotonic() + max(12.0, settings.warmup_seconds + 8.0)
        while time.monotonic() < deadline:
            if cancelled():
                raise CaptureCancelled()
            if proc.poll() is not None:
                raise EmulatorError(f"Dolphin exited early with code {proc.returncode}.")
            hwnd = _find_window_for_pid(proc.pid)
            if hwnd and _client_bbox(hwnd):
                break
            time.sleep(0.2)
        if not hwnd:
            raise EmulatorError("Could not find the Dolphin render window.")
        wait_remaining = settings.warmup_seconds
        while wait_remaining > 0:
            if cancelled():
                try:
                    image = _capture_window(hwnd)
                    p = session_dir / "frame_0001.png"
                    image.save(p)
                    samples.append(FrameSample(p, max(0.0, settings.warmup_seconds - wait_remaining)))
                except Exception:
                    pass
                if samples:
                    log(f"Stop requested during warm-up. Keeping {len(samples)} captured frame(s).")
                    return samples
                raise CaptureCancelled()
            sl = min(0.1, wait_remaining)
            time.sleep(sl)
            wait_remaining -= sl

        start = time.monotonic()
        next_capture = 0.0
        index = 0
        while True:
            if cancelled():
                log(f"Stop requested. Keeping {len(samples)} captured frame(s) for analysis.")
                break
            elapsed = time.monotonic() - start
            if elapsed > settings.duration_seconds:
                log(f"Capture duration reached ({settings.duration_seconds:.1f}s). Finalizing {len(samples)} frame(s).")
                break
            if proc.poll() is not None:
                break
            if elapsed >= next_capture:
                index += 1
                hwnd = _find_window_for_pid(proc.pid) or hwnd
                image = _capture_window(hwnd)
                p = session_dir / f"frame_{index:04d}.png"
                image.save(p)
                samples.append(FrameSample(p, elapsed))
                next_capture += settings.interval_seconds
            else:
                time.sleep(min(0.05, max(0.01, next_capture - elapsed)))
        if not samples:
            raise EmulatorError("No Dolphin frames were captured.")
        return samples
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def capture_game(
    info: RomInfo,
    settings: EmulatorSettings,
    session_dir: Path,
    log: Callable[[str], None] = print,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[FrameSample]:
    # For GameCube/Wii/WiiWare, try the RetroArch Dolphin core when it is ready,
    # but automatically fall back to standalone Dolphin on missing Sys files,
    # startup/capture errors, or an all-black render. Metadata is handled before
    # capture and is independent of this emulator selection.
    if info.system in {"GameCube", "Wii", "WiiWare"}:
        retroarch = Path(settings.retroarch_path or "")
        dolphin = Path(settings.dolphin_path or "")
        override = (settings.core_overrides or {}).get(info.system, "")
        core = discover_core(retroarch, info.system, override) if retroarch.exists() else None
        can_use_libretro = bool(core and dolphin_system_files_ready(retroarch))

        if can_use_libretro:
            try:
                samples = capture_retroarch(info, settings, session_dir, log, cancelled)
                if cancelled():
                    return samples
                if _samples_are_effectively_black(samples):
                    raise EmulatorError("RetroArch Dolphin core produced only black frames.")
                return samples
            except CaptureCancelled:
                raise
            except EmulatorError as exc:
                if dolphin.exists():
                    log(f"RetroArch Dolphin capture failed: {exc}")
                    log("Falling back automatically to external Dolphin.exe. Existing metadata will be retained.")
                    return capture_dolphin(info, settings, session_dir, log, cancelled)
                raise

        if dolphin.exists():
            if core and not dolphin_system_files_ready(retroarch):
                log("RetroArch Dolphin system files are incomplete. Falling back automatically to external Dolphin.exe.")
            elif not core:
                log("RetroArch Dolphin core was not found. Falling back automatically to external Dolphin.exe.")
            return capture_dolphin(info, settings, session_dir, log, cancelled)
        raise EmulatorError("No usable Dolphin emulator was found. Configure standalone Dolphin.exe or a working RetroArch Dolphin core.")

    if info.backend == "Dolphin":
        return capture_dolphin(info, settings, session_dir, log, cancelled)
    return capture_retroarch(info, settings, session_dir, log, cancelled)

