from io import BytesIO
from pathlib import Path
import json
import os
import struct
import threading
import time
import urllib.error
import zipfile

import pytest

from vc_extractor import updater as u
from scripts.check_root import check


def manifest():
    return {"schema": 1, "app_id": u.APP_ID, "version": "5.07.0", "platform": "windows-x64",
            "url": u.RELEASES + "/download/v5.07.0/app.zip", "size": 100, "sha256": "a" * 64,
            "exe_sha256": "b" * 64, "notes": ["Test change"]}


class Response(BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def settle(manager):
    deadline = time.monotonic() + 5
    while manager._inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager._inflight


@pytest.mark.parametrize("url", ["http://github.com/a", "https://github.com.evil.test/a", "https://github.com/other/project/releases/a",
                                  u.RELEASES + "/../secret", "https://user@github.com/" + u.REPO + "/releases/a",
                                  "https://release-assets.githubusercontent.com/foo", u.RELEASES + "/a?x=1"])
def test_reject_untrusted_urls(url):
    with pytest.raises(ValueError):
        u.trusted_url(url)


@pytest.mark.parametrize("change", [{"app_id": "wrong"}, {"version": "../x"}, {"platform": "windows-arm64"},
                                     {"sha256": "0"}, {"size": u.MAX_DOWNLOAD + 1}, {"url": u.RELEASES + "/download/v9.0/a.zip"}])
def test_manifest_rejections(change):
    with pytest.raises(ValueError):
        u.validate_manifest({**manifest(), **change})


def test_single_flight_and_manual_cooldown(tmp_path):
    gate = threading.Event()
    calls = []
    def opener(*args):
        calls.append(args)
        gate.wait(2)
        return Response(json.dumps(manifest()).encode(), {"ETag": '"one"'})
    manager = u.UpdateManager("5.06.0", tmp_path, opener)
    assert manager.request_check("startup")
    assert not manager.request_check("manual")
    gate.set()
    settle(manager)
    assert manager.state()["status"] == "available"
    assert not manager.request_check("manual")
    assert len(calls) == 1


def test_etag_cached_304_and_no_automatic_download(tmp_path):
    data = manifest()
    calls = []
    def opener(url, headers=None):
        calls.append((url, headers))
        if len(calls) == 1:
            return Response(json.dumps(data).encode(), {"ETag": '"one"'})
        raise urllib.error.HTTPError(url, 304, "not modified", {}, None)
    manager = u.UpdateManager("5.06.0", tmp_path, opener)
    manager.request_check("startup"); settle(manager)
    manager._last -= 301
    manager.request_check("interval"); settle(manager)
    assert calls[1][1]["If-None-Match"] == '"one"'
    assert manager.state()["status"] == "available"
    assert manager._staged is None
    assert all(call[0] == u.FEED for call in calls)


def test_offline_is_never_current_and_retry_after_wins(tmp_path):
    def opener(url, headers=None):
        raise urllib.error.HTTPError(url, 429, "limited", {"Retry-After": "600"}, None)
    manager = u.UpdateManager("5.06.0", tmp_path, opener)
    manager.request_check("startup"); settle(manager)
    assert manager.state()["status"] == "error"
    assert manager.state()["retry_after"] >= 598
    manager._last -= 1000
    assert not manager.request_check("manual")


def test_failed_check_after_success_does_not_claim_current(tmp_path):
    manager = u.UpdateManager("5.07.0", tmp_path, lambda *a: Response(json.dumps(manifest()).encode()))
    manager.request_check("startup"); settle(manager)
    assert manager.state()["status"] == "current"
    def offline(*args):
        raise OSError("offline")
    manager.opener = offline
    manager._last -= 301
    manager.request_check("interval"); settle(manager)
    assert manager.state()["status"] == "error"


def create_package(tmp_path, extra=None):
    pe = bytearray(512)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 60, 64)
    pe[64:68] = b"PE\0\0"
    struct.pack_into("<H", pe, 68, 0x8664)
    struct.pack_into("<H", pe, 86, 2)
    struct.pack_into("<H", pe, 88, 0x20B)
    exe = tmp_path / "sample.exe"
    exe.write_bytes(pe)
    data = manifest()
    data["exe_sha256"] = u.digest(exe)
    identity = {"app_id": u.APP_ID, "version": data["version"], "exe_sha256": data["exe_sha256"]}
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        prefix = "Wii_U_VC_Image_Studio/"
        bundle.writestr(prefix + u.EXE, pe)
        bundle.writestr(prefix + "release.json", json.dumps(identity))
        bundle.writestr(prefix + "README.txt", "Test")
        bundle.writestr(prefix + "THIRD_PARTY_NOTICES.txt", "Test")
        if extra:
            bundle.writestr(extra, "unsafe")
    data.update(size=archive.stat().st_size, sha256=u.digest(archive))
    return archive, data


