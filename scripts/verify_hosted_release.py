"""Verify the real stable feed/download after publication, without installing.

The installed-version value is a test fixture, not a historical app upgrade.
"""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vc_extractor.help_docs import APP_VERSION
from vc_extractor.updater import UpdateManager, digest


def wait(manager, seconds, allow_check_error=False):
    deadline = time.monotonic() + seconds
    while manager._inflight and time.monotonic() < deadline:
        time.sleep(.1)
    assert not manager._inflight, "Hosted update operation timed out"
    if not allow_check_error:
        assert manager.state()["status"] != "error", manager.state()


def main():
    with tempfile.TemporaryDirectory(prefix="studio-hosted-check-") as directory:
        manager = UpdateManager("0.0.0", Path(directory))
        for attempt in range(3):
            assert manager.request_check("manual")
            wait(manager, 60, allow_check_error=True)
            if manager.state()["status"] != "error":
                break
            delay = manager.state()["retry_after"] + 1
            assert attempt < 2 and delay <= 121, manager.state()
            print("Waiting for the newly published feed using the updater's retry delay", flush=True)
            time.sleep(delay)
        assert manager.state()["status"] == "available", manager.state()
        assert manager.state()["latest"] == APP_VERSION, manager.state()
        assert manager._staged is None, "Checking must never download automatically"
        assert manager.request_download()
        wait(manager, 180)
        assert manager.state()["status"] == "downloaded", manager.state()
        session, payload, manifest = manager._staged
        assert digest(payload) == manifest["exe_sha256"]
        print(json.dumps({"version":APP_VERSION, "url":manifest["url"],
                          "zip_sha256":manifest["sha256"], "exe_sha256":manifest["exe_sha256"],
                          "size":manifest["size"], "real_https_download_verified":True,
                          "installed_anything":False}))


if __name__ == "__main__":
    main()
