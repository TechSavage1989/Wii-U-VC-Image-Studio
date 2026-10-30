"""Exercise the built EXE helper, native health, cleanup and failed-health rollback.

This is a local package transition, not a claim of a historical hosted upgrade.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psutil
from vc_extractor.updater import APP_ID, EXE, atomic_json, digest, validate_archive


def scenario(base, target_base, fail_health=False):
    workspace = base / ("rollback" if fail_health else "healthy")
    workspace.mkdir()
    local = workspace / "local"
    appdata = workspace / "roaming"
    appdata.mkdir()
    settings = appdata / "Wii_U_VC_Image_Builder/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"custom_preservation_test": "keep", "automatic_repository_artwork": false}')
    before = settings.read_bytes()
    update_root = local / "Wii_U_VC_Image_Studio/updates"
    session = update_root / ("stage-" + uuid.uuid4().hex)
    session.mkdir(parents=True)
    atomic_json(session / "owner.json", {"app_id": APP_ID})
    manifest = json.loads((ROOT / "artifacts/update-manifest.json").read_text())
    archive = next((ROOT / "artifacts").glob("*_Portable.zip"))
    payload = validate_archive(archive, manifest, session / "payload")
    installation = target_base / workspace.name
    installation.mkdir()
    target = installation / "My custom studio name.exe"
    shutil.copy2(payload, target)
    # Different old hash with an intact unsigned PE, mimicking an existing build.
    with target.open("ab") as handle:
        handle.write(b"old-build-test-marker")
    old_hash = digest(target)
    helper = session / "helper.exe"
    shutil.copy2(payload, helper)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    token = uuid.uuid4().hex
    atomic_json(session / "plan.json", {"app_id":APP_ID, "target":str(target.resolve()), "old_sha256":old_hash,
                "payload":str(payload.resolve()), "manifest":manifest, "parent_pid":parent.pid,
                "token":token, "session":str(session.resolve())})
    env = {**os.environ, "LOCALAPPDATA":str(local), "APPDATA":str(appdata), "PYINSTALLER_RESET_ENVIRONMENT":"1"}
    if fail_health:
        # Real startup failure: WebView storage cannot be created beneath a file.
        blocked = workspace / "not-a-directory"
        blocked.write_text("negative control")
        env["APPDATA"] = str(blocked)
    running = subprocess.Popen([str(helper), "--studio-apply-update", str(session / "plan.json")], env=env)
    observed = {}
    deadline = time.monotonic() + 160
    try:
        while time.monotonic() < deadline:
            try:
                for child in psutil.Process(running.pid).children(recursive=True):
                    observed[child.pid] = child
            except psutil.Error:
                pass
            result_file = update_root / "last-result.json"
            if result_file.exists():
                result = json.loads(result_file.read_text())
                if result.get("status") in {"success", "rolled-back"}:
                    break
            time.sleep(.25)
        else:
            raise AssertionError("Native update did not finish: " + str(list(update_root.rglob("*.json"))))
        running.wait(timeout=30)
        assert settings.read_bytes() == before
        backups = list((update_root / "rollback").rglob("*.exe"))
        assert len(backups) == 1 and digest(backups[0]) == old_hash
        assert not list(installation.glob(".studio-update-*"))
        if fail_health:
            assert result["status"] == "rolled-back", result
            assert digest(target) == old_hash
        else:
            assert result["status"] == "success", result
            assert digest(target) == manifest["exe_sha256"]
            for _ in range(120):
                if not session.exists():
                    break
                time.sleep(.25)
            assert not session.exists(), "Healthy app did not clean staging after helper exit"
        print(json.dumps({"scenario":"failed-health" if fail_health else "healthy", "result":result,
                          "custom_filename":True, "settings_preserved":True, "one_backup":True,
                          "cross_volume":target.drive.casefold() != session.drive.casefold()}))
    finally:
        for process in list(observed.values()):
            try:
                for child in process.children(recursive=True):
                    observed[child.pid] = child
            except psutil.Error:
                pass
        for process in reversed(list(observed.values())):
            try:
                process.kill()
            except psutil.Error:
                pass
        if running.poll() is None:
            subprocess.run(["taskkill", "/PID", str(running.pid), "/T", "/F"], capture_output=True)
        psutil.wait_procs(list(observed.values()), timeout=10)
        parent.wait(timeout=5)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="studio-native-update-") as directory, \
         tempfile.TemporaryDirectory(prefix="studio-install-", dir=ROOT / "artifacts") as targets:
        scenario(Path(directory), Path(targets))
        scenario(Path(directory), Path(targets), True)
