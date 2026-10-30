"""Windows source-shell smoke using real WebView2 and its JS bridge."""
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webview
from vc_extractor.webui import WebUIApi


def main():
    api = WebUIApi()
    window = webview.create_window("Studio native smoke", str(ROOT / "vc_extractor/webui_assets/index.html"), js_api=api, width=1040, height=720)
    api._window = window
    window.events.shown += lambda: setattr(api, "_window_shown", True)
    result = []
    finished = threading.Event()
    def probe():
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    state = window.evaluate_js("Boolean(window.studioUpdates && document.querySelector('#versionLabel').textContent)")
                    if state:
                        break
                except Exception:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Native WebView2 startup did not complete")
            assert api._window_shown
            assert window.evaluate_js("document.documentElement.scrollHeight <= innerHeight")
            assert window.evaluate_js("typeof window.pywebview.api.install_update === 'function'")
            assert api.report_ui_ready()["healthy"]
            result.append("Native Windows WebView2 shown, bridge ready, viewport fitted")
        except Exception as exc:
            result.append(exc)
        finally:
            window.destroy()
            finished.set()
    webview.start(probe, gui="edgechromium", private_mode=True)
    assert finished.is_set() and result and isinstance(result[0], str), result
    print(result[0])


if __name__ == "__main__":
    main()
