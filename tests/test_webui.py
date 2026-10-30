from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
import threading
import wave

from PIL import Image, ImageDraw
import pytest

from vc_extractor.models import GameJob
from vc_extractor.repository_art import RepositoryArtwork
from vc_extractor.webui import ActivityWindowApi, PreviewPanelWindowApi, PreviewWindowApi, WebUIApi
import vc_extractor.webui as webui


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApi:
    config = tmp_path / "config"
    monkeypatch.setattr(webui, "load_settings", lambda: {})
    monkeypatch.setattr(webui, "save_settings", lambda values: None)
    monkeypatch.setattr(webui, "config_dir", lambda: config)
    monkeypatch.setattr(webui, "default_config_dir", lambda: config)
    controller = WebUIApi()
    controller._settings["output_root"] = str(tmp_path / "output")
    controller._settings["automatic_repository_artwork"] = False
    monkeypatch.setattr(controller, "_start_metadata", lambda *args, **kwargs: None)
    return controller


def make_gba_rom(path: Path, title: str = "WEBUI GAME", code: bytes = b"WUBE") -> Path:
    data = bytearray(0xC0)
    data[0xA0:0xAC] = title.encode("ascii")[:12].ljust(12, b" ")
    data[0xAC:0xB0] = code[:4].ljust(4, b"E")
    path.write_bytes(data)
    return path


def make_image(path: Path, color: tuple[int, int, int], label: str = "TITLE") -> Path:
    image = Image.new("RGB", (640, 480), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 70, 570, 410), outline="white", width=8)
    draw.text((250, 220), label, fill="white")
    image.save(path)
    return path


def add_one_game(controller: WebUIApi, tmp_path: Path) -> GameJob:
    rom = make_gba_rom(tmp_path / "webui_game.gba")
    result = controller.add_paths([rom])
    assert result["ok"] and result["added"] == 1
    return controller.jobs[0]


def test_webui_entrypoint_and_assets_are_packaged():
    root = Path(__file__).resolve().parents[1]
    assert "from vc_extractor.webui import run" in (root / "app.py").read_text(encoding="utf-8")
    assert "pywebview==6.2.1" in (root / "requirements.txt").read_text(encoding="utf-8")
    build = (root / "scripts" / "build_windows.py").read_text(encoding="utf-8")
    assert '"--collect-all", "webview"' in build
    assert '"vc_extractor/webui_assets") + ";vc_extractor/webui_assets"' in build
    assets = root / "vc_extractor" / "webui_assets"
    for name in (
        "index.html",
        "style.css",
        "app.js",
        "updates.js",
        "app-icon.png",
        "preview_window.html",
        "preview_window.js",
        "preview_panel_window.html",
        "preview_panel_window.js",
        "activity_window.html",
        "activity_window.js",
        "window.css",
    ):
        assert (assets / name).is_file(), name


def test_webui_markup_exposes_primary_workflows():
    root = Path(__file__).resolve().parents[1] / "vc_extractor" / "webui_assets"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    for text in (
        "Add ROMs",
        "Remove Checked",
        "Review Artwork Matches",
        "Adjust Crop & Zoom",
        "Automatic",
        "Full Image",
        "Play Boot Sound",
        "Stop and Use Captures",
        "Detach",
        "Detach Preview",
        "Reattach Preview",
    ):
        assert text in html
    assert 'callApi("open_preview_window"' in script
    assert 'case "detach-activity"' in script
    assert 'case "detach-preview"' in script
    assert 'classList.toggle("detached", Boolean(ui.state.activity_detached))' in script
    assert "pywebviewFullPath" in script


def test_bootstrap_returns_serializable_empty_state(api: WebUIApi):
    result = api.bootstrap()
    assert result["ok"]
    state = result["state"]
    assert state["version"] == "5.06.0"
    assert state["jobs"] == []
    assert state["busy"] is False
    assert state["preview_detached"] is False
    assert state["activity_detached"] is False
    assert "Getting Started" in state["help_topics"]


def test_update_install_refuses_active_operations_before_launching_helper(api, monkeypatch):
    called = []
    monkeypatch.setattr(api._updates, "install", lambda: called.append(True))
    api._busy_kind = "capture"
    result = api.install_update()
    assert not result["ok"]
    assert not called


def test_update_health_requires_native_shown_window(api):
    assert api.report_ui_ready()["healthy"] is False
    api._window_shown = True
    assert api.report_ui_ready()["healthy"] is True


