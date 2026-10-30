"""Production timers in Chromium without Playwright's forced-focus emulation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vc_extractor.webui import WebUIApi


class Chrome:
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="studio-timer-")
        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path
        args = [executable, "--no-sandbox", "--no-first-run", "--remote-debugging-port=0",
                "--user-data-dir=" + self.directory.name, "about:blank"]
        if not os.environ.get("DISPLAY"):
            args.append("--headless=new")
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.directory.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while not port_file.exists() and self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        if not port_file.exists():
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.directory.cleanup()
            raise RuntimeError("Chromium could not start its local test connection; run this check in hosted CI")
        port, path = port_file.read_text().splitlines()[:2]
        self.socket = connect("ws://127.0.0.1:" + port + path, max_size=16 * 1024 * 1024)
        self.sequence = 0
        return self

    def call(self, method, params=None, session=None):
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.socket.send(json.dumps(message))
        while True:
            response = json.loads(self.socket.recv(timeout=20))
            if response.get("id") == self.sequence:
                assert "error" not in response, response
                return response.get("result", {})

    def evaluate(self, session, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session)
        assert "exceptionDetails" not in result, result
        return result["result"].get("value")

    def __exit__(self, *args):
        self.socket.close()
        self.process.terminate()
        self.process.wait(timeout=15)
        self.directory.cleanup()


def main():
    probe = "--focus-probe" in sys.argv
    state = json.dumps(WebUIApi().get_state())
    with Chrome() as chrome:
        target = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = chrome.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        chrome.call("Page.enable", session=session)
        chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            window.checkEvents=[]; window.visibilityEvents=[];
            window.addEventListener('load',()=>window.dispatchEvent(new Event('pywebviewready')));
            document.addEventListener('visibilitychange',()=>visibilityEvents.push(document.visibilityState));
            window.pywebview={api:{bootstrap:async()=>({ok:true,state:STATE}),get_state:async()=>STATE,
              report_ui_ready:async()=>({ok:true,healthy:true}),check_updates:async reason=>{
                checkEvents.push({reason,time:Date.now()});return {ok:true,update:STATE.updates};}}};
        """.replace("STATE", "(" + state + ")")}, session)
        chrome.call("Page.navigate", {"url": (ROOT / "vc_extractor/webui_assets/index.html").as_uri()}, session)
        chrome.call("Target.activateTarget", {"targetId": target})
        deadline = time.monotonic() + 15
        while not chrome.evaluate(session, "Boolean(window.studioUpdates)"):
            assert time.monotonic() < deadline, "Studio UI did not start"
            time.sleep(.1)
        started = time.monotonic()
        assert chrome.evaluate(session, "checkEvents.some(e=>e.reason==='startup')")
        if not probe:
            while time.monotonic() - started < 125:
                time.sleep(1)
        other = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        chrome.call("Target.activateTarget", {"targetId": other})
        time.sleep(.5)
        assert chrome.evaluate(session, "document.visibilityState") == "hidden"
        chrome.call("Target.activateTarget", {"targetId": target})
        time.sleep(.5)
        assert chrome.evaluate(session, "document.visibilityState") == "visible"
        events = chrome.evaluate(session, "checkEvents")
        visibility = chrome.evaluate(session, "visibilityEvents")
        assert "hidden" in visibility and "visible" in visibility, visibility
        if not probe:
            assert any(e["reason"] == "focus" and e["time"] - events[0]["time"] >= 120000 for e in events), events
            while time.monotonic() - started < 305:
                time.sleep(1)
            events = chrome.evaluate(session, "checkEvents")
            assert any(e["reason"] == "interval" for e in events), events
        print(json.dumps({"events":events,"real_visibility_events":visibility,"production_intervals_tested":not probe}))


if __name__ == "__main__":
    main()
