"""Explicit, bounded updates from this application's public GitHub releases."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

APP_ID = "TechSavage1989.Wii-U-VC-Image-Studio"
REPO = "TechSavage1989/Wii-U-VC-Image-Studio"
EXE = "Wii_U_VC_Image_Studio.exe"
RELEASES = f"https://github.com/{REPO}/releases"
FEED = RELEASES + "/latest/download/update-manifest.json"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,4}\.\d{1,4}(?:\.\d{1,4})?", value):
        raise ValueError("Unsupported release version")
    return tuple(map(int, value.split("."))) + (0,) * (3 - len(value.split(".")))


def storage_root():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "Wii_U_VC_Image_Studio" / "updates"


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def trusted_url(url, *, redirected=False):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError("Update URL must use trusted HTTPS")
    if parsed.hostname == "github.com" and parsed.path.startswith(f"/{REPO}/releases/"):
        if "/../" not in parsed.path and "%" not in parsed.path and not parsed.query:
            return url
    if redirected and parsed.hostname == "release-assets.githubusercontent.com":
        return url
    raise ValueError("Update URL is outside this application's release allowlist")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        trusted_url(newurl, redirected=True)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def open_url(url, headers=None):
    trusted_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Wii-U-VC-Image-Studio-Updater", **(headers or {})})
    return urllib.request.build_opener(SafeRedirect()).open(request, timeout=15)


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("app_id") != APP_ID:
        raise ValueError("Manifest belongs to a different application")
    version = data.get("version")
    version_key(version)
    if data.get("platform") != "windows-x64":
        raise ValueError("Release is not Windows x64")
    expected_prefix = RELEASES + f"/download/v{version}/"
    if not trusted_url(data.get("url", "")).startswith(expected_prefix):
        raise ValueError("Asset URL does not match the release version")
    if not str(data["url"]).endswith(".zip"):
        raise ValueError("Expected a portable ZIP release")
    for key in ("sha256", "exe_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(data.get(key, ""))):
            raise ValueError("Release has no valid SHA-256 digest")
    if type(data.get("size")) is not int or not 0 < data["size"] <= MAX_DOWNLOAD:
        raise ValueError("Invalid release download size")
    notes = data.get("notes", [])
    if not isinstance(notes, list) or len(notes) > 30 or any(not isinstance(x, str) or len(x) > 1000 for x in notes):
        raise ValueError("Invalid release notes")
    return data


def validate_pe(path, version=None):
    """Check PE executable architecture; native Windows also checks product resources."""
    with Path(path).open("rb") as handle:
        head = handle.read(64)
        if len(head) != 64 or head[:2] != b"MZ":
            raise ValueError("Downloaded program is not a Windows executable")
        offset = struct.unpack_from("<I", head, 60)[0]
        if not 64 <= offset <= 1024 * 1024:
            raise ValueError("Invalid PE header offset")
        handle.seek(offset)
        pe = handle.read(26)
        if len(pe) != 26 or pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
            raise ValueError("Expected a Windows x64 PE executable")
        flags = struct.unpack_from("<H", pe, 22)[0]
        if not flags & 2 or flags & 0x2000 or struct.unpack_from("<H", pe, 24)[0] != 0x20B:
            raise ValueError("Invalid Windows executable characteristics")
    if os.name == "nt" and version:
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("version", use_last_error=True)
        api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        size = api.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            raise ValueError("Executable lacks version identity resources")
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
            raise ValueError("Cannot read executable identity")
        for name, expected in (("ProductName", "Wii U VC Image Studio"), ("ProductVersion", version), ("OriginalFilename", EXE)):
            pointer, length = ctypes.c_void_p(), wintypes.UINT()
            if not api.VerQueryValueW(buffer, "\\StringFileInfo\\040904B0\\" + name, ctypes.byref(pointer), ctypes.byref(length)):
                raise ValueError("Missing executable identity field")
            if ctypes.wstring_at(pointer.value) != expected:
                raise ValueError("Executable identity or version does not match the manifest")


def validate_archive(archive, manifest, destination):
    validate_manifest(manifest)
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size != manifest["size"] or digest(archive) != manifest["sha256"]:
        raise ValueError("Downloaded ZIP failed its size or SHA-256 check")
    root = "Wii_U_VC_Image_Studio/"
    allowed = {root + x for x in (EXE, "release.json", "README.txt", "THIRD_PARTY_NOTICES.txt")}
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)) or len(names) > 5:
            raise ValueError("Duplicate or unexpected archive entries")
        if set(names) - allowed - {root} or not allowed <= set(names):
            raise ValueError("Unexpected portable package contents")
        total = 0
        for item in members:
            name = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if name.is_absolute() or ".." in name.parts or "\\" in item.filename or mode & 0o170000 == 0o120000 or item.flag_bits & 1:
                raise ValueError("Unsafe archive member")
            total += item.file_size
            if total > MAX_EXPANDED or item.file_size > MAX_EXPANDED or item.file_size > max(item.compress_size, 1) * 250:
                raise ValueError("Archive expansion exceeds safety limits")
        if bundle.getinfo(root + "release.json").file_size > 16384:
            raise ValueError("Oversized package identity")
        identity = json.loads(bundle.read(root + "release.json"))
        if identity != {"app_id": APP_ID, "version": manifest["version"], "exe_sha256": manifest["exe_sha256"]}:
            raise ValueError("Package identity does not match the release")
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / EXE
        with bundle.open(root + EXE) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        if digest(target) != manifest["exe_sha256"]:
            raise ValueError("Program SHA-256 mismatch")
        validate_pe(target, manifest["version"])
        return target


class UpdateManager:
    def __init__(self, installed, root=None, opener=open_url, clock=time.time):
        self.installed = installed
        self.root = Path(root) if root else storage_root()
        self.opener, self.clock = opener, clock
        self._lock = threading.Lock()
        self._inflight = False
        self._last = 0.0
        self._blocked_until = 0.0
        self._failures = 0
        self._manifest = None
        self._staged = None
        self._state = {"status": "idle", "installed": installed, "message": "Not checked yet", "latest": "", "notes": [], "progress": 0}
        self._cache = {}
        try:
            cache = json.loads((self.root / "check.json").read_text())
            self._blocked_until = min(float(cache.get("blocked_until", 0)), self.clock() + 86400)
            validate_manifest(cache["manifest"])
            self._cache = cache
            # Cached information is not a successful check in this session.
            self._state.update(latest=cache["manifest"]["version"], notes=cache["manifest"].get("notes", []))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def state(self):
        with self._lock:
            return dict(self._state, retry_after=max(0, int(self._blocked_until - self.clock())))

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def request_check(self, reason="manual"):
        with self._lock:
            now = self.clock()
            # The window owns the five-minute interval. A focus check between
            # ticks must not suppress the next tick for another five minutes.
            cooldown = 120 if reason == "focus" else 30
            if self._inflight or now < self._blocked_until or (self._last and now - self._last < cooldown):
                return False
            if self._state["status"] in ("downloaded", "installing"):
                return False
            self._inflight, self._last = True, now
            self._state.update(status="checking", message="Checking for updates")
        threading.Thread(target=self._check, daemon=True, name="studio-update-check").start()
        return True

    def _check(self):
        try:
            headers = {"Accept": "application/json"}
            if self._cache.get("etag"):
                headers["If-None-Match"] = self._cache["etag"]
            try:
                with self.opener(FEED, headers) as response:
                    raw = response.read(128 * 1024 + 1)
                    if len(raw) > 128 * 1024:
                        raise ValueError("Update feed exceeds size limit")
                    data = validate_manifest(json.loads(raw))
                    self._cache = {"manifest": data, "etag": response.headers.get("ETag", "")}
            except urllib.error.HTTPError as exc:
                if exc.code != 304 or not self._cache.get("manifest"):
                    raise
                data = validate_manifest(self._cache["manifest"])
            self._manifest = data
            available = version_key(data["version"]) > version_key(self.installed)
            self._failures = 0
            self._cache["checked_at"] = self.clock()
            self._cache["blocked_until"] = 0
            atomic_json(self.root / "check.json", self._cache)
            self._set(status="available" if available else "current", latest=data["version"], notes=data.get("notes", []),
                      message="Update available" if available else "No newer stable release found")
        except Exception as exc:
            self._failures += 1
            delay = min(3600, 60 * 2 ** min(self._failures - 1, 6))
            if isinstance(exc, urllib.error.HTTPError):
                retry = exc.headers.get("Retry-After", "")
                try:
                    delay = max(delay, int(retry))
                except ValueError:
                    try:
                        delay = max(delay, int(parsedate_to_datetime(retry).timestamp() - self.clock()))
                    except (ValueError, TypeError):
                        pass
                try:
                    delay = max(delay, int(exc.headers.get("X-RateLimit-Reset", "0")) - int(self.clock()))
                except ValueError:
                    pass
            self._blocked_until = self.clock() + min(max(delay, 30), 86400)
            self._cache["blocked_until"] = self._blocked_until
            try:
                atomic_json(self.root / "check.json", self._cache)
            except OSError:
                pass
            self._set(status="error", message=f"Update check failed. Connection or release feed unavailable. {exc}")
        finally:
            with self._lock:
                self._inflight = False

    def request_download(self):
        with self._lock:
            if self._inflight or self._state["status"] != "available" or not self._manifest:
                return False
            self._inflight = True
            self._state.update(status="downloading", message="Downloading update", progress=0)
        threading.Thread(target=self._download, daemon=True, name="studio-update-download").start()
        return True

    def _download(self):
        session = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            session = Path(tempfile.mkdtemp(prefix="stage-", dir=self.root))
            atomic_json(session / "owner.json", {"app_id": APP_ID})
            archive = session / "download.zip"
            manifest = dict(self._manifest)
            count = 0
            with self.opener(manifest["url"]) as response, archive.open("xb") as handle:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > manifest["size"] or count > MAX_DOWNLOAD:
                        raise ValueError("Download exceeds expected size")
                    handle.write(chunk)
                    self._set(progress=int(count * 100 / manifest["size"]))
            target = validate_archive(archive, manifest, session / "payload")
            self._staged = (session, target, manifest)
            self._set(status="downloaded", message="Verified download ready to install", progress=100)
        except Exception as exc:
            self._set(status="error", message=f"Download failed: {exc}")
            if session:
                cleanup_session(session, self.root)
        finally:
            with self._lock:
                self._inflight = False

    def install(self, target=None):
        if os.name != "nt" or not getattr(sys, "frozen", False):
            raise ValueError("Install is available only in the packaged Windows application")
        if not self._staged or self.state()["status"] != "downloaded":
            raise ValueError("Download and verify an update first")
        session, payload, manifest = self._staged
        target = Path(target or sys.executable).resolve()
        if target.suffix.lower() != ".exe" or not target.is_file():
            raise ValueError("Cannot locate the running Windows application")
        if digest(payload) != manifest["exe_sha256"]:
            raise ValueError("Staged program changed after verification")
        validate_pe(payload, manifest["version"])
        helper = session / "update-helper.exe"
        shutil.copy2(sys.executable, helper)
        plan = {"app_id": APP_ID, "target": str(target), "old_sha256": digest(target),
                "payload": str(payload), "manifest": manifest, "parent_pid": os.getpid(),
                "token": uuid.uuid4().hex, "session": str(session)}
        atomic_json(session / "plan.json", plan)
        subprocess.Popen([str(helper), "--studio-apply-update", str(session / "plan.json")], cwd=str(session),
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
                         env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        self._set(status="installing", message="Closing for installation")


def cleanup_session(session, root=None):
    root = Path(root or storage_root()).resolve()
    session = Path(session)
    if session.is_symlink() or session.resolve().parent != root or not session.name.startswith("stage-"):
        raise ValueError("Refusing cleanup outside an owned update session")
    if json.loads((session / "owner.json").read_text()) != {"app_id": APP_ID}:
        raise ValueError("Update session ownership is missing")
    # Ownership is checked once before deletion. Windows may briefly retain an
    # EXE handle even after the helper exits; retry the same validated directory.
    deadline = time.monotonic() + 20
    while session.exists():
        if session.is_symlink():
            raise ValueError("Update session changed during cleanup")
        try:
            shutil.rmtree(session)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.25)


def wait_pid(pid, timeout):
    """Wait on an actual Windows process handle, not PID polling/reuse."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return True
        raise OSError("Cannot verify whether the update process has exited")
    try:
        return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def apply_update(plan_path):
    """Helper process: target-volume staging, bounded relaunch health, rollback."""
    plan_path = Path(plan_path).resolve()
    root = storage_root().resolve()
    session = plan_path.parent
    if session.parent != root or not session.name.startswith("stage-"):
        raise ValueError("Invalid installer session")
    plan = json.loads(plan_path.read_text())
    manifest = validate_manifest(plan["manifest"])
    if plan["app_id"] != APP_ID or Path(plan["session"]).resolve() != session:
        raise ValueError("Invalid installer identity")
    target = Path(plan["target"]).resolve()
    payload = Path(plan["payload"]).resolve()
    if payload != session / "payload" / EXE or target.suffix.lower() != ".exe":
        raise ValueError("Unexpected installer paths")
    if digest(payload) != manifest["exe_sha256"]:
        raise ValueError("Staged executable digest changed")
    validate_pe(payload, manifest["version"])
    if not wait_pid(int(plan["parent_pid"]), 90):
        raise ValueError("The previous application did not close; no files replaced")
    if digest(target) != plan["old_sha256"]:
        raise ValueError("The installed application changed; refusing replacement")
    installation = hashlib.sha256(str(target).casefold().encode()).hexdigest()[:24]
    rollback = root / "rollback" / installation / "previous.exe"
    rollback.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(rollback.parent / "installation.json", {"target": str(target), "previous_sha256": plan["old_sha256"]})
    fd, temporary_name = tempfile.mkstemp(prefix=".studio-update-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    replaced, child = False, None
    try:
        # Copy across volumes, then replace atomically on the target filesystem.
        shutil.copy2(payload, temporary)
        if digest(temporary) != manifest["exe_sha256"]:
            raise ValueError("Target-filesystem staging digest mismatch")
        shutil.copy2(target, rollback.with_suffix(".tmp"))
        os.replace(rollback.with_suffix(".tmp"), rollback)
        replacement_deadline = time.monotonic() + 20
        while True:
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if time.monotonic() >= replacement_deadline:
                    raise
                time.sleep(0.25)
        replaced = True
        atomic_json(session / "helper.json", {"pid": os.getpid(),
                    "bootloader_pid": os.getppid() if getattr(sys, "frozen", False) else None})
        child = subprocess.Popen([str(target), "--studio-update-health", str(session), plan["token"]], cwd=str(target.parent),
                                 env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        deadline = time.monotonic() + 90
        healthy = False
        while time.monotonic() < deadline and child.poll() is None:
            try:
                ack = json.loads((session / "healthy.json").read_text())
                # One-file bootloaders spawn the Python process below Popen's PID.
                healthy = (ack.get("token") == plan["token"] and ack.get("version") == manifest["version"]
                           and ack.get("executable") == str(target))
            except (OSError, ValueError):
                pass
            if healthy:
                break
            time.sleep(0.25)
        if not healthy:
            raise ValueError("New application did not confirm a visible, healthy window")
        atomic_json(root / "last-result.json", {"status": "success", "version": manifest["version"], "time": datetime.now(timezone.utc).isoformat()})
        atomic_json(session / "finished.json", {"status": "success"})
    except Exception as exc:
        if child and child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, timeout=15)
            child.wait(timeout=15)
        if replaced:
            shutil.copy2(rollback, temporary)
            os.replace(temporary, target)
            subprocess.Popen([str(target)], cwd=str(target.parent), env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        atomic_json(root / "last-result.json", {"status": "rolled-back" if replaced else "failed", "error": str(exc)})
        raise
    finally:
        temporary.unlink(missing_ok=True)


def acknowledge_health(session, token, version):
    session = Path(session).resolve()
    if session.parent != storage_root().resolve() or not session.name.startswith("stage-"):
        return False
    plan = json.loads((session / "plan.json").read_text())
    if plan["token"] != token or plan["manifest"]["version"] != version or Path(plan["target"]).resolve() != Path(sys.executable).resolve():
        return False
    atomic_json(session / "healthy.json", {"token": token, "version": version, "pid": os.getpid(), "executable": str(Path(sys.executable).resolve())})

    def after_helper():
        try:
            helper = json.loads((session / "helper.json").read_text())
            exited = wait_pid(helper["pid"], 120)
            if exited and helper.get("bootloader_pid"):
                exited = wait_pid(helper["bootloader_pid"], 30)
            if exited and (session / "finished.json").is_file():
                cleanup_session(session)
        except (OSError, ValueError, KeyError) as exc:
            try:
                atomic_json(storage_root() / "cleanup-error.json", {"error": str(exc), "session": str(session)})
            except OSError:
                pass  # Keep diagnostic evidence, never broaden cleanup.
    threading.Thread(target=after_helper, daemon=True, name="studio-update-cleanup").start()
    return True