def test_webview_bridges_do_not_expose_recursive_native_objects(api: WebUIApi):
    """Public API fields are recursively inspected by pywebview at startup."""
    class NativeWindow:
        pass

    native_window = NativeWindow()
    controller = WebUIApi(window=native_window)
    assert controller._window is native_window
    assert "window" not in vars(controller)

    preview_api = PreviewWindowApi(controller, "job-id", "icon")
    activity_api = ActivityWindowApi(controller)
    assert "parent" not in vars(preview_api)
    assert "parent" not in vars(activity_api)
    preview_panel_api = PreviewPanelWindowApi(controller)
    for bridge in (controller, preview_api, preview_panel_api, activity_api):
        recursive_public_fields = {
            name: type(value).__name__
            for name, value in vars(bridge).items()
            if not name.startswith("_") and hasattr(value, "__module__")
        }
        assert recursive_public_fields == {}


def test_child_preview_windows_fit_within_available_screen():
    assert WebUIApi._fit_child_window_size(
        (1280, 720),
        (1920, 1080),
        fallback=(1100, 760),
        minimum=(420, 300),
    ) == (1320, 850)
    assert WebUIApi._fit_child_window_size(
        (1280, 720),
        (1024, 768),
        fallback=(1100, 760),
        minimum=(420, 300),
    ) == (944, 648)
    assert WebUIApi._fit_child_window_size(
        (128, 128),
        (1920, 1080),
        fallback=(1100, 760),
        minimum=(420, 300),
    ) == (420, 300)


