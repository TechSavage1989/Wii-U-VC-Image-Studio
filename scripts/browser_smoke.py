"""Real Chromium UI test. --timers waits for production 2/5-minute intervals."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from vc_extractor.webui import WebUIApi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timers", action="store_true")
    args = parser.parse_args()
    state = WebUIApi().get_state()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not bool(os.environ.get("DISPLAY")), args=["--disable-background-timer-throttling"])
        context = browser.new_context(viewport={"width": 1440, "height": 940})
        context.add_init_script("""
            window.checkEvents = [];
            window.pywebview = {api: {
                bootstrap: async () => ({ok:true, state: STATE}),
                get_state: async () => STATE,
                report_ui_ready: async () => ({ok:true, healthy:true}),
                check_updates: async reason => {
                    window.checkEvents.push({reason, time:Date.now()});
                    return {ok:true, update:STATE.updates};
                }
            }};
        """.replace("STATE", "(" + json.dumps(state) + ")"))
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((ROOT / "vc_extractor/webui_assets/index.html").as_uri())
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_timeout(1000)
        assert page.evaluate("checkEvents.some(x=>x.reason==='startup')")
        for width, height in [(1440,940), (1040,720), (1280,768)]:
            page.set_viewport_size({"width":width, "height":height})
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth")
            assert page.locator(".workspace-grid").bounding_box()["height"] > 120
        page.evaluate("studioUpdates.render({status:'available',installed:'5.06.0',latest:'5.07.0',message:'Update available',notes:['One','Two','Three']})")
        assert page.get_by_role("button", name="Download", exact=True).is_visible()
        assert page.locator("#updateBanner summary").inner_text() == "Read more"
        page.evaluate("studioUpdates.render({status:'current',installed:'5.06.0',latest:'5.06.0',message:'Current',notes:[]}); studioUpdates.manualVisible=true; studioUpdates.render({status:'current',installed:'5.06.0',latest:'5.06.0',message:'Current',notes:[]})")
        assert page.locator("#updateBanner strong").inner_text().count("5.06.0") == 1
        context.set_offline(True)
        page.get_by_role("button", name="Updates", exact=True).click()
        assert "Offline" in page.locator("#updateBanner").inner_text()
        context.set_offline(False)
        if args.timers:
            started = time.monotonic()
            while time.monotonic() - started < 125:
                page.wait_for_timeout(1000)
            other = context.new_page()
            other.goto("about:blank")
            other.bring_to_front()
            page.wait_for_timeout(500)
            page.bring_to_front()
            page.wait_for_timeout(500)
            events = page.evaluate("checkEvents")
            assert any(e["reason"] == "focus" and e["time"] - events[0]["time"] >= 120000 for e in events), events
            while time.monotonic() - started < 305:
                page.wait_for_timeout(1000)
            events = page.evaluate("checkEvents")
            assert any(e["reason"] == "interval" for e in events), events
            print(json.dumps({"production_timer_events":events}))
        assert not errors, errors
        browser.close()
    print("Browser layout, update banner and offline checks passed" + ("; real 2/5-minute timers passed" if args.timers else ""))


if __name__ == "__main__":
    main()