def test_valid_archive_and_tampered_hash_negative_control(tmp_path, monkeypatch):
    archive, data = create_package(tmp_path)
    # Minimal PE fixtures cannot have real Windows version resources.
    original = u.validate_pe
    monkeypatch.setattr(u, "validate_pe", lambda p, v: original(p))
    assert u.validate_archive(archive, data, tmp_path / "safe").name == u.EXE
    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="SHA-256"):
        u.validate_archive(archive, data, tmp_path / "bad")


@pytest.mark.parametrize("extra", ["../escaped.exe", "/absolute", "Wii_U_VC_Image_Studio/second.exe", "Wii_U_VC_Image_Studio/README.txt"])
def test_archive_traversal_duplicate_and_extra_executable_rejected(tmp_path, extra):
    archive, data = create_package(tmp_path, extra)
    with pytest.raises(ValueError):
        u.validate_archive(archive, data, tmp_path / "bad")
    assert not (tmp_path / "escaped.exe").exists()


def test_download_then_explicit_install_only(tmp_path, monkeypatch):
    archive, data = create_package(tmp_path)
    original = u.validate_pe
    monkeypatch.setattr(u, "validate_pe", lambda p, v: original(p))
    def opener(url, headers=None):
        return Response(json.dumps(data).encode() if url == u.FEED else archive.read_bytes())
    manager = u.UpdateManager("5.06.0", tmp_path / "updates", opener)
    manager.request_check("startup"); settle(manager)
    assert manager._staged is None
    assert manager.request_download()
    settle(manager)
    assert manager.state()["status"] == "downloaded"
    assert not (manager.root / "rollback").exists()


def test_root_guard_without_git_and_clutter_negative_control(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/root_allowlist.json").write_text('["README.md"]')
    (tmp_path / "README.md").write_text("clean")
    assert check(tmp_path)
    (tmp_path / "unapproved-report.txt").write_text("clutter")
    with pytest.raises(ValueError, match="unapproved-report"):
        check(tmp_path)


def test_cleanup_cannot_remove_unowned_or_other_directories(tmp_path):
    with pytest.raises(ValueError):
        u.cleanup_session(tmp_path, tmp_path)
    session = tmp_path / "stage-owned"
    session.mkdir()
    u.atomic_json(session / "owner.json", {"app_id": u.APP_ID})
    (session / "download.zip").write_text("owned")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("user data")
    u.cleanup_session(session, tmp_path)
    assert unrelated.read_text() == "user data"


def test_existing_settings_location_is_preserved():
    from vc_extractor.config import default_config_dir
    assert "wii_u_vc_image_builder" in str(default_config_dir()).lower()


def test_cleanup_retries_partial_deletion_after_windows_lock(tmp_path, monkeypatch):
    session = tmp_path / "stage-owned"
    session.mkdir()
    u.atomic_json(session / "owner.json", {"app_id": u.APP_ID})
    (session / "helper.exe").write_bytes(b"fixture")
    original = u.shutil.rmtree
    attempts = []
    def temporarily_locked(path):
        attempts.append(path)
        if len(attempts) == 1:
            (path / "owner.json").unlink()
            raise PermissionError("Windows helper handle still closing")
        return original(path)
    monkeypatch.setattr(u.shutil, "rmtree", temporarily_locked)
    u.cleanup_session(session, tmp_path)
    assert len(attempts) == 2 and not session.exists()