def test_enlarged_preview_reuses_one_fit_to_screen_window(
    api: WebUIApi,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import webview as webview_module

    class EventHook:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class FakeChild:
        def __init__(self):
            self.events = SimpleNamespace(closed=EventHook())
            self.titles = []
            self.restored = 0
            self.shown = 0

        def set_title(self, value):
            self.titles.append(value)

        def restore(self):
            self.restored += 1

        def show(self):
            self.shown += 1

    job = add_one_game(api, tmp_path)
    assert api.import_screenshot(job.job_id, str(make_image(tmp_path / "source.png", (30, 80, 120))))["ok"]
    child = FakeChild()
    created = []

    def create_window(*args, **kwargs):
        created.append((args, kwargs))
        return child

    monkeypatch.setattr(webview_module, "create_window", create_window)
    monkeypatch.setattr(webview_module, "screens", [SimpleNamespace(x=0, y=0, width=1024, height=768)])
    assert api.open_preview_window(job.job_id, "icon")["ok"]
    assert len(created) == 1
    assert created[0][1]["width"] == 944
    assert created[0][1]["height"] == 648
    assert created[0][1]["min_size"] == (720, 480)
    assert api.open_preview_window(job.job_id, "tv")["existing"] is True
    assert len(created) == 1
    assert api._large_preview_api.get_state()["label"] == "TV Image"
    assert child.restored == 1 and child.shown == 1


def test_add_select_check_and_remove_use_stable_job_ids(api: WebUIApi, tmp_path: Path):
    first = make_gba_rom(tmp_path / "first.gba", "FIRST", b"FIRE")
    second = make_gba_rom(tmp_path / "second.gba", "SECOND", b"SECE")
    result = api.add_paths([first, second, first])
    assert result["ok"] and result["added"] == 2
    first_job, second_job = api.jobs
    assert len(first_job.job_id) == 32
    assert first_job.job_id != second_job.job_id

    assert api.set_selection([second_job.job_id])["ok"]
    assert api.clear_checks()["ok"]
    assert api.toggle_selected_capture()["checked"] is True
    assert first_job.capture_checked is False
    assert second_job.capture_checked is True
    removed = api.remove_checked()
    assert removed["ok"] and removed["removed"] == 1
    assert api.jobs == [first_job]
    assert first.is_file() and second.is_file()


def test_imported_screenshot_builds_all_previews(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    screenshot = make_image(tmp_path / "screen.png", (25, 70, 145))
    result = api.import_screenshot(job.job_id, str(screenshot))
    assert result["ok"]
    assert job.output_dir is not None
    assert Image.open(job.output_dir / "Icon Image.png").size == (128, 128)
    assert Image.open(job.output_dir / "TV Image.png").size == (1280, 720)
    assert Image.open(job.output_dir / "Gamepad Image.png").size == (854, 480)

    state = api.get_state()
    serialized = state["jobs"][0]
    assert all(serialized["preview_available"].values())
    preview = api.get_preview(job.job_id, "tv", 500, 320)
    assert preview["ok"] and preview["source_width"] == 1280
    assert base64.b64decode(preview["data_url"].split(",", 1)[1]).startswith(b"\x89PNG")


def test_generic_source_area_and_framing_rebuild_immediately(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    screenshot = make_image(tmp_path / "stacked.png", (80, 35, 95), "STACKED")
    assert api.import_screenshot(job.job_id, str(screenshot))["ok"]
    revision = job.preview_revision
    values = dict(api._crop_state_for_info(job.info))
    values.update({"source_area_selection": "left", "icon_zoom": 135, "tv_x": 62, "gamepad_y": 38})
    result = api.update_framing(job.job_id, values, rebuild=True)
    assert result["ok"]
    assert job.info.source_area_selection == "left"
    assert job.info.icon_zoom == 135
    assert job.preview_revision > revision
    assert (job.output_dir / "TV Image.png").is_file()


def test_output_and_overlay_settings_rebuild_selected_preview(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    screenshot = make_image(tmp_path / "settings-source.png", (35, 75, 120), "SETTINGS")
    assert api.import_screenshot(job.job_id, str(screenshot))["ok"]
    revision = job.preview_revision
    result = api.update_settings({"include_banner_metadata": False, "create_gamepad_image": False})
    assert result["ok"]
    assert job.preview_revision > revision
    assert (job.output_dir / "TV Image.png").is_file()
    assert not (job.output_dir / "Gamepad Image.png").exists()


def test_artwork_matches_assign_icon_and_banners_independently(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    icon_source = make_image(tmp_path / "icon-source.png", (145, 35, 35), "ICON")
    banner_source = make_image(tmp_path / "banner-source.png", (25, 110, 85), "BANNER")
    icon_art = RepositoryArtwork(
        provider_name="Icon Provider",
        provider_url="https://example.invalid/icon",
        product_code="WUBE",
        system="Game Boy Advance",
        source_image_path=icon_source,
        source_image_kind="Title screen",
        matched_title="WebUI Game",
        match_method="Title and region",
        confidence=0.78,
    )
    banner_art = RepositoryArtwork(
        provider_name="Banner Provider",
        provider_url="https://example.invalid/banner",
        product_code="WUBE",
        system="Game Boy Advance",
        source_image_path=banner_source,
        source_image_kind="Gameplay screenshot",
        matched_title="WebUI Game",
        match_method="Title and region",
        confidence=0.81,
    )
    job.repository_artwork_candidates = [icon_art, banner_art]
    assert api.assign_artwork(job.job_id, 0, "icon")["ok"]
    assert api.assign_artwork(job.job_id, 1, "tv_gamepad")["ok"]
    assert job.repository_icon_artwork == icon_art
    assert job.repository_tv_gamepad_artwork == banner_art
    serialized = api.get_state()["jobs"][0]["artwork_matches"]
    assert serialized[0]["assigned_icon"] is True
    assert serialized[1]["assigned_tv_gamepad"] is True
    metadata = (job.output_dir / "metadata.txt").read_text(encoding="utf-8")
    assert "Icon Provider" in metadata and "Banner Provider" in metadata


def test_manual_boot_sound_is_persistent_playable_and_clearable(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    source = tmp_path / "startup.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes((b"\x00\x00\x40\x00\x00\x00\xc0\xff") * 1000)
    result = api.import_game_boot_sound(job.job_id, str(source))
    assert result["ok"]
    stored = api._game_boot_sound_path(job.info)
    assert stored.is_file()
    preview = api.get_boot_sound_audio(job.job_id)
    assert preview["ok"] and preview["data_url"].startswith("data:audio/wav;base64,")
    assert (job.output_dir / "bootSound.wav").is_file()
    assert api.clear_boot_sound(job.job_id)["ok"]
    assert not stored.exists()
    assert not (job.output_dir / "bootSound.wav").exists()


def test_worker_is_released_before_follow_up_queue_runs(api: WebUIApi, monkeypatch: pytest.MonkeyPatch):
    gate = threading.Event()
    follow_up = threading.Event()

    def target():
        gate.wait(2)

    def pending():
        assert api._worker is None
        follow_up.set()

    monkeypatch.setattr(api, "_run_pending_auto_artwork", pending)
    api._start_worker("test", "Testing background state", target)
    worker = api._worker
    assert worker is not None
    gate.set()
    worker.join(3)
    assert not worker.is_alive()
    assert follow_up.is_set()
    assert api._busy_kind == ""


def test_detached_window_bridges_track_live_parent_state(api: WebUIApi, tmp_path: Path):
    job = add_one_game(api, tmp_path)
    screenshot = make_image(tmp_path / "preview.png", (45, 80, 125))
    assert api.import_screenshot(job.job_id, str(screenshot))["ok"]
    preview_api = PreviewWindowApi(api, job.job_id, "icon")
    state = preview_api.get_state()
    assert state["ok"] and state["available"] and state["label"] == "Icon Image"
    image = preview_api.get_preview(600, 500)
    assert image["ok"] and image["source_width"] == 128

    panel_api = PreviewPanelWindowApi(api)
    panel = panel_api.get_state()
    assert panel["ok"] and panel["job_id"] == job.job_id
    assert panel["preview_available"]["icon"] is True

    initial_revision = state["revision"]
    preview_api._select(job.job_id, "tv")
    changed = preview_api.get_state()
    assert changed["label"] == "TV Image"
    assert changed["revision"] != initial_revision

    api.log("Detached activity test")
    activity_api = ActivityWindowApi(api)
    activity = activity_api.get_state()
    assert activity["ok"]
    assert activity["activity"][-1].endswith("Detached activity test")


def test_reattach_controls_restore_embedded_preview_and_activity(api: WebUIApi):
    class FakeWindow:
        def __init__(self):
            self.destroyed = False

        def destroy(self):
            self.destroyed = True

    preview_window = FakeWindow()
    preview_api = PreviewPanelWindowApi(api)
    api._preview_panel_window = preview_window
    api._preview_panel_api = preview_api
    api._child_apis.append(preview_api)
    result = api.reattach_preview_panel()
    assert result["ok"] and preview_window.destroyed
    assert api.get_state()["preview_detached"] is False
    assert preview_api not in api._child_apis

    activity_window = FakeWindow()
    api._activity_window = activity_window
    result = api.reattach_activity_window()
    assert result["ok"] and activity_window.destroyed
    assert api.get_state()["activity_detached"] is False


def test_preview_layout_matches_original_two_by_two_grid():
    root = Path(__file__).resolve().parents[1] / "vc_extractor" / "webui_assets"
    style = (root / "style.css").read_text(encoding="utf-8")
    panel_html = (root / "preview_panel_window.html").read_text(encoding="utf-8")
    panel_style = (root / "window.css").read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(2, minmax(220px, 1fr));" in style
    assert "grid-column: 3" not in style
    assert panel_html.index('data-preview="title"') < panel_html.index('data-preview="icon"')
    assert panel_html.index('data-preview="icon"') < panel_html.index('data-preview="tv"')
    assert panel_html.index('data-preview="tv"') < panel_html.index('data-preview="gamepad"')
    assert "grid-template-columns: repeat(2, minmax(260px, 1fr));" in panel_style
    preview_script = (root / "preview_window.js").read_text(encoding="utf-8")
    assert "ResizeObserver(fitPreview)" in preview_script
    assert "get_preview(3840, 2160)" in preview_script
    assert "availableHeight / sourceHeight, 4" not in preview_script


def test_workspace_defaults_to_expanded_game_panel_and_compact_framing_dialog():
    root = Path(__file__).resolve().parents[1] / "vc_extractor" / "webui_assets"
    style = (root / "style.css").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert "minmax(430px, var(--controls-width, 2fr)) 8px minmax(400px, 1fr)" in style
    assert ".modal-backdrop.framing-backdrop" in style
    assert ".modal.framing" in style
    framing_backdrop = style.split(".modal-backdrop.framing-backdrop", 1)[1].split("}", 1)[0]
    assert "background: transparent;" in framing_backdrop
    assert "backdrop-filter: none;" in framing_backdrop
    assert "blur(" not in framing_backdrop
    assert 'size: "framing"' in script
    assert "liveUpdateQueued" in script
    assert "Applying the latest change" in script


def test_main_webui_fits_the_viewport_with_panel_level_scrolling():
    root = Path(__file__).resolve().parents[1] / "vc_extractor" / "webui_assets"
    style = (root / "style.css").read_text(encoding="utf-8")
    page_rule = style.split("html,\nbody {", 1)[1].split("}", 1)[0]
    shell_rule = style.split(".app-shell {", 1)[1].split("}", 1)[0]
    content_rule = style.split(".main-content {", 1)[1].split("}", 1)[0]
    table_rule = style.split(".table-wrap {", 1)[1].split("}", 1)[0]
    workspace_rule = style.split(".workspace-grid {", 1)[1].split("}", 1)[0]
    activity_rule = style.split("\n.activity-log {", 1)[1].split("}", 1)[0]

    assert "overflow: hidden;" in page_rule
    assert "height: 100dvh;" in shell_rule
    assert "grid-template-rows: auto minmax(0, 1fr);" in shell_rule
    assert "grid-template-rows: auto auto auto minmax(0, 1fr) auto auto;" in content_rule
    assert "overflow: hidden;" in content_rule
    assert "min-height: 0;" in workspace_rule
    assert "overflow: hidden;" in workspace_rule
    assert "overflow: auto;" in table_rule
    assert "overflow: auto;" in activity_rule
    assert "grid-template-rows: repeat(2, minmax(0, 1fr));" in style
    assert "overflow-y: auto;" not in style


def test_help_describes_local_webui_and_private_rom_handling(api: WebUIApi):
    topic = api.get_help_topic("Getting Started")
    assert topic["ok"]
    assert "local WebUI workspace" in topic["body"]
    assert "does not upload ROMs" in topic["body"]
