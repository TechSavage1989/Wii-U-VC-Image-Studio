from pathlib import Path

from PIL import Image, ImageDraw

from vc_extractor.scoring import FrameSample, select_candidates
from vc_extractor.exporter import ExportOptions, export_selected, make_metadata_text, export_metadata_only
from vc_extractor.roms import RomInfo
from vc_extractor.gui import App


def test_scoring_and_export(tmp_path: Path):
    samples = []
    for i in range(8):
        im = Image.new("RGB", (320, 240), "black")
        draw = ImageDraw.Draw(im)
        if i >= 3:
            draw.rectangle((35, 30, 285, 205), fill=(70, 90, 120))
            draw.text((80, 90), "TITLE SCREEN", fill="white")
        else:
            draw.rectangle((130, 100, 190, 140), fill=(40 + i * 20, 40, 40))
        p = tmp_path / f"f{i}.png"
        im.save(p)
        samples.append(FrameSample(p, i * 1.0))
    candidates = select_candidates(samples, 3)
    assert candidates
    info = RomInfo(tmp_path / "test.nds", "Nintendo DS", "test.nds", "TEST", "USA", "Test", "RetroArch")
    opts = ExportOptions(tmp_path / "out")
    out = export_selected(info, candidates[0], candidates, opts)
    assert (out / "title_screen.png").exists()
    assert (out / "metadata.txt").exists()
    assert (out / "Icon Image.png").exists()
    assert Image.open(out / "Icon Image.png").size == (128, 128)
    assert (out / "TV Image.png").exists()
    assert (out / "Gamepad Image.png").exists()
    assert Image.open(out / "TV Image.png").size == (1280, 720)
    assert Image.open(out / "Gamepad Image.png").size == (854, 480)


def test_nds_header_detection(tmp_path: Path):
    data = bytearray(0x200)
    data[0:12] = b"MYJAPCOACH  "
    data[0x0C:0x10] = b"CJCE"
    p = tmp_path / "coach.nds"
    p.write_bytes(data)
    from vc_extractor.roms import detect_rom
    info = detect_rom(p)
    assert info is not None
    assert info.system == "Nintendo DS"
    assert info.product_code == "CJCE"
    assert info.region == "USA"


def test_gba_header_detection(tmp_path: Path):
    data = bytearray(0xC0)
    data[0xA0:0xAC] = b"TEST GAME   "
    data[0xAC:0xB0] = b"ABCE"
    p = tmp_path / "game.gba"
    p.write_bytes(data)
    from vc_extractor.roms import detect_rom
    info = detect_rom(p)
    assert info is not None
    assert info.system == "Game Boy Advance"
    assert info.product_code == "ABCE"
    assert info.region == "USA"


def test_nds_auto_selects_single_screen(tmp_path: Path):
    from vc_extractor.scoring import select_nds_candidates

    samples = []
    for i in range(7):
        top = Image.new("RGB", (256, 192), (10, 10, 14))
        bottom = Image.new("RGB", (256, 192), (35, 45, 85))
        draw = ImageDraw.Draw(bottom)
        draw.rectangle((18, 20, 238, 170), outline="white", width=3)
        draw.text((55, 72), "GAME TITLE", fill="white")
        draw.text((74, 112), "PRESS START", fill=(240, 220, 80) if i % 2 else (120, 110, 50))
        combined = Image.new("RGB", (256, 384), "black")
        combined.paste(top, (0, 0))
        combined.paste(bottom, (0, 192))
        p = tmp_path / f"nds{i}.png"
        combined.save(p)
        samples.append(FrameSample(p, float(i)))

    candidates = select_nds_candidates(samples, tmp_path / "split", 4)
    assert candidates
    assert candidates[0].screen_label == "bottom"
    with Image.open(candidates[0].path) as im:
        assert im.width / im.height > 1.2
        assert im.width / im.height < 1.5


def test_output_folder_uses_game_name_and_code(tmp_path: Path):
    info = RomInfo(tmp_path / "My Japanese Coach.nds", "Nintendo DS", "My Japanese Coach", "CJCE", "USA", "MYJAPCOACH", "RetroArch")
    sample_path = tmp_path / "sample.png"
    Image.new("RGB", (256, 192), "black").save(sample_path)
    sample = FrameSample(sample_path, 0.0, score=1.0)
    out = export_selected(info, sample, [sample], ExportOptions(tmp_path / "out"))
    assert out.name == "My Japanese Coach [CJCE]"


def test_libretro_dat_parser_extracts_metadata_and_hashes(tmp_path: Path):
    from vc_extractor.metadata import _extract_game_blocks, _entry_from_block, _index_dat
    dat = """clrmamepro (
 name "Test"
)

game (
 name "Example Game (USA)"
 developer "Example Dev"
 publisher "Example Pub"
 releaseyear 2006
 users 4
 rom ( name "Example.gba" size 4 crc ABCDEF01 md5 11111111111111111111111111111111 sha1 2222222222222222222222222222222222222222 )
)
"""
    blocks = _extract_game_blocks(dat)
    assert len(blocks) == 1
    entry = _entry_from_block(blocks[0])
    assert entry["name"] == "Example Game (USA)"
    assert entry["releaseyear"] == "2006"
    assert entry["users"] == "4"
    assert entry["developer"] == "Example Dev"
    assert entry["publisher"] == "Example Pub"
    path = tmp_path / "test.dat"
    path.write_text(dat, encoding="utf-8")
    idx = _index_dat(path)
    assert idx["crc:ABCDEF01"]["name"] == "Example Game (USA)"


def test_libretro_gamecube_serial_lookup_without_hash_match(tmp_path: Path, monkeypatch):
    from vc_extractor import metadata as md
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path / "config")
    rom = tmp_path / "game.iso"
    rom.write_bytes(b"not a real disc")
    dat = tmp_path / "gc.dat"
    dat.write_text("""game (
 name "Example Cube Game (USA)"
 serial "GEXE01"
 developer "Example Dev"
 publisher "Nintendo"
 releaseyear 2004
 users 4
 rom ( name "Example.iso" serial "GEXE01" )
)
""", encoding="utf-8")
    monkeypatch.setattr(md, "_download_dat", lambda *args, **kwargs: dat)
    result = md.lookup_libretro(rom, "GameCube", "USA", "GEXE01", md.LibretroSettings(), use_cache=False)
    assert result.title == "Example Cube Game"
    assert result.release_year == "2004"
    assert result.players == "4"
    assert result.publisher == "Nintendo"
    assert result.developer == "Example Dev"
    assert result.source == "Libretro Database"


def test_metadata_text_contains_uwuvci_fields(tmp_path: Path):
    info = RomInfo(
        tmp_path / "My Japanese Coach (USA).nds", "Nintendo DS", "My Japanese Coach (USA)",
        "CJCE", "USA", "MYJAPCOACH", "RetroArch", release_year="2008", players="1",
        publisher="Ubisoft", developer="Sensory Sweep", metadata_source="Libretro Database"
    )
    sample = FrameSample(tmp_path / "sample.png", 12.5, score=1.0, screen_label="bottom")
    text = make_metadata_text(info, sample)
    assert "Game Name: My Japanese Coach" in text
    assert "Console: Nintendo DS" in text
    assert "Release Year: 2008" in text
    assert "Players: 1" in text
    assert "Product Code: CJCE" in text
    assert "Region: USA" in text
    assert "Selected Capture Screen: bottom" in text
    assert "TV Image.png" in text
    assert "Gamepad Image.png" in text


def test_metadata_can_be_written_before_capture(tmp_path: Path):
    info = RomInfo(
        tmp_path / "game.nds", "Nintendo DS", "Example Game", "ABCE", "USA", "EXAMPLE", "RetroArch",
        release_year="2008", players="2", metadata_source="Libretro Database"
    )
    out = export_metadata_only(info, ExportOptions(tmp_path / "out"), None)
    metadata = (out / "metadata.txt").read_text(encoding="utf-8")
    assert "Game Name: Example Game" in metadata
    assert "Title Screen: Not captured/imported yet" in metadata
    assert not (out / "title_screen.png").exists()


def test_imported_image_export_builds_all_artwork(tmp_path: Path):
    image_path = tmp_path / "manual_title.png"
    Image.new("RGB", (640, 480), (40, 80, 120)).save(image_path)
    info = RomInfo(
        image_path, "NES", "Manual Game", "", "USA", "Manual Game", "Image",
        release_year="1987", players="2", metadata_source="Manual image import"
    )
    sample = FrameSample(image_path, 0.0, score=1.0)
    out = export_selected(info, sample, [sample], ExportOptions(tmp_path / "out", screen_crop="full"))
    assert Image.open(out / "title_screen.png").size == (640, 480)
    assert Image.open(out / "Icon Image.png").size == (128, 128)
    assert Image.open(out / "TV Image.png").size == (1280, 720)
    assert Image.open(out / "Gamepad Image.png").size == (854, 480)
    assert "Input Type: Imported image" in (out / "metadata.txt").read_text(encoding="utf-8")


def test_v31_import_screenshot_is_attached_to_selected_rom():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "Select exactly one loaded ROM first" in gui_text
    assert "ImportImageDialog" not in gui_text
    assert "job.image_imported = True" in gui_text
    assert "source_display = \"ROM + Image\" if job.image_imported else job.source_kind" in gui_text


def test_v31_metadata_is_written_and_fetched_on_rom_add():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("    def _add_paths(self, paths):")
    end = gui_text.index("    def import_images(self):", start)
    block = gui_text[start:end]
    assert "self._write_metadata_now(job)" in block
    assert "self._start_metadata_lookup(added_indices)" in block
    assert block.index("self._write_metadata_now(job)") < block.index("self._start_metadata_lookup(added_indices)")


def test_v32_review_button_and_output_checkboxes_exist():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "self.review_btn = ttk.Button(progress_line, text=\"Review Selected\"" in gui_text
    assert "self.make_icon_var = tk.BooleanVar(value=True)" in gui_text
    assert "self.make_tv_var = tk.BooleanVar(value=True)" in gui_text
    assert "self.make_gamepad_var = tk.BooleanVar(value=True)" in gui_text


def test_optional_image_outputs_can_be_disabled(tmp_path: Path):
    image_path = tmp_path / "manual_title.png"
    Image.new("RGB", (320, 240), (60, 80, 120)).save(image_path)
    info = RomInfo(image_path, "NES", "Manual Game", "", "USA", "Manual Game", "Image")
    sample = FrameSample(image_path, 0.0, score=1.0)
    opts = ExportOptions(tmp_path / "out", screen_crop="full", create_icon=False, create_tv=True, create_gamepad=False)
    out = export_selected(info, sample, [sample], opts)
    assert not (out / "Icon Image.png").exists()
    assert (out / "TV Image.png").exists()
    assert not (out / "Gamepad Image.png").exists()


def test_custom_screenshot_placement_is_used(tmp_path: Path):
    from vc_extractor.exporter import shot_rect_for_info, default_shot_rect
    info = RomInfo(tmp_path / "x.gba", "Game Boy Advance", "X")
    assert shot_rect_for_info(info) == default_shot_rect("Game Boy Advance")
    info.shot_x, info.shot_y, info.shot_width, info.shot_height = 200, 210, 350, 240
    assert shot_rect_for_info(info) == (200, 210, 350, 240)


def test_gametdb_xml_index_extracts_year_and_players(tmp_path: Path):
    from vc_extractor import metadata as md
    xml = '<datafile><game name="Example Game"><id>ABCE</id><region>NTSC-U</region><locale lang="EN"><title>Example Game</title></locale><developer>Dev</developer><publisher>Pub</publisher><date year="2007" month="2" day="3"/><input players="4"/><rom crc="ABCDEF01" md5="11111111111111111111111111111111" sha1="2222222222222222222222222222222222222222"/></game></datafile>'
    path = tmp_path / "db.xml"
    path.write_text(xml, encoding="utf-8")
    idx = md._gametdb_index(path)
    assert idx["id:ABCE"]["releaseyear"] == "2007"
    assert idx["id:ABCE"]["players"] == "4"
    assert idx["crc:ABCDEF01"]["developer"] == "Dev"


def test_v33_select_all_none_and_placement_controls_exist():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Select All"' in gui_text
    assert 'text="Select None"' in gui_text
    assert 'Screenshot Placement / Size' in gui_text


def test_v34_metadata_panel_is_compact_and_select_none_not_fixed_width():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Game Metadata"' in gui_text
    assert "No account or API key" not in gui_text
    assert 'text="Select None", command=self._select_no_outputs).pack' in gui_text
    assert 'text="Select None", command=self._select_no_outputs, width=' not in gui_text


def test_title_fallback_can_fill_consistent_missing_release_year(tmp_path: Path):
    from vc_extractor import metadata as md
    dat = tmp_path / "releaseyear.dat"
    dat.write_text(
        'game (\n comment "Example Game (Europe)"\n releaseyear "2002"\n rom ( crc 11111111 )\n)\n'
        'game (\n comment "Example Game (USA)"\n releaseyear "2002"\n rom ( crc 22222222 )\n)\n',
        encoding="utf-8",
    )
    idx = md._index_dat(dat)
    primary = {"name": "Example Game (USA)", "comment": ""}
    entry = md._lookup_optional_title(idx, primary)
    assert entry is not None
    assert entry["releaseyear"] == "2002"


def test_old_metadata_cache_schema_is_ignored(tmp_path: Path, monkeypatch):
    from vc_extractor import metadata as md
    monkeypatch.setattr(md, "_cache_path", lambda: tmp_path / "cache.json")
    (tmp_path / "cache.json").write_text('{"ABC": {"title": "Old", "release_year": ""}}', encoding="utf-8")
    assert md.cached_metadata("ABC") is None


def test_icon_image_uses_template_border_for_supported_system(tmp_path: Path):
    info = RomInfo(tmp_path / "game.gba", "Game Boy Advance", "Template Test", "ABCE", "USA", "TEMPLATE", "Image")
    img = tmp_path / "sample.png"
    Image.new("RGB", (400, 300), (25, 120, 220)).save(img)
    sample = FrameSample(img, 0.0, score=1.0)
    out = export_selected(info, sample, [sample], ExportOptions(tmp_path / "out", screen_crop="full"))
    icon = Image.open(out / "Icon Image.png").convert("RGB")
    # Top header region should preserve the template border/logo instead of being filled entirely by the screenshot.
    header = icon.crop((0, 0, 128, 20))
    colors = header.getcolors(maxcolors=10000) or []
    assert len(colors) > 1


def test_icon_template_choices_include_screenshot_only_and_system_frames():
    from vc_extractor.exporter import icon_template_choices, default_icon_template_for_system, SCREENSHOT_ONLY_TEMPLATE
    gba = icon_template_choices("Game Boy Advance")
    assert gba[0] == ("Screenshot only", SCREENSHOT_ONLY_TEMPLATE)
    assert ("Light Frame", "GBA alt1.png") in gba
    assert ("Dark Frame", "GBA alt2.png") in gba
    assert default_icon_template_for_system("Game Boy Advance") == "GBA alt1.png"


def test_gui_contains_icon_frame_selector_controls():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Icon Image frame"' in gui_text
    assert 'def _apply_selected_icon_frame' in gui_text


def test_gbc_and_gb_accept_vbam_core(tmp_path: Path):
    from vc_extractor.emulators import discover_core
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    cores = tmp_path / "cores"
    cores.mkdir()
    vbam = cores / "vbam_libretro.dll"
    vbam.write_bytes(b"")
    assert discover_core(retro, "Game Boy Color") == vbam
    assert discover_core(retro, "Game Boy") == vbam


def test_gbc_prefers_mgba_when_available(tmp_path: Path):
    from vc_extractor.emulators import discover_core
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    cores = tmp_path / "cores"
    cores.mkdir()
    mgba = cores / "mgba_libretro.dll"
    mgba.write_bytes(b"")
    assert discover_core(retro, "Game Boy Color") == mgba


def test_gb_family_accepts_vba_next_core(tmp_path: Path):
    from vc_extractor.emulators import discover_core
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    cores = tmp_path / "cores"
    cores.mkdir()
    vban = cores / "vba_next_libretro.dll"
    vban.write_bytes(b"")
    assert discover_core(retro, "Game Boy") == vban
    assert discover_core(retro, "Game Boy Color") == vban


def test_gui_contains_selected_preview_tiles():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'Selected Preview' in gui_text
    assert 'Title Screen' in gui_text
    assert 'Icon Image' in gui_text
    assert 'TV Image' in gui_text
    assert 'Gamepad Image' in gui_text


def test_screenshot_only_icon_bypasses_frame_and_letterboxes(tmp_path: Path):
    from vc_extractor.exporter import SCREENSHOT_ONLY_TEMPLATE
    image_path = tmp_path / "wide.png"
    Image.new("RGB", (320, 160), (20, 100, 200)).save(image_path)
    info = RomInfo(image_path, "Game Boy Advance", "Wide Game", "ABCE", "USA", "Wide Game", "Image")
    info.icon_template = SCREENSHOT_ONLY_TEMPLATE
    sample = FrameSample(image_path, 0.0, score=1.0)
    out = export_selected(info, sample, [sample], ExportOptions(tmp_path / "out", screen_crop="full", create_tv=False, create_gamepad=False))
    icon = Image.open(out / "Icon Image.png").convert("RGB")
    assert icon.size == (128, 128)
    # A 2:1 screenshot should be centered at 128x64 with black letterboxing above and below.
    assert icon.getpixel((64, 5)) == (0, 0, 0)
    assert icon.getpixel((64, 64)) == (20, 100, 200)


def test_gui_uses_content_based_window_sizing():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'def _autosize_to_content' in gui_text
    assert 'self.after_idle(self._autosize_to_content)' in gui_text
    assert 'self.geometry("1280x900")' not in gui_text


def test_dark_icon_frame_has_no_light_inner_seam(tmp_path: Path):
    from vc_extractor.exporter import make_icon_tex
    info = RomInfo(tmp_path / "game.gba", "Game Boy Advance", "Seam Test", "ABCE", "USA", "SEAM", "Image", icon_template="GBA alt2.png")
    src = Image.new("RGB", (256, 192), (30, 90, 180))
    icon = make_icon_tex(src, info)
    # Inner seam rows used by the dark frame should now show screenshot color instead of a light gray line.
    assert icon.getpixel((64, 22))[2] > 150
    assert icon.getpixel((64, 117))[2] > 150


def test_banner_cover_fit_preserves_source_aspect_ratio():
    from vc_extractor.exporter import _cover_fit
    src = Image.new("RGB", (400, 200), "red")
    # Add a green vertical stripe at the center so a centered crop remains identifiable.
    d = ImageDraw.Draw(src)
    d.rectangle((190, 0, 210, 199), fill="green")
    fitted = _cover_fit(src, (200, 200))
    assert fitted.size == (200, 200)
    # A 2:1 source covering a square must crop horizontally, not squash the entire 400px width into 200px.
    center = fitted.getpixel((100, 100))
    assert center[1] > center[0]


def test_gamepad_renderer_accepts_original_screenshot_directly(tmp_path: Path):
    from vc_extractor.exporter import _render_banner_drc
    info = RomInfo(tmp_path / "game.gba", "Game Boy Advance", "Aspect Test", "ABCE", "USA", "ASPECT", "Image")
    src = Image.new("RGB", (320, 240), (10, 50, 100))
    out = _render_banner_drc(src, info, ExportOptions(tmp_path / "out"))
    assert out.size == (854, 480)


def test_wii_iso_header_detection(tmp_path: Path):
    import struct
    from vc_extractor.roms import detect_rom, WII_DISC_MAGIC
    data = bytearray(0x440)
    data[0:6] = b"RDSE70"
    data[0x18:0x1C] = struct.pack(">I", WII_DISC_MAGIC)
    data[0x20:0x20+16] = b"Example Wii Game"
    p = tmp_path / "RDSE70.iso"
    p.write_bytes(data)
    info = detect_rom(p)
    assert info is not None
    assert info.system == "Wii"
    assert info.product_code == "RDSE70"
    assert info.region == "USA"
    assert info.backend == "RetroArch"


def test_gamecube_iso_still_detects_separately(tmp_path: Path):
    import struct
    from vc_extractor.roms import detect_rom, GAMECUBE_DISC_MAGIC
    data = bytearray(0x440)
    data[0:6] = b"GABE01"
    data[0x1C:0x20] = struct.pack(">I", GAMECUBE_DISC_MAGIC)
    p = tmp_path / "game.iso"
    p.write_bytes(data)
    info = detect_rom(p)
    assert info is not None
    assert info.system == "GameCube"
    assert info.backend == "RetroArch"


def test_wii_template_and_system_folder_are_configured(tmp_path: Path):
    from vc_extractor.roms import RomInfo, system_folder
    from vc_extractor.exporter import _template_name
    info = RomInfo(tmp_path / "game.iso", "Wii", "Example")
    assert system_folder("Wii") == "wii"
    assert _template_name(info) == "WII.png"


def test_msx_and_tgcd_extensions_are_accepted(tmp_path: Path):
    from vc_extractor.roms import detect_rom
    msx = tmp_path / "game.rom"
    msx.write_bytes(b"test")
    tg = tmp_path / "game.cue"
    tg.write_text('FILE "game.bin" BINARY\n', encoding="utf-8")
    assert detect_rom(msx).system == "MSX"
    assert detect_rom(tg).system == "TurboGrafx-CD / PC Engine CD"


def test_dolphin_libretro_core_used_for_gamecube_wii_and_wiiware(tmp_path: Path):
    from vc_extractor.emulators import discover_core
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    cores = tmp_path / "cores"
    cores.mkdir()
    dolphin = cores / "dolphin_libretro.dll"
    dolphin.write_bytes(b"")
    assert discover_core(retro, "GameCube") == dolphin
    assert discover_core(retro, "Wii") == dolphin
    assert discover_core(retro, "WiiWare") == dolphin


def test_wii_and_gamecube_roms_default_to_retroarch_backend(tmp_path: Path):
    from vc_extractor.roms import _detect_wii, _detect_gamecube
    wii = tmp_path / "wii.iso"
    data = bytearray(0x100)
    data[0:6] = b"RDSE70"
    data[0x18:0x1C] = bytes.fromhex("5D1C9EA3")
    wii.write_bytes(data)
    info = _detect_wii(wii)
    assert info.backend == "RetroArch"


def test_v44_capture_worker_has_common_finalize_path():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "def _finalize_captured_samples" in gui_text
    assert "Timed capture completed normally. Captured frames were finalized and exported." in gui_text
    assert "Export finished but expected files were not created" in gui_text


def test_v44_nds_worker_uses_dataclass_replace_not_tk_export_options():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("    def _finalize_captured_samples")
    end = gui_text.index("    def _capture_worker", start)
    block = gui_text[start:end]
    assert 'replace(options, screen_crop="full")' in block
    assert "self._export_options(" not in block


def test_retroarch_system_directory_parses_config(tmp_path: Path):
    from vc_extractor.emulators import retroarch_system_directory, dolphin_sys_directory
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    custom = tmp_path / "CustomSystem"
    (tmp_path / "retroarch.cfg").write_text(f'system_directory = "{custom}"\n', encoding="utf-8")
    assert retroarch_system_directory(retro) == custom
    assert dolphin_sys_directory(retro) == custom / "dolphin-emu" / "Sys"


def test_dolphin_system_files_readiness(tmp_path: Path):
    from vc_extractor.emulators import dolphin_system_files_ready
    retro = tmp_path / "retroarch.exe"
    retro.write_bytes(b"")
    assert not dolphin_system_files_ready(retro)
    sysdir = tmp_path / "system" / "dolphin-emu" / "Sys"
    sysdir.mkdir(parents=True)
    (sysdir / "dummy.ini").write_text("x", encoding="utf-8")
    assert dolphin_system_files_ready(retro)


def test_dolphin_capture_uses_temporary_gl_compatibility_config():
    source = (Path(__file__).resolve().parents[1] / "vc_extractor" / "emulators.py").read_text(encoding="utf-8")
    assert "extra_video_cfg.append('video_driver = \"gl\"')" in source


def test_v418_wiiu_input_formats_are_not_supported(tmp_path: Path):
    from vc_extractor.roms import detect_rom, ROM_EXTENSIONS
    for ext in (".wud", ".wux", ".wua", ".rpx"):
        assert ext not in ROM_EXTENSIONS
        p = tmp_path / ("Example" + ext)
        p.write_bytes(b"test")
        assert detect_rom(p) is None


def test_v418_cemu_and_wiiu_capture_removed():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    emu_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "emulators.py").read_text(encoding="utf-8")
    assert 'Cemu.exe' not in gui_text
    assert 'cemu_path' not in gui_text
    assert 'def capture_cemu' not in emu_text
    assert 'cemu_path' not in emu_text
    assert 'Falling back automatically to external Dolphin.exe' in emu_text


def test_v418_wiiu_metadata_mapping_removed():
    from vc_extractor.metadata import SYSTEM_DATABASE_NAMES
    assert "Wii U" not in SYSTEM_DATABASE_NAMES


def test_black_frame_detection_for_dolphin_fallback(tmp_path: Path):
    from vc_extractor.emulators import _samples_are_effectively_black
    from vc_extractor.scoring import FrameSample
    black = tmp_path / "black.png"
    visible = tmp_path / "visible.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(black)
    Image.new("RGB", (64, 64), (80, 100, 120)).save(visible)
    assert _samples_are_effectively_black([FrameSample(black, 0.0)])
    assert not _samples_are_effectively_black([FrameSample(visible, 0.0)])


def test_optional_banner_metadata_can_be_disabled(tmp_path: Path):
    image_path = tmp_path / "manual_title.png"
    Image.new("RGB", (640, 480), (70, 100, 160)).save(image_path)
    info = RomInfo(image_path, "Wii", "Example Wii Game", "RDSE70", "USA", "EXAMPLE", "Image", release_year="2007", players="2")
    sample = FrameSample(image_path, 0.0, score=1.0)
    out = export_selected(info, sample, [sample], ExportOptions(tmp_path / "out", screen_crop="full", include_banner_metadata=False))
    tv = Image.open(out / "TV Image.png").convert("RGB")
    # The text anchor area should remain mostly unchanged when banner metadata is disabled.
    a = tv.getpixel((650, 360))
    b = tv.getpixel((650, 460))
    assert a != (32, 32, 32)
    assert b != (32, 32, 32)


def test_gui_contains_banner_metadata_toggle():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'Include game title / metadata on TV and Gamepad images' in gui_text
    assert 'def _apply_banner_metadata_setting' in gui_text


def test_wii_banner_title_is_fit_within_text_frame(tmp_path: Path):
    from vc_extractor.exporter import _render_banner_tv, _rendered_text_width, _fit_title_lines, BANNER_TEXT_RIGHT, BANNER_TEXT_PADDING_X, TITLE_RECT_SHORT, TITLE_FONT_PX
    info = RomInfo(tmp_path / "RDSE70.iso", "Wii", "Dragon Ball Z - Budokai Tenkaichi 3", "RDSE70", "USA", "Dragon Ball Z - Budokai Tenkaichi 3", "Image", release_year="2007", players="2")
    src = Image.new("RGB", (640, 360), (220, 180, 120))
    out = _render_banner_tv(src, info, ExportOptions(tmp_path / "out"))
    assert out.size == (1280, 720)
    probe = ImageDraw.Draw(out)
    scale = 1.0
    title = "Dragon Ball Z: Budokai Tenkaichi 3"
    max_width = BANNER_TEXT_RIGHT - TITLE_RECT_SHORT[0] - BANNER_TEXT_PADDING_X
    font, lines = _fit_title_lines(probe, title, ExportOptions(tmp_path / "out"), base_size=TITLE_FONT_PX, max_width=max_width, min_size=24, scale=scale)
    assert lines
    assert max(_rendered_text_width(probe, line, font, bold=True, scale=scale) for line in lines) <= max_width


def test_preview_has_single_selected_preview_and_detach_controls():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Selected Preview"' in gui_text
    assert 'text="Live Preview"' not in gui_text
    assert 'text="Detach Preview"' in gui_text
    assert 'def detach_preview' in gui_text
    assert 'def reattach_preview' in gui_text


def test_nes_normalized_hash_variant_strips_ines_header(tmp_path: Path):
    from vc_extractor.metadata import compute_normalized_hash_variants, _hash_bytes
    payload = bytes(range(64)) * 32
    header = bytearray(16)
    header[:4] = b"NES\x1a"
    rom = tmp_path / "game.nes"
    rom.write_bytes(bytes(header) + payload)
    variants = compute_normalized_hash_variants(rom, "NES")
    expected = _hash_bytes(payload)
    assert any(v.sha1 == expected.sha1 for v in variants)


def test_snes_normalized_hash_variant_strips_copier_header(tmp_path: Path):
    from vc_extractor.metadata import compute_normalized_hash_variants, _hash_bytes
    payload = b"S" * 0x8000
    rom = tmp_path / "game.smc"
    rom.write_bytes(b"H" * 512 + payload)
    variants = compute_normalized_hash_variants(rom, "SNES")
    expected = _hash_bytes(payload)
    assert any(v.sha1 == expected.sha1 for v in variants)


def test_metadata_title_fallback_works_when_hash_does_not_match(tmp_path: Path, monkeypatch):
    from vc_extractor import metadata as md
    monkeypatch.setattr(md, "config_dir", lambda: tmp_path / "config")
    rom = tmp_path / "Example Adventure (USA).nes"
    rom.write_bytes(b"not a recognized no-intro dump")
    primary = tmp_path / "primary.dat"
    primary.write_text('''game (\n name "Example Adventure (USA)"\n rom ( crc 11111111 )\n)\n''', encoding="utf-8")
    year = tmp_path / "year.dat"
    year.write_text('''game (\n comment "Example Adventure (USA)"\n releaseyear "1990"\n rom ( crc 11111111 )\n)\n''', encoding="utf-8")
    users = tmp_path / "users.dat"
    users.write_text('''game (\n comment "Example Adventure (USA)"\n users "2"\n rom ( crc 11111111 )\n)\n''', encoding="utf-8")

    def fake_download(rel, settings, force=False, optional=False):
        if "no-intro" in rel:
            return primary
        if "/releaseyear/" in rel:
            return year
        if "/maxusers/" in rel:
            return users
        return None

    monkeypatch.setattr(md, "_download_dat", fake_download)
    monkeypatch.setattr(md, "_lookup_gametdb", lambda *args, **kwargs: None)
    result = md.lookup_libretro(rom, "NES", "USA", "", md.LibretroSettings(), use_cache=False, title_hint=rom.stem)
    assert result.title == "Example Adventure"
    assert result.release_year == "1990"
    assert result.players == "2"


def test_wii_text_safe_zones_do_not_intersect_screenshot():
    from vc_extractor.exporter import (
        WII_TITLE_BAND,
        WII_META_BAND,
        WII_SCREENSHOT_TOP_WITH_TITLE,
        WII_SCREENSHOT_BOTTOM_WITH_META,
    )
    title_bottom = WII_TITLE_BAND[1] + WII_TITLE_BAND[3]
    meta_top = WII_META_BAND[1]
    assert title_bottom < WII_SCREENSHOT_TOP_WITH_TITLE
    assert WII_SCREENSHOT_BOTTOM_WITH_META < meta_top


def test_wii_rendered_text_cannot_touch_screenshot_band(tmp_path: Path):
    from vc_extractor.exporter import _render_banner_tv, WII_SCREENSHOT_TOP_WITH_TITLE, WII_SCREENSHOT_BOTTOM_WITH_META
    src_color = (12, 80, 140)
    src = Image.new("RGB", (640, 360), src_color)
    info = RomInfo(
        tmp_path / "game.iso", "Wii", "Fire Emblem: Radiant Dawn", "RFEE01", "USA",
        "Fire Emblem: Radiant Dawn", "RetroArch", release_year="2008", players="1"
    )
    out = _render_banner_tv(src, info, ExportOptions(tmp_path / "out"))
    assert out.getpixel((640, WII_SCREENSHOT_TOP_WITH_TITLE)) == src_color
    assert out.getpixel((640, WII_SCREENSHOT_BOTTOM_WITH_META - 1)) == src_color
    assert out.getpixel((640, WII_SCREENSHOT_TOP_WITH_TITLE - 1)) != src_color
    assert out.getpixel((640, WII_SCREENSHOT_BOTTOM_WITH_META)) != src_color


def test_wii_text_safe_zones_have_visible_gaps():
    from vc_extractor.exporter import WII_TITLE_BAND, WII_META_BAND, WII_SCREENSHOT_TOP_WITH_TITLE, WII_SCREENSHOT_BOTTOM_WITH_META
    title_bottom = WII_TITLE_BAND[1] + WII_TITLE_BAND[3]
    meta_top = WII_META_BAND[1]
    assert WII_SCREENSHOT_TOP_WITH_TITLE - title_bottom >= 20
    assert meta_top - WII_SCREENSHOT_BOTTOM_WITH_META >= 20




def test_v413_wii_art_source_is_screenshot_or_custom_only():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'Import Custom TV/Gamepad Image' in gui_text
    assert 'Use Selected Screenshot for TV/Gamepad' in gui_text
    assert 'Use Dolphin Banner' not in gui_text
    assert 'Dolphin Banner' not in gui_text
    assert 'sources = ["Selected Screenshot"]' in gui_text
    assert 'sources.append("Custom Image")' in gui_text


def test_v413_external_dolphin_capture_fallback_remains():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'Dolphin.exe fallback' in gui_text
    assert 'external Dolphin.exe' in gui_text


def test_gui_has_explicit_screenshot_restore_and_custom_source_selector():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Use Selected Screenshot for TV/Gamepad"' in gui_text
    assert 'text="Import Custom TV/Gamepad Image"' in gui_text
    assert 'def restore_screenshot_for_tv_gamepad' in gui_text
    assert 'def _update_tv_gamepad_source_selector' in gui_text
    assert 'def _apply_tv_gamepad_source' in gui_text
    assert 'Dolphin Banner' not in gui_text


def test_v414_wii_metadata_does_not_shrink_screenshot_opening(tmp_path: Path):
    from vc_extractor.exporter import _render_banner_tv, ExportOptions, WII_SCREENSHOT_RECT
    src_color = (18, 90, 150)
    src = Image.new("RGB", (640, 360), src_color)
    info = RomInfo(tmp_path / "game.iso", "Wii", "Fire Emblem: Radiant Dawn", "RFEE01", "USA", "Fire Emblem: Radiant Dawn", "RetroArch", release_year="2008", players="1")
    with_text = _render_banner_tv(src, info, ExportOptions(tmp_path / "a", include_banner_metadata=True))
    without_text = _render_banner_tv(src, info, ExportOptions(tmp_path / "b", include_banner_metadata=False))
    x, y, w, h = WII_SCREENSHOT_RECT
    # The screenshot occupies the same full template opening whether text is enabled or disabled.
    for px, py in ((x + w // 2, y + 10), (x + w // 2, y + h // 2), (x + w // 2, y + h - 10)):
        assert with_text.getpixel((px, py)) == without_text.getpixel((px, py))


def test_v414_cover_fit_focus_moves_crop():
    from vc_extractor.exporter import _cover_fit
    src = Image.new("RGB", (300, 100), (0, 0, 0))
    for x in range(150):
        for y in range(100):
            src.putpixel((x, y), (220, 20, 20))
    for x in range(150, 300):
        for y in range(100):
            src.putpixel((x, y), (20, 20, 220))
    left = _cover_fit(src, (100, 100), focus_x=0, focus_y=50).convert("RGB")
    right = _cover_fit(src, (100, 100), focus_x=100, focus_y=50).convert("RGB")
    assert left.getpixel((50, 50))[0] > left.getpixel((50, 50))[2]
    assert right.getpixel((50, 50))[2] > right.getpixel((50, 50))[0]


def test_v414_gui_has_independent_crop_position_controls():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'Adjust Image Crop & Zoom' in gui_text
    assert 'Horizontal focus' in gui_text
    assert 'Vertical focus' in gui_text
    assert 'Icon Image' in gui_text
    assert 'TV Image' in gui_text
    assert 'Gamepad Image' in gui_text


def test_v415_crop_position_has_live_debounced_preview():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "PREVIEW_DELAY_MS = 160" in gui_text
    assert 'trace_add("write"' in gui_text
    assert "def _schedule_preview" in gui_text
    assert "self.after(self.PREVIEW_DELAY_MS, self._run_preview)" in gui_text
    assert "self.on_preview(values)" in gui_text


def test_v415_tv_gamepad_crop_positions_can_be_linked():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Link TV and Gamepad crop & zoom"' in gui_text
    assert '"tv_x": "gamepad_x"' in gui_text
    assert '"tv_y": "gamepad_y"' in gui_text
    assert '"gamepad_x": "tv_x"' in gui_text
    assert '"gamepad_y": "tv_y"' in gui_text


def test_v415_apply_updates_without_closing_crop_dialog():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("    def _apply_without_closing(self):")
    end = gui_text.index("    def _done(self):", start)
    block = gui_text[start:end]
    assert "self.on_preview(values)" in block
    assert "self.destroy()" not in block
    assert 'text="Done"' in gui_text


def test_v416_image_position_button_is_in_selected_preview_controls():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    preview_start = gui_text.index('preview_controls = ttk.Frame(right)')
    preview_end = gui_text.index('self.preview_host = ttk.Frame(right)', preview_start)
    preview_block = gui_text[preview_start:preview_end]
    assert 'text="Adjust Image Crop & Zoom"' in preview_block
    fields_start = gui_text.index('fields = ttk.Frame(left)')
    outputs_start = gui_text.index('outputs = ttk.LabelFrame(left', fields_start)
    fields_block = gui_text[fields_start:outputs_start]
    assert 'Adjust Image Crop & Zoom' not in fields_block
    assert 'Image Crop & Zoom' not in fields_block


def test_v417_zoom_changes_cover_scale():
    from vc_extractor.exporter import _cover_fit
    src = Image.new("RGB", (200, 100), (220, 20, 20))
    d = ImageDraw.Draw(src)
    d.rectangle((80, 0, 120, 99), fill=(20, 220, 20))
    normal = _cover_fit(src, (100, 100), focus_x=50, focus_y=50, zoom=100).convert("RGB")
    zoomed = _cover_fit(src, (100, 100), focus_x=50, focus_y=50, zoom=150).convert("RGB")
    assert normal.size == (100, 100)
    assert zoomed.size == (100, 100)
    # Zooming in makes the center stripe occupy more of the target width.
    normal_green = sum(1 for x in range(100) if normal.getpixel((x, 50))[1] > normal.getpixel((x, 50))[0])
    zoom_green = sum(1 for x in range(100) if zoomed.getpixel((x, 50))[1] > zoomed.getpixel((x, 50))[0])
    assert zoom_green > normal_green


def test_v417_zoom_out_can_reveal_padding():
    from vc_extractor.exporter import _cover_fit
    src = Image.new("RGB", (200, 100), (120, 80, 40))
    out = _cover_fit(src, (100, 100), zoom=50).convert("RGB")
    assert out.getpixel((50, 5)) == (0, 0, 0)
    assert out.getpixel((50, 50)) == (120, 80, 40)


def test_v417_detached_preview_keeps_crop_zoom_control():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("    def detach_preview(self):")
    end = gui_text.index("    def reattach_preview(self):", start)
    block = gui_text[start:end]
    assert 'text="Adjust Image Crop & Zoom"' in block
    assert "command=self.edit_image_crop_position" in block


def test_v418_crop_zoom_dialog_uses_detached_preview_as_owner():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("    def edit_image_crop_position(self):")
    end = gui_text.index("    def _browse_retro(self):", start)
    block = gui_text[start:end]
    assert "owner = self" in block
    assert "owner = self.preview_detached_window" in block
    assert "ImageCropPositionDialog(owner" in block
    update_start = gui_text.index("    def _update_preview(self):")
    update_end = gui_text.index("    def review_selected(self):", update_start)
    update_block = gui_text[update_start:update_end]
    assert "self.after_idle(self._autosize_detached_preview)" not in update_block
    assert "self._autosize_to_content" not in update_block


def test_v417_review_reexport_is_nonblocking_and_has_done_button():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    start = gui_text.index("class ReviewWindow")
    block = gui_text[start:]
    assert 'text="Done", command=self.destroy' in block
    assert 'self.save_status.config(text=f"Re-exported selected frame to:' in block
    assert 'messagebox.showinfo(APP_NAME, f"Re-exported selected frame' not in block


def test_v417_tv_gamepad_link_includes_zoom():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert '"tv_zoom": "gamepad_zoom"' in gui_text
    assert '"gamepad_zoom": "tv_zoom"' in gui_text
    assert 'self.vars["gamepad_zoom"].set(self.vars["tv_zoom"].get())' in gui_text


def test_wii_icon_frame_labels_and_defaults():
    from vc_extractor.exporter import icon_template_choices, default_icon_template_for_system
    wii = icon_template_choices("Wii")
    assert ("WiiWare Frame", "WiiIcon.png") in wii
    assert ("Wii Frame", "Wii2.png") in wii
    assert default_icon_template_for_system("Wii") == "Wii2.png"
    assert default_icon_template_for_system("WiiWare") == "WiiIcon.png"


def test_product_code_is_consolidated_into_game_details_dialog():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Product code"' in gui_text
    assert 'text="Edit Product Code"' not in gui_text
    assert 'def edit_code(self)' not in gui_text


def test_v420_detached_preview_geometry_is_preserved_during_live_refresh():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    detach_start = gui_text.index("    def detach_preview(self):")
    detach_end = gui_text.index("    def reattach_preview(self):", detach_start)
    detach_block = gui_text[detach_start:detach_end]
    assert "self.after_idle(self._autosize_detached_preview)" in detach_block
    update_start = gui_text.index("    def _update_preview(self):")
    update_end = gui_text.index("    def review_selected(self):", update_start)
    update_block = gui_text[update_start:update_end]
    assert "self.after_idle(self._autosize_detached_preview)" not in update_block
    assert "preserve" in update_block
    assert "size/position" in update_block


def test_v422_font_ui_is_generic():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'label="Artwork Font"' in gui_text
    assert 'label="Select Custom Font..."' in gui_text
    assert 'label="Use Automatic/Default Font"' in gui_text
    assert 'label="Show Active Font"' in gui_text
    assert 'title="Select custom font"' in gui_text
    assert 'self._path_row(paths, 3, "Custom font"' not in gui_text
    assert '"UWUVCI font.otf"' not in gui_text
    assert 'title="Select UWUVCI' not in gui_text


def test_v423_has_builtin_help_documentation_center():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    docs_text = (root / "vc_extractor" / "help_docs.py").read_text(encoding="utf-8")
    assert 'menubar.add_cascade(label="Help"' in gui_text
    assert 'label="Documentation Center"' in gui_text
    assert 'self.bind_all("<F1>"' in gui_text
    assert 'class DocumentationWindow' in gui_text
    assert 'class AboutWindow' in gui_text
    assert '"Getting Started"' in docs_text
    assert '"Artwork & Framing"' in docs_text
    assert '"Troubleshooting"' in docs_text
    assert '"Credits & Notices"' in docs_text


def test_v424_main_view_has_emulator_capture_requirement_notice():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'text="Screenshot capture requires an emulator"' in gui_text
    assert "Automatic ROM screenshot capture uses RetroArch" in gui_text
    assert "manual screenshot import does not require an emulator" in gui_text
    assert 'text="Capture Setup Help"' in gui_text
    assert 'self.open_documentation("Emulator Capture Setup")' in gui_text


def test_v425_application_icon_is_bundled_and_used():
    root = Path(__file__).resolve().parents[1]
    icon_path = root / "assets" / "vc_title_extractor.ico"
    assert icon_path.exists()
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'self.iconbitmap(default=str(icon_path))' in gui_text
    build_text = (root / "scripts" / "build_windows.py").read_text(encoding="utf-8")
    assert '"--icon", str(ROOT / "assets/vc_title_extractor.ico")' in build_text
    assert '"--add-data", str(ROOT / "assets") + ";assets"' in build_text


def test_v427_hover_tip_infrastructure_exists():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "class HoverTip:" in gui_text
    assert "def add_hover_tip(" in gui_text
    assert 'widget.bind("<Enter>"' in gui_text
    assert 'widget.bind("<Leave>"' in gui_text
    assert 'background="#FFFBEA"' in gui_text


def test_v427_main_workflow_controls_have_hover_help():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "add_hover_tip(self.tree" in gui_text
    assert "add_hover_tip(self.start_btn" in gui_text
    assert "add_hover_tip(self.review_btn" in gui_text
    assert "add_hover_tip(self.stop_btn" in gui_text
    assert "add_hover_tip(self.icon_frame_combo" in gui_text
    assert "add_hover_tip(self.tv_gamepad_source_combo" in gui_text
    assert "Black = black bars/space" in gui_text


def test_v427_dialogs_and_help_include_hover_guidance():
    gui_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    help_text = (Path(__file__).resolve().parents[1] / "vc_extractor" / "help_docs.py").read_text(encoding="utf-8")
    assert "Horizontal focus chooses" in gui_text
    assert "Reset to Console Default" in gui_text and "add_hover_tip(reset_btn" in gui_text
    assert "game_name_tip =" in gui_text
    assert "review_crop_tip =" in gui_text
    assert "HOVER HELP" in help_text
    assert "Rest the pointer" in help_text


def test_v428_uses_uploaded_vector_as_universal_icon():
    root = Path(__file__).resolve().parents[1]
    assert (root / "assets" / "vc_title_extractor.svg").exists()
    assert (root / "assets" / "vc_title_extractor.png").exists()
    assert (root / "assets" / "vc_title_extractor.ico").exists()
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "self.iconphoto(True, self._app_icon_photo, self._app_icon_source)" in gui_text
    assert "def apply_child_window_icon(window):" in gui_text
    assert "SetCurrentProcessExplicitAppUserModelID" in gui_text


def test_v428_packaging_includes_all_icon_formats():
    root = Path(__file__).resolve().parents[1]
    build_text = (root / "scripts" / "build_windows.py").read_text(encoding="utf-8")
    assert '"--icon", str(ROOT / "assets/vc_title_extractor.ico")' in build_text
    assert '"--add-data", str(ROOT / "assets") + ";assets"' in build_text
    for extension in ("ico", "png", "svg"):
        assert (root / "assets" / ("vc_title_extractor." + extension)).is_file()


def test_v432_forces_native_windows_window_icons_after_handle_creation():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "def _asset_path(filename: str)" in gui_text
    assert 'getattr(sys, "_MEIPASS"' in gui_text
    assert "def _apply_windows_native_icon(window)" in gui_text
    assert "LoadImageW" in gui_text
    assert "SendMessageW" in gui_text
    assert "wm_seticon = 0x0080" in gui_text
    assert "self._app_icon_source.subsample(32, 32)" in gui_text


def test_v433_help_menu_ampersands_and_document_styling():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'label="Artwork & Framing"' in gui_text
    assert 'label="Game Details & Banner Text"' in gui_text
    assert 'label="Supported Systems & Files"' in gui_text
    assert 'label="Credits & Notices"' in gui_text
    assert " && " not in gui_text
    assert "def _configure_document_tags(self):" in gui_text
    assert "def _render_document_body(self, body: str):" in gui_text
    assert '"section_heading"' in gui_text
    assert '"bullet"' in gui_text
    assert '"numbered"' in gui_text
    assert '"definition_label"' in gui_text


def test_v434_capture_checkboxes_and_single_selection_priority():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'capture_checked: bool = True' in gui_text
    assert '"☑" if job.capture_checked else "☐"' in gui_text
    assert 'headings = {"capture": "Capture"' in gui_text
    assert "def _tree_capture_checkbox_click(self, event):" in gui_text
    assert "def check_selected_for_capture(self):" in gui_text
    assert "def check_all_for_capture(self):" in gui_text
    assert "def clear_capture_checks(self):" in gui_text

    class FakeJob:
        def __init__(self, checked):
            self.source_kind = "ROM"
            self.capture_checked = checked

    app = App.__new__(App)
    app.jobs = [FakeJob(True), FakeJob(False), FakeJob(True)]
    app.selected_job_indices = lambda: [1]
    assert app._capture_target_indices() == [1]
    app.selected_job_indices = lambda: []
    assert app._capture_target_indices() == [0, 2]
    app.selected_job_indices = lambda: [0, 1]
    assert app._capture_target_indices() == [0, 2]
    for job in app.jobs:
        job.capture_checked = False
    assert app._capture_target_indices() == [0, 1]


def test_v435_hover_help_auto_dismisses_and_waits_for_reentry():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "self._hide_after_id" in gui_text
    assert "self._dismissed_until_leave" in gui_text
    assert 'widget.bind("<Motion>", self._on_motion' in gui_text
    assert 'widget.bind("<MouseWheel>", self._dismiss' in gui_text
    assert 'widget.bind("<KeyPress>", self._dismiss' in gui_text
    assert 'top.bind("<FocusOut>", lambda event: HoverTip.dismiss_active()' in gui_text
    assert "self._hide_after_id = self.widget.after(self.display_ms, self._dismiss)" in gui_text
    assert "abs(current[0] - self._show_point[0]) >= 8" in gui_text
    assert "self._dismissed_until_leave = True" in gui_text


def test_v436_activity_panel_is_resizable_closable_and_detachable():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'self.activity_paned = self._make_grab_paned(shell, tk.VERTICAL)' in gui_text
    assert 'text="Detach", command=self.detach_activity_log' in gui_text
    assert 'text="Reattach", command=self.reattach_activity_log' in gui_text
    assert 'text="Close", command=self.close_activity_log' in gui_text
    assert "def show_activity_log(self):" in gui_text
    assert "def close_activity_log(self):" in gui_text
    assert "def detach_activity_log(self):" in gui_text
    assert "def reattach_activity_log(self):" in gui_text
    assert 'view_menu.add_command(label="Show Activity Log"' in gui_text


def test_v436_activity_file_logging_writes_each_line_immediately(tmp_path: Path):
    import threading

    class Flag:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    app = App.__new__(App)
    app.activity_logging_enabled_var = Flag(True)
    app.activity_log_dir = tmp_path
    app._activity_log_lock = threading.Lock()
    app._session_log_path = None
    assert app._write_activity_log_line("First live line") is None
    assert app._write_activity_log_line("Second live line") is None
    files = list(tmp_path.glob("*.log"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Wii U VC Image Studio v5.06.0 Activity Log" in text
    assert "First live line" in text
    assert "Second live line" in text


def test_v436_logging_settings_and_main_configuration_selector():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'label="Activity Logging..."' in gui_text
    assert "class ActivityLoggingSettingsDialog(tk.Toplevel):" in gui_text
    assert 'text="Enable file logging"' in gui_text
    assert 'text="Log output directory:"' in gui_text
    assert 'text="Browse..."' in gui_text
    assert 'text="Open Folder"' in gui_text
    assert 'text="Save"' in gui_text
    assert 'text="Cancel"' in gui_text
    assert '"activity_log_dir": str(self.activity_log_dir)' in gui_text
    assert '"activity_logging_enabled": bool(self.activity_logging_enabled_var.get())' in gui_text
    assert 'settings_menu.add_command(label="Open Configuration Folder"' in gui_text
    assert 'settings_menu.add_command(label="Change Configuration Folder"' in gui_text
    assert "self.config_dir_var" not in gui_text
    assert 'text="Configuration folder"' not in gui_text


def test_v437_main_window_autosizes_only_at_startup():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "self._initial_autosize_complete = False" in gui_text
    assert "if self._initial_autosize_complete:" in gui_text
    assert "self._initial_autosize_complete = True" in gui_text
    assert gui_text.count("self.after_idle(self._autosize_to_content)") == 1
    update_start = gui_text.index("    def _update_preview(self):")
    update_end = gui_text.index("    def review_selected(self):", update_start)
    assert "_autosize_to_content" not in gui_text[update_start:update_end]


def test_v437_main_areas_use_visible_resize_grab_bars():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "def _make_grab_paned(self, parent, orient):" in gui_text
    assert "showhandle=True" in gui_text
    assert "sashwidth=8" in gui_text
    assert "self.workspace_paned = self._make_grab_paned(main, tk.VERTICAL)" in gui_text
    assert "self.details_paned = self._make_grab_paned(self.workspace_paned, tk.HORIZONTAL)" in gui_text
    assert "self.activity_paned = self._make_grab_paned(shell, tk.VERTICAL)" in gui_text


def test_v437_rom_list_and_controls_respond_to_available_space():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'tree_y_scroll = ttk.Scrollbar(rom_table, orient="vertical"' in gui_text
    assert 'tree_x_scroll = ttk.Scrollbar(rom_table, orient="horizontal"' in gui_text
    assert "xscrollcommand=tree_x_scroll.set" in gui_text
    assert 'uniform="main_toolbar"' in gui_text
    assert "def _bind_responsive_wrap(self, label, container" in gui_text
    assert "parent.columnconfigure(0, weight=1, minsize=150)" in gui_text
    assert "parent.rowconfigure(0, weight=1, minsize=80)" in gui_text


def test_v438_required_controls_remain_reachable_during_resizing():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    docs_text = (root / "vc_extractor" / "help_docs.py").read_text(encoding="utf-8")
    assert "self.minsize(1000, 840)" in gui_text
    assert "self.activity_paned.add(main, minsize=690" in gui_text
    assert "self.workspace_paned.add(self.rom_list_panel, minsize=205" in gui_text
    assert "self.workspace_paned.add(self.details_paned, minsize=210" in gui_text
    assert "self.rom_list_panel.rowconfigure(0, weight=1, minsize=80)" in gui_text
    assert 'rom_table.grid(row=0, column=0, sticky="nsew")' in gui_text
    assert 'capture_choice_bar.grid(row=1, column=0, sticky="ew"' in gui_text
    assert 'uniform="capture_actions"' in gui_text
    assert "toolbar_column_count = 4" in gui_text
    assert "self.capture_settings_canvas = tk.Canvas(" in gui_text
    assert "yscrollcommand=capture_scrollbar.set" in gui_text
    assert "def _scroll_capture_settings(self, event):" in gui_text
    assert 'self.bind_all("<MouseWheel>", self._scroll_capture_settings' in gui_text
    assert "Capture Settings can be scrolled" in docs_text


def test_v438_font_selection_is_settings_only_and_workspace_copy_is_concise():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'settings_menu.add_cascade(label="Artwork Font"' in gui_text
    assert "def use_default_artwork_font(self):" in gui_text
    assert "def show_active_artwork_font(self):" in gui_text
    assert 'self._path_row(paths, 3, "Custom font"' not in gui_text
    assert "icon_frame_description" not in gui_text
    assert "tv_source_description" not in gui_text
    assert "output_description" not in gui_text
    assert 'text="Enable metadata overlay"' in gui_text


def test_v439_capture_settings_has_compact_descriptive_text():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "capture_settings_summary = ttk.Label(" in gui_text
    assert "Configure capture timing, screen crop, output images, icon framing, artwork source, and boot sound." in gui_text
    assert 'font=("Segoe UI", 9)' in gui_text
    assert 'capture_settings_summary.pack(fill="x", pady=(0, 6))' in gui_text
    assert "self._bind_responsive_wrap(capture_settings_summary, left" in gui_text


def test_v440_file_menu_exposes_relevant_existing_actions():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "file_menu = tk.Menu(menubar, tearoff=False)" in gui_text
    assert 'menubar.add_cascade(label="File", menu=file_menu)' in gui_text
    assert 'label="Add ROMs...", accelerator="Ctrl+O"' in gui_text
    assert 'label="Add ROM Folder...", accelerator="Ctrl+Shift+O"' in gui_text
    assert 'label="Import Screenshot for Selected Game..."' in gui_text
    assert 'label="Import Custom TV/Gamepad Image..."' in gui_text
    assert 'label="Open Selected Output Folder"' in gui_text
    assert 'label="Open Selected Metadata"' in gui_text
    assert 'file_menu.add_command(label="Exit", command=self._on_close)' in gui_text
    assert 'self.bind_all("<Control-o>", self._shortcut_add_roms)' in gui_text
    assert 'self.bind_all("<Control-Shift-O>", self._shortcut_add_rom_folder)' in gui_text


def test_v440_logging_dialog_applies_changes_only_on_save():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    dialog_start = gui_text.index("class ActivityLoggingSettingsDialog(tk.Toplevel):")
    dialog_end = gui_text.index("class ImageCropPositionDialog", dialog_start)
    dialog_text = gui_text[dialog_start:dialog_end]
    assert 'self.title(f"{APP_NAME} - Activity Logging Settings")' in dialog_text
    assert "self.enabled_var = tk.BooleanVar(" in dialog_text
    assert "self.directory_var = tk.StringVar(" in dialog_text
    assert "def _browse_directory(self):" in dialog_text
    assert "def _open_directory(self):" in dialog_text
    assert "def _save(self):" in dialog_text
    assert "parent.activity_log_dir = directory" in dialog_text
    assert "parent.activity_logging_enabled_var.set(enabled and directory is not None)" in dialog_text
    assert "parent._save_ui_settings()" in dialog_text
    assert "def _close(self):" in dialog_text


def test_v440_logging_dialog_save_updates_live_logging_state(tmp_path: Path):
    from vc_extractor.gui import ActivityLoggingSettingsDialog

    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Parent:
        def __init__(self):
            self.activity_log_dir = None
            self.activity_logging_enabled_var = Value(False)
            self._activity_file_error_reported = True
            self._session_log_path = tmp_path / "old.log"
            self.saved = 0
            self.status_updates = 0
            self.messages = []

        def _save_ui_settings(self):
            self.saved += 1

        def _update_activity_logging_status(self):
            self.status_updates += 1

        def log(self, message):
            self.messages.append(message)

    parent = Parent()
    dialog = ActivityLoggingSettingsDialog.__new__(ActivityLoggingSettingsDialog)
    dialog.parent_app = parent
    dialog.enabled_var = Value(True)
    dialog.directory_var = Value(str(tmp_path / "logs"))
    dialog.closed = False
    dialog._close = lambda: setattr(dialog, "closed", True)
    dialog._save()

    assert parent.activity_log_dir == (tmp_path / "logs").resolve()
    assert parent.activity_logging_enabled_var.get() is True
    assert parent._activity_file_error_reported is False
    assert parent._session_log_path is None
    assert parent.saved == 1
    assert parent.status_updates == 1
    assert parent.messages == [f"Live activity file logging enabled: {(tmp_path / 'logs').resolve()}"]
    assert dialog.closed is True


def test_v441_each_rom_row_has_an_inline_remove_control():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert 'columns = ("capture", "remove", "file"' in gui_text
    assert '"remove": "Remove"' in gui_text
    assert '"remove": 62' in gui_text
    assert '"✕",' in gui_text
    assert 'self.tree.bind("<Button-1>", self._tree_remove_click, add="+")' in gui_text
    assert "def _tree_remove_click(self, event):" in gui_text
    assert 'self.tree.identify_column(event.x) != "#2"' in gui_text
    assert "def _remove_job_indices(self, indices) -> bool:" in gui_text
    assert '"The source ROM was not deleted."' in gui_text
    assert "def _tree_double_click(self, event):" in gui_text


def test_v441_inline_remove_preserves_other_selection_and_source_file(tmp_path: Path):
    from types import SimpleNamespace
    from vc_extractor.gui import GameJob

    class FakeTree:
        def __init__(self):
            self.selected = {"1"}

        def selection(self):
            return tuple(sorted(self.selected))

        def selection_remove(self, *items):
            self.selected.difference_update(items)

        def selection_add(self, item):
            self.selected.add(str(item))

        def selection_set(self, item):
            self.selected = {str(item)}

    paths = [tmp_path / f"game{i}.nes" for i in range(3)]
    for path in paths:
        path.write_bytes(b"ROM")
    jobs = [
        GameJob(info=SimpleNamespace(path=path, display_name=f"Game {i}"))
        for i, path in enumerate(paths)
    ]
    expected_jobs = jobs[1:]
    app = App.__new__(App)
    app.jobs = jobs
    app.tree = FakeTree()
    app.worker = None
    app.metadata_worker = None
    app._refresh_tree = lambda: None
    app._on_tree_selection_changed = lambda: None
    messages = []
    app.log = messages.append

    assert app._remove_job_indices([0]) is True
    assert app.jobs == expected_jobs
    assert app.tree.selected == {"0"}
    assert paths[0].exists()
    assert messages == ["Removed from ROM list: Game 0. The source ROM was not deleted."]


def test_v443_remove_checked_replaces_row_highlight_actions():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert '("Remove", self.remove_selected' not in gui_text
    assert "rom_selection_bar = ttk.Frame(self.rom_list_panel)" not in gui_text
    assert 'text="Select All", command=self.select_all_rows' not in gui_text
    assert 'text="Select None", command=self.select_no_rows' not in gui_text
    assert 'text="Remove Selected"' not in gui_text
    assert 'text="Remove Checked"' in gui_text
    assert 'command=self.remove_checked' in gui_text
    assert 'state="disabled"' in gui_text
    assert "def _update_checked_action_state(self):" in gui_text
    assert "def select_all_rows(self):" not in gui_text
    assert "def select_no_rows(self):" not in gui_text
    assert 'self.tree.bind("<Control-a>"' not in gui_text
    assert 'self.tree.bind("<Escape>"' not in gui_text
    assert 'self.tree.bind("<Delete>", self._tree_remove_checked_shortcut)' in gui_text
    assert "Remove every checked ROM from the application list." in gui_text


def test_v443_remove_checked_ignores_highlighted_unchecked_rows():
    class FakeTree:
        def __init__(self):
            self.selected = {"0", "1"}

        def selection(self):
            return tuple(sorted(self.selected))

        def selection_remove(self, *items):
            self.selected.difference_update(str(item) for item in items)

        def selection_add(self, item):
            self.selected.add(str(item))

        def selection_set(self, item):
            self.selected = {str(item)}

    class FakeButton:
        def __init__(self):
            self.state = None

        def config(self, **kwargs):
            self.state = kwargs.get("state", self.state)

    class Job:
        def __init__(self, name, checked, source_kind="ROM"):
            self.name = name
            self.capture_checked = checked
            self.source_kind = source_kind

    app = App.__new__(App)
    checked_job = Job("checked", True)
    highlighted_unchecked_job = Job("highlighted", False)
    image_job = Job("image", True, source_kind="Image")
    app.jobs = [checked_job, highlighted_unchecked_job, image_job]
    app.tree = FakeTree()
    app.remove_checked_btn = FakeButton()
    removed_indices = []
    app._remove_job_indices = lambda indices: removed_indices.extend(indices)

    app._update_checked_action_state()
    assert app.remove_checked_btn.state == "normal"
    app.remove_checked()
    assert removed_indices == [0]

    checked_job.capture_checked = False
    app._update_checked_action_state()
    assert app.remove_checked_btn.state == "disabled"


def test_v444_selected_capture_control_is_a_state_aware_toggle():
    class FakeTree:
        def __init__(self):
            self.selected = {"0"}

        def selection(self):
            return tuple(sorted(self.selected))

    class FakeButton:
        def __init__(self):
            self.text = None
            self.state = None

        def config(self, **kwargs):
            self.text = kwargs.get("text", self.text)
            self.state = kwargs.get("state", self.state)

    class Job:
        def __init__(self, checked, source_kind="ROM"):
            self.capture_checked = checked
            self.source_kind = source_kind

    app = App.__new__(App)
    app.jobs = [Job(False), Job(True), Job(True, source_kind="Image")]
    app.tree = FakeTree()
    app.check_selected_capture_btn = FakeButton()
    app.remove_checked_btn = FakeButton()
    app._refresh_tree = app._update_checked_action_state

    app._update_checked_action_state()
    assert app.check_selected_capture_btn.text == "Check Selected"
    assert app.check_selected_capture_btn.state == "normal"

    app.check_selected_for_capture()
    assert app.jobs[0].capture_checked is True
    assert app.check_selected_capture_btn.text == "Uncheck Selected"

    app.check_selected_for_capture()
    assert app.jobs[0].capture_checked is False
    assert app.check_selected_capture_btn.text == "Check Selected"

    app.tree.selected = {"0", "1"}
    app._update_checked_action_state()
    assert app.check_selected_capture_btn.text == "Check Selected"
    app.check_selected_for_capture()
    assert [app.jobs[0].capture_checked, app.jobs[1].capture_checked] == [True, True]
    assert app.check_selected_capture_btn.text == "Uncheck Selected"

    app.check_selected_for_capture()
    assert [app.jobs[0].capture_checked, app.jobs[1].capture_checked] == [False, False]
    assert app.check_selected_capture_btn.text == "Check Selected"

    app.tree.selected = set()
    app._update_checked_action_state()
    assert app.check_selected_capture_btn.state == "disabled"


def test_v444_selected_capture_toggle_is_wired_to_dynamic_button():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    assert "self.check_selected_capture_btn = ttk.Button(" in gui_text
    assert 'text="Uncheck Selected" if all_selected_checked else "Check Selected"' in gui_text
    assert "new_checked_state = not all(" in gui_text
    assert "state=\"disabled\"," in gui_text


def test_v500_webui_drag_and_drop_runtime_is_integrated_into_source_and_build():
    root = Path(__file__).resolve().parents[1]
    webui_text = (root / "vc_extractor" / "webui.py").read_text(encoding="utf-8")
    frontend_text = (root / "vc_extractor" / "webui_assets" / "app.js").read_text(encoding="utf-8")
    requirements_text = (root / "requirements.txt").read_text(encoding="utf-8")
    build_text = (root / "scripts" / "build_windows.py").read_text(encoding="utf-8")

    assert "from webview.dom import DOMEventHandler" in webui_text
    assert "pywebviewFullPath" in webui_text
    assert "add_dropped_paths" in frontend_text
    assert "pywebview==6.2.1" in requirements_text
    assert '"--collect-all", "webview"' in build_text
    assert '"vc_extractor/webui_assets") + ";vc_extractor/webui_assets"' in build_text


def test_v445_dropped_folders_are_scanned_and_duplicate_paths_are_removed(tmp_path: Path):
    direct_rom = tmp_path / "Direct Game.nds"
    direct_rom.write_bytes(b"nds")
    folder = tmp_path / "ROM Folder"
    nested = folder / "Nested"
    nested.mkdir(parents=True)
    nested_rom = nested / "Nested Game.gba"
    nested_rom.write_bytes(b"gba")
    (folder / "notes.txt").write_text("not a ROM", encoding="utf-8")
    unsupported = tmp_path / "readme.txt"
    unsupported.write_text("not a ROM", encoding="utf-8")

    paths, ignored = App._collect_dropped_rom_paths(
        [direct_rom, folder, direct_rom, unsupported, tmp_path / "missing.nds"]
    )

    assert {path.resolve() for path in paths} == {direct_rom.resolve(), nested_rom.resolve()}
    assert ignored == 2


def test_v445_windows_drop_list_preserves_paths_with_spaces():
    class FakeTk:
        @staticmethod
        def splitlist(data):
            assert data == "drop-data"
            return ("C:/Games/One Game.nds", "D:/ROM Library")

    app = App.__new__(App)
    app.tk = FakeTk()
    paths = app._split_drop_paths("drop-data")

    assert [str(path) for path in paths] == ["C:/Games/One Game.nds", "D:/ROM Library"]


def test_v446_repository_artwork_uses_exact_code_and_prefers_uwuvci(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    requested_directories = []

    def fake_json(url):
        requested_directories.append(url)
        return {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "gcn/G07E69/iconTex.png"},
                {"type": "blob", "path": "gcn/G07E69/bootTvTex.png"},
            ],
        }

    def fake_bytes(url):
        image = Image.new("RGB", (320, 180), "navy" if url.endswith("tv") else "gold")
        data = BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_json", fake_json)
    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    result = artwork.find_and_download_artwork("GameCube", "G07E69", tmp_path / "cache")

    assert result is not None
    assert result.provider_name == "UWUVCI Community Images"
    assert result.product_code == "G07E69"
    assert result.icon_path and result.icon_path.exists()
    assert result.tv_path and result.tv_path.exists()
    assert len(requested_directories) == 1
    assert "UWUVCI-PRIME/UWUVCI-IMAGES/git/trees/master" in requested_directories[0]


def test_v446_repository_artwork_falls_back_to_cucholix(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    requested_directories = []
    downloaded_urls = []

    def fake_json(url):
        requested_directories.append(url)
        if "UWUVCI-PRIME" in url:
            return {"truncated": False, "tree": []}
        return {
            "truncated": False,
            "tree": [{"type": "blob", "path": "gcn/image/G07E69/bootTvTex.tga"}],
        }

    def fake_bytes(url):
        downloaded_urls.append(url)
        image = Image.new("RGB", (640, 360), "green")
        data = BytesIO()
        image.save(data, format="TGA")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_json", fake_json)
    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    result = artwork.find_and_download_artwork("GameCube", "G07E69", tmp_path / "cache")

    assert result is not None
    assert result.provider_name == "Cucholix WiiVC BIS"
    assert any("cucholix/wiivc-bis/git/trees/master" in url for url in requested_directories)
    assert any("cucholix/wiivc-bis/master/gcn/image/G07E69/bootTvTex.tga" in url for url in downloaded_urls)


def test_v447_repository_product_code_candidates_remain_conservative():
    from vc_extractor.repository_art import product_code_candidates

    assert product_code_candidates("Super Mario World") == []
    assert product_code_candidates("AGB-A2CE-USA") == ["A2CE"]
    assert product_code_candidates("G07E69") == ["G07E69"]


def test_v446_repository_artwork_writes_standard_output_sizes(tmp_path: Path):
    from vc_extractor.repository_art import RepositoryArtwork, apply_repository_artwork

    icon = tmp_path / "icon.png"
    tv = tmp_path / "tv.png"
    Image.new("RGBA", (256, 256), (20, 40, 80, 255)).save(icon)
    Image.new("RGB", (1920, 1080), "purple").save(tv)
    result = RepositoryArtwork(
        provider_name="Test Repository",
        provider_url="https://example.invalid/repository",
        product_code="ABCE",
        system="Game Boy Advance",
        icon_path=icon,
        tv_path=tv,
    )
    out = tmp_path / "out"
    written = apply_repository_artwork(result, out)

    assert len(written) == 3
    assert Image.open(out / "Icon Image.png").size == (128, 128)
    assert Image.open(out / "TV Image.png").size == (1280, 720)
    assert Image.open(out / "Gamepad Image.png").size == (854, 480)


def test_v447_gui_exposes_multi_source_controls():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    docs_text = (root / "vc_extractor" / "help_docs.py").read_text(encoding="utf-8")
    assert 'label="Repository Artwork"' in gui_text
    assert 'label="Automatically Search Artwork and Boot-Sound Sources"' in gui_text
    assert 'label="Search Artwork Sources for Selected Game..."' in gui_text
    assert 'label="Allow Title and Metadata Fallbacks"' in gui_text
    assert 'label="Search Libretro Thumbnails"' in gui_text
    assert 'label="Search GameCube Artwork Collection"' in gui_text
    assert 'label="Use Generated Artwork for Selected Game"' in gui_text
    assert 'label="Open Download Cache"' in gui_text
    assert '"automatic_repository_artwork": bool(self.auto_repository_artwork_var.get())' in gui_text
    assert '"repository_title_fallback": bool(self.repository_title_fallback_var.get())' in gui_text
    assert '"libretro_artwork_enabled": bool(self.libretro_artwork_var.get())' in gui_text
    assert '"Repository Artwork"' in docs_text
    assert "Libretro Thumbnails" in docs_text
    assert "lower-confidence but decodable images are retained" in docs_text


def test_v447_libretro_thumbnails_match_title_region_and_prefer_title_screen(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    requested = []

    def fake_json(url):
        requested.append(url)
        return {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "Named_Boxarts/Super Mario World (USA).png"},
                {"type": "blob", "path": "Named_Snaps/Super Mario World (USA).png"},
                {"type": "blob", "path": "Named_Titles/Super Mario World (USA).png"},
                {"type": "blob", "path": "Named_Titles/Super Mario World (Japan).png"},
            ],
        }

    def fake_bytes(url):
        image = Image.new("RGB", (320, 240), "royalblue")
        data = BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_json", fake_json)
    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    result = artwork.find_and_download_artwork(
        "SNES",
        "",
        tmp_path / "cache",
        title_candidates=["Super Mario World"],
        region="USA",
    )

    assert result is not None
    assert result.provider_name == "Libretro Thumbnails"
    assert result.source_image_path and result.source_image_path.exists()
    assert result.source_image_kind == "Title screen"
    assert result.matched_title == "Super Mario World (USA)"
    assert result.match_method == "Exact normalized title and region"
    assert result.available_outputs == ("Icon", "TV", "Gamepad")
    assert any("libretro-thumbnails/Nintendo_-_Super_Nintendo_Entertainment_System" in url for url in requested)


def test_v447_libretro_thumbnails_falls_back_to_gameplay_snap(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    monkeypatch.setattr(
        artwork,
        "_request_json",
        lambda url: {
            "truncated": False,
            "tree": [{"type": "blob", "path": "Named_Snaps/Sonic the Hedgehog (USA).png"}],
        },
    )

    def fake_bytes(url):
        data = BytesIO()
        Image.new("RGB", (320, 224), "blue").save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    result = artwork.find_and_download_artwork(
        "Genesis / Mega Drive",
        "",
        tmp_path / "cache",
        title_candidates=["Sonic the Hedgehog"],
        region="USA",
    )

    assert result is not None
    assert result.source_image_kind == "Gameplay screenshot"


def test_v447_title_matching_accepts_clear_typo_and_rejects_ambiguity():
    from vc_extractor.repository_art import _select_title_match, normalized_title_key

    clear = [
        {"name": "Super Mario World (USA).png", "match_title": "Super Mario World (USA)", "art_kind": "Named_Titles", "path": "a"},
        {"name": "F-Zero (USA).png", "match_title": "F-Zero (USA)", "art_kind": "Named_Titles", "path": "b"},
    ]
    ambiguous = [
        {"name": "Mega Man 2 (USA).png", "match_title": "Mega Man 2 (USA)", "art_kind": "Named_Titles", "path": "a"},
        {"name": "Mega Man 3 (USA).png", "match_title": "Mega Man 3 (USA)", "art_kind": "Named_Titles", "path": "b"},
    ]

    assert _select_title_match(clear, ["Super Mario Worl"], "USA") is not None
    assert _select_title_match(ambiguous, ["Mega Man"], "USA") is None
    assert normalized_title_key("Dr. Mario") == "dr mario"
    assert normalized_title_key("Dr. Mario (USA).png") == "dr mario"


def test_v447_gamecube_title_collection_precedes_libretro(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    requested = []

    def fake_json(url):
        requested.append(url)
        assert "reddragon105/gamecube-inject-artwork" in url
        return {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "artwork/F-Zero GX Banner.jpg"},
                {"type": "blob", "path": "artwork/F-Zero GX Icon.jpg"},
            ],
        }

    def fake_bytes(url):
        data = BytesIO()
        Image.new("RGB", (640, 360), "orange").save(data, format="JPEG")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_json", fake_json)
    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    result = artwork.find_and_download_artwork(
        "GameCube",
        "",
        tmp_path / "cache",
        title_candidates=["F-Zero GX"],
        region="USA",
    )

    assert result is not None
    assert result.provider_name == "GameCube Inject Artwork"
    assert result.icon_path and result.icon_path.exists()
    assert result.tv_path and result.tv_path.exists()
    assert len(requested) == 1


def test_v447_libretro_mapping_covers_every_supported_console():
    from vc_extractor.repository_art import LIBRETRO_THUMBNAIL_REPOSITORIES

    supported = {
        "Nintendo DS", "Game Boy Advance", "Game Boy Color", "Game Boy", "NES", "SNES",
        "Nintendo 64", "GameCube", "Wii", "WiiWare", "MSX",
        "TurboGrafx-CD / PC Engine CD", "Genesis / Mega Drive", "Master System", "Game Gear",
        "TurboGrafx-16 / PC Engine",
    }
    assert supported <= set(LIBRETRO_THUMBNAIL_REPOSITORIES)


def test_v447_libretro_source_is_rendered_and_original_capture_is_preserved(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    capture = tmp_path / "capture.png"
    source = tmp_path / "libretro.png"
    Image.new("RGB", (320, 240), "red").save(capture)
    Image.new("RGB", (320, 240), "blue").save(source)
    info = RomInfo(tmp_path / "game.sfc", "SNES", "Test Game", region="USA")
    original_sample = FrameSample(capture, 1.0)
    job = GameJob(
        info=info,
        selected=original_sample,
        candidates=[original_sample],
        repository_artwork=RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url="https://github.com/libretro-thumbnails/test",
            product_code="",
            system="SNES",
            source_image_path=source,
            source_image_kind="Title screen",
            matched_title="Test Game (USA)",
            match_method="Exact normalized title and region",
        ),
    )
    app = App.__new__(App)
    options = ExportOptions(tmp_path / "out", screen_crop="auto", include_banner_metadata=False)

    output = App._export_job(app, job, options=options)
    with Image.open(output / "title_screen.png") as rendered:
        assert rendered.getpixel((10, 10)) == (0, 0, 255)
    assert job.selected is original_sample
    assert (output / "Icon Image.png").exists()
    assert "Libretro Thumbnails" in (output / "metadata.txt").read_text(encoding="utf-8")

    job.repository_artwork = None
    output = App._export_job(app, job, options=options)
    with Image.open(output / "title_screen.png") as restored:
        assert restored.getpixel((10, 10)) == (255, 0, 0)


def test_v448_nds_downloaded_source_screen_choice_applies_to_every_output(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    source = tmp_path / "nds-both-screens.png"
    combined = Image.new("RGB", (256, 384), "black")
    combined.paste(Image.new("RGB", (256, 192), "red"), (0, 0))
    combined.paste(Image.new("RGB", (256, 192), "blue"), (0, 192))
    combined.save(source)

    info = RomInfo(tmp_path / "game.nds", "Nintendo DS", "Test DS Game", region="USA")
    job = GameJob(
        info=info,
        repository_artwork=RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url="https://github.com/libretro-thumbnails/test",
            product_code="",
            system="Nintendo DS",
            source_image_path=source,
            source_image_kind="Title screen",
            matched_title="Test DS Game (USA)",
            match_method="Exact normalized title and region",
        ),
    )
    app = App.__new__(App)
    options = ExportOptions(tmp_path / "out", include_banner_metadata=False)

    info.source_area_selection = "top"
    output = App._export_job(app, job, options=options)
    with Image.open(output / "title_screen.png") as title:
        assert title.size == (256, 192)
        assert title.getpixel((10, 10)) == (255, 0, 0)
    top_icon = (output / "Icon Image.png").read_bytes()
    top_tv = (output / "TV Image.png").read_bytes()
    top_gamepad = (output / "Gamepad Image.png").read_bytes()

    info.source_area_selection = "bottom"
    output = App._export_job(app, job, options=options)
    with Image.open(output / "title_screen.png") as title:
        assert title.size == (256, 192)
        assert title.getpixel((10, 10)) == (0, 0, 255)
    assert (output / "Icon Image.png").read_bytes() != top_icon
    assert (output / "TV Image.png").read_bytes() != top_tv
    assert (output / "Gamepad Image.png").read_bytes() != top_gamepad
    assert "Selected Source Area: Bottom" in (output / "metadata.txt").read_text(encoding="utf-8")


def test_v448_framing_change_rebuilds_downloaded_source_immediately(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    source = tmp_path / "source.png"
    image = Image.new("RGB", (640, 360), "navy")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 210, 359), fill="red")
    draw.rectangle((430, 0, 639, 359), fill="lime")
    image.save(source)

    info = RomInfo(tmp_path / "game.sfc", "SNES", "Live Framing Test")
    job = GameJob(
        info=info,
        repository_artwork=RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url="https://github.com/libretro-thumbnails/test",
            product_code="",
            system="SNES",
            source_image_path=source,
        ),
    )
    app = App.__new__(App)
    app._export_options = lambda screen_crop=None: ExportOptions(
        tmp_path / "out",
        screen_crop=screen_crop or "auto",
        include_banner_metadata=False,
    )
    app._update_preview = lambda: None

    job.output_dir = App._export_job(app, job)
    original_tv = (job.output_dir / "TV Image.png").read_bytes()
    values = App._crop_state_for_info(app, info)
    values["tv_zoom"] = 150
    values["tv_x"] = 100
    App._set_crop_focus_values(app, job, values, rebuild=True)

    assert info.tv_zoom == 150
    assert info.tv_focus_x == 100
    assert (job.output_dir / "TV Image.png").read_bytes() != original_tv


def test_v448_metadata_overlay_toggle_rebuilds_downloaded_source_immediately(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    class Value:
        def __init__(self, current):
            self.current = current

        def get(self):
            return self.current

        def set(self, current):
            self.current = current

    class Tree:
        def selection_set(self, _value):
            pass

    source = tmp_path / "source.png"
    Image.new("RGB", (640, 360), "purple").save(source)
    info = RomInfo(
        tmp_path / "game.sfc",
        "SNES",
        "Metadata Overlay Test",
        release_year="1994",
        players="1-2",
    )
    job = GameJob(
        info=info,
        repository_artwork=RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url="https://github.com/libretro-thumbnails/test",
            product_code="",
            system="SNES",
            source_image_path=source,
        ),
    )
    app = App.__new__(App)
    app.jobs = [job]
    app.include_banner_metadata_var = Value(False)
    app.include_game_title_var = Value(True)
    app.include_release_date_var = Value(True)
    app.include_player_count_var = Value(True)
    app._export_options = lambda screen_crop=None: ExportOptions(
        tmp_path / "out",
        screen_crop=screen_crop or "auto",
        include_banner_metadata=app.include_banner_metadata_var.get(),
        include_game_title=True,
        include_release_date=True,
        include_player_count=True,
    )
    app._save_ui_settings = lambda: None
    app.selected_job_indices = lambda: [0]
    app._refresh_tree = lambda: None
    app._update_preview = lambda: None
    app.log = lambda _message: None
    app.tree = Tree()

    job.output_dir = App._export_job(app, job)
    without_overlay = (job.output_dir / "TV Image.png").read_bytes()
    app.include_banner_metadata_var.set(True)
    App._apply_banner_metadata_setting(app)

    assert (job.output_dir / "TV Image.png").read_bytes() != without_overlay
    assert "banner text updated" in job.status


def test_v448_capture_replaces_downloaded_source_and_keeps_cache_preference(tmp_path: Path, monkeypatch):
    import queue

    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    source = tmp_path / "downloaded.png"
    capture = tmp_path / "capture.png"
    Image.new("RGB", (320, 240), "blue").save(source)
    Image.new("RGB", (320, 240), "red").save(capture)
    sample = FrameSample(capture, 5.0, score=1.0)
    info = RomInfo(tmp_path / "game.sfc", "SNES", "Capture Replacement Test")
    job = GameJob(
        info=info,
        repository_artwork=RepositoryArtwork(
            provider_name="Libretro Thumbnails",
            provider_url="https://github.com/libretro-thumbnails/test",
            product_code="",
            system="SNES",
            source_image_path=source,
        ),
    )
    app = App.__new__(App)
    app.events = queue.Queue()
    monkeypatch.setattr("vc_extractor.gui.select_candidates", lambda samples, count: list(samples)[:count])

    output_options = ExportOptions(tmp_path / "out", include_banner_metadata=False)
    App._finalize_captured_samples(app, job, [sample], tmp_path / "session", output_options, 1)

    assert job.repository_artwork is None
    assert job.prefer_user_source is True
    with Image.open(job.output_dir / "title_screen.png") as title:
        assert title.getpixel((10, 10)) == (255, 0, 0)
    queued_logs = [event[1] for event in list(app.events.queue) if event[0] == "log"]
    assert any("replaced downloaded artwork" in message for message in queued_logs)


def test_v448_late_automatic_artwork_result_cannot_replace_user_source(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    capture = tmp_path / "capture.png"
    downloaded = tmp_path / "downloaded.png"
    Image.new("RGB", (320, 240), "red").save(capture)
    Image.new("RGB", (320, 240), "blue").save(downloaded)
    info = RomInfo(tmp_path / "game.sfc", "SNES", "User Source Test")
    job = GameJob(
        info=info,
        selected=FrameSample(capture, 2.0),
        prefer_user_source=True,
    )
    artwork = RepositoryArtwork(
        provider_name="Libretro Thumbnails",
        provider_url="https://github.com/libretro-thumbnails/test",
        product_code="",
        system="SNES",
        source_image_path=downloaded,
    )
    app = App.__new__(App)
    app.jobs = [job]
    messages = []
    app.log = messages.append

    App._accept_repository_artwork(
        app,
        job,
        artwork,
        ExportOptions(tmp_path / "out"),
        automatic=True,
    )

    assert job.repository_artwork is None
    assert job.selected.path == capture
    assert any("not reapplied" in message for message in messages)


def test_v449_generic_source_area_supports_every_direction_for_any_console(tmp_path: Path):
    from vc_extractor.gui import GameJob

    info = RomInfo(tmp_path / "game.sfc", "SNES", "Generic Source Area")
    info.source_area_selection = "left"
    sample = FrameSample(tmp_path / "wide.png", 0.0)
    job = GameJob(info=info, selected=sample, image_imported=True)

    assert App._source_area_choice(job) == "left"
    assert App._screen_crop_for_source(job, sample, "auto") == "left"
    info.source_area_selection = "right"
    assert App._screen_crop_for_source(job, sample, "auto", repository_source=True) == "right"


def test_v449_multi_source_artwork_retains_each_libretro_art_type(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    monkeypatch.setattr(
        artwork,
        "_request_json",
        lambda url: {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "Named_Titles/Test Game (USA).png"},
                {"type": "blob", "path": "Named_Snaps/Test Game (USA).png"},
                {"type": "blob", "path": "Named_Boxarts/Test Game (USA).png"},
            ],
        },
    )

    def fake_bytes(url):
        data = BytesIO()
        Image.new("RGB", (320, 240), "blue").save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(artwork, "_request_bytes", fake_bytes)
    matches = artwork.find_and_download_artwork_candidates(
        "SNES",
        "",
        tmp_path / "cache",
        title_candidates=["Test Game"],
        region="USA",
    )

    assert [match.source_image_kind for match in matches] == [
        "Title screen",
        "Gameplay screenshot",
        "Box art",
    ]
    assert all(match.source_image_path and match.source_image_path.exists() for match in matches)


def test_v449_boot_sound_search_prefers_exact_game_then_console_default(tmp_path: Path, monkeypatch):
    from vc_extractor import repository_art as artwork

    monkeypatch.setattr(
        artwork,
        "_request_json",
        lambda url: {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "nes/sound/ABCE/bootSound.btsnd"},
                {"type": "blob", "path": "nes/sound/bootSound.btsnd"},
            ],
        },
    )
    monkeypatch.setattr(artwork, "_request_bytes", lambda url: b"test boot sound")

    sounds = artwork.find_and_download_boot_sounds("NES", "ABCE", tmp_path / "cache")

    assert [sound.sound_kind for sound in sounds] == ["Per-game", "Console default"]
    assert sounds[0].product_code == "ABCE"
    assert all(sound.path.exists() for sound in sounds)


def test_v450_selected_boot_sound_is_converted_to_standard_wav_and_documented(tmp_path: Path):
    import wave
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import BootSound

    source = tmp_path / "console-default.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22_050)
        handle.writeframes(b"\x00\x00\x10\x00\xf0\xff\x00\x00")
    output = tmp_path / "out"
    output.mkdir()
    (output / "metadata.txt").write_text("Base metadata\n", encoding="utf-8")
    job = GameJob(
        info=RomInfo(tmp_path / "game.nes", "NES", "Boot Sound Test"),
        boot_sound=BootSound(
            provider_name="Test Sound Repository",
            provider_url="https://example.invalid/sounds",
            system="NES",
            path=source,
            label="NES boot",
        ),
    )
    app = App.__new__(App)

    written = App._apply_boot_sound_output(app, job, output)

    assert written == output / "bootSound.wav"
    with wave.open(str(written), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 22_050
        assert handle.getnframes() == 4
    metadata = (output / "metadata.txt").read_text(encoding="utf-8")
    assert "Boot Sound" in metadata
    assert "Output File: bootSound.wav" in metadata
    assert "Automatically converted to WAV" in metadata


def test_v451_manual_boot_sound_preview_replaces_a_previous_console_default(tmp_path: Path):
    import wave

    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import BootSound

    manual_source = tmp_path / "manual.wav"
    with wave.open(str(manual_source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\x34\x12" * 800)

    output = tmp_path / "output"
    output.mkdir()
    (output / "metadata.txt").write_text("metadata\n", encoding="utf-8")
    with wave.open(str(output / "bootSound.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\x78\x56" * 800)

    job = GameJob(
        info=RomInfo(tmp_path / "game.gba", "Game Boy Advance", "Manual Preview Test"),
        output_dir=output,
        boot_sound=BootSound(
            provider_name="Manual file",
            provider_url="",
            system="Game Boy Advance",
            path=manual_source,
            label="manual",
            sound_kind="Manual",
        ),
    )
    app = App.__new__(App)
    app._boot_sound_preview_path = None

    preview = App._prepare_boot_sound_preview(app, job)
    with wave.open(str(preview), "rb") as handle:
        preview_frames = handle.readframes(handle.getnframes())
    with wave.open(str(output / "bootSound.wav"), "rb") as handle:
        output_frames = handle.readframes(handle.getnframes())

    assert preview_frames == b"\x34\x12" * 800
    assert output_frames == preview_frames
    App.stop_boot_sound(app, log_change=False)
    assert not preview.exists()


def test_v451_console_defaults_are_labeled_and_cached_per_system(tmp_path: Path, monkeypatch):
    from vc_extractor import repository_art as artwork

    monkeypatch.setattr(
        artwork,
        "_request_json",
        lambda url: {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "gba/sound/bootSound.btsnd"},
                {"type": "blob", "path": "nes/sound/bootSound.btsnd"},
            ],
        },
    )
    monkeypatch.setattr(artwork, "_request_bytes", lambda url: b"console sound")

    gba = artwork.find_and_download_boot_sounds("Game Boy Advance", "", tmp_path / "cache")
    nes = artwork.find_and_download_boot_sounds("NES", "", tmp_path / "cache")

    assert len(gba) == 1
    assert len(nes) == 1
    assert gba[0].path != nes[0].path
    assert gba[0].label == "Game Boy Advance startup sound"
    assert nes[0].label == "NES startup sound"
    assert gba[0].display_label.startswith("Game Boy Advance console startup:")
    assert nes[0].display_label.startswith("NES console startup:")


def test_v451_local_console_sound_is_available_for_every_supported_system(tmp_path: Path, monkeypatch):
    import wave
    import vc_extractor.gui as gui

    monkeypatch.setattr(gui, "config_dir", lambda: tmp_path / "config")
    destination = App._console_boot_sound_path("Genesis / Mega Drive")
    destination.parent.mkdir(parents=True)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\x00\x00" * 400)

    sound = App._local_console_boot_sound("Genesis / Mega Drive")

    assert sound is not None
    assert sound.path == destination
    assert sound.provider_name == "Local console sound"
    assert sound.sound_kind == "Local console default"
    assert "Genesis / Mega Drive console startup (local)" in sound.display_label


def test_v450_btsnd_is_decoded_as_stereo_48khz_wav(tmp_path: Path):
    import struct
    import wave
    from vc_extractor.boot_sound import convert_boot_sound_to_wav

    source = tmp_path / "bootSound.btsnd"
    pcm_values = (1000, -1000, 2000, -2000)
    source.write_bytes(struct.pack(">Ii", 2, 0) + struct.pack(">4h", *pcm_values))
    destination = tmp_path / "bootSound.wav"

    convert_boot_sound_to_wav(source, destination)

    with wave.open(str(destination), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 48_000
        assert handle.getnframes() == 2
        assert handle.readframes(2) == struct.pack("<4h", *pcm_values)


def test_v450_libretro_keeps_valid_images_when_one_candidate_is_unreadable(tmp_path: Path, monkeypatch):
    from io import BytesIO
    from vc_extractor import repository_art as artwork

    monkeypatch.setattr(
        artwork,
        "_request_json",
        lambda url: {
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "Named_Titles/Test Game (USA).png"},
                {"type": "blob", "path": "Named_Snaps/Test Game (USA).png"},
                {"type": "blob", "path": "Named_Boxarts/Test Game (USA).png"},
            ],
        },
    )

    def valid_png():
        data = BytesIO()
        Image.new("RGB", (320, 240), "green").save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(
        artwork,
        "_request_bytes",
        lambda url: b"not an image" if "Named_Titles" in url else valid_png(),
    )
    monkeypatch.setattr(artwork, "_request_github_file_bytes", lambda *args: b"still not an image")

    matches = artwork.find_and_download_artwork_candidates(
        "SNES",
        "",
        tmp_path / "cache",
        title_candidates=["Test Game"],
        region="USA",
    )

    assert [match.source_image_kind for match in matches] == [
        "Gameplay screenshot",
        "Box art",
    ]


def test_v450_possible_title_match_can_be_retained_for_manual_review():
    from vc_extractor import repository_art as artwork

    entries = [{
        "path": "Named_Titles/Dr Mario Puzzle Challenge.png",
        "name": "Dr Mario Puzzle Challenge.png",
        "match_title": "Dr Mario Puzzle Challenge",
        "art_kind": "Named_Titles",
        "download_url": "https://example.invalid/image.png",
    }]
    match = artwork._select_title_match(
        entries,
        ["Dr Mario Puzzle League"],
        "",
        allow_uncertain=True,
    )

    assert match is not None
    assert match[2] < 0.86
    assert "manual review required" in match[3].lower()


def test_v450_large_preview_review_and_boot_sound_controls_are_exposed():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")

    assert "class LargeImagePreviewWindow" in gui_text
    assert "class RepositoryArtworkReviewWindow" in gui_text
    assert 'label="Review Artwork Matches for Selected Game..."' in gui_text
    assert 'text="Source Image Area"' in gui_text
    assert '("Automatic", "auto")' in gui_text
    assert '("Full Image", "full")' in gui_text
    assert '("Left", "left")' in gui_text
    assert '("Right", "right")' in gui_text
    assert 'text="Boot Sound"' in gui_text
    assert 'text="Search Sources"' in gui_text
    assert 'text="Play Boot Sound"' in gui_text
    assert 'text="Set Console..."' in gui_text
    assert "_prepare_boot_sound_preview" in gui_text
    assert 'text="Review Artwork Matches"' in gui_text
    assert "Review Artwork Matches ({count})" in gui_text
    assert 'label="Include Possible Matches for Manual Review"' in gui_text


def test_v451_repository_artwork_can_use_different_icon_and_banner_sources(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    capture = tmp_path / "capture.png"
    icon_source = tmp_path / "icon-source.png"
    banner_source = tmp_path / "banner-source.png"
    Image.new("RGB", (640, 360), "red").save(capture)
    Image.new("RGB", (640, 360), "lime").save(icon_source)
    Image.new("RGB", (640, 360), "blue").save(banner_source)

    icon_artwork = RepositoryArtwork(
        provider_name="Icon Source",
        provider_url="https://example.invalid/icon",
        product_code="",
        system="SNES",
        source_image_path=icon_source,
        source_image_kind="Title screen",
    )
    banner_artwork = RepositoryArtwork(
        provider_name="Banner Source",
        provider_url="https://example.invalid/banner",
        product_code="",
        system="SNES",
        source_image_path=banner_source,
        source_image_kind="Gameplay screenshot",
    )
    info = RomInfo(tmp_path / "game.sfc", "SNES", "Independent Artwork Test")
    sample = FrameSample(capture, 1.0)
    app = App.__new__(App)

    mixed_job = GameJob(
        info=info,
        selected=sample,
        candidates=[sample],
        repository_icon_artwork=icon_artwork,
        repository_tv_gamepad_artwork=banner_artwork,
    )
    mixed = App._export_job(
        app,
        mixed_job,
        options=ExportOptions(tmp_path / "mixed", screen_crop="full", include_banner_metadata=False),
    )

    icon_reference_job = GameJob(info=info, repository_artwork=icon_artwork)
    icon_reference = App._export_job(
        app,
        icon_reference_job,
        options=ExportOptions(tmp_path / "icon-reference", screen_crop="full", include_banner_metadata=False),
    )
    banner_reference_job = GameJob(info=info, repository_artwork=banner_artwork)
    banner_reference = App._export_job(
        app,
        banner_reference_job,
        options=ExportOptions(tmp_path / "banner-reference", screen_crop="full", include_banner_metadata=False),
    )

    with Image.open(mixed / "title_screen.png") as title:
        assert title.getpixel((10, 10)) == (255, 0, 0)
    assert (mixed / "Icon Image.png").read_bytes() == (icon_reference / "Icon Image.png").read_bytes()
    assert (mixed / "TV Image.png").read_bytes() == (banner_reference / "TV Image.png").read_bytes()
    assert (mixed / "Gamepad Image.png").read_bytes() == (banner_reference / "Gamepad Image.png").read_bytes()
    metadata = (mixed / "metadata.txt").read_text(encoding="utf-8")
    assert "Assigned Outputs: Icon Image" in metadata
    assert "Assigned Outputs: TV Image and Gamepad Image" in metadata
    assert "Source: Icon Source" in metadata
    assert "Source: Banner Source" in metadata


def test_v451_review_assignment_controls_preserve_the_other_artwork_role(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    class Tree:
        @staticmethod
        def selection():
            return ()

    icon_artwork = RepositoryArtwork(
        provider_name="Icon Choice",
        provider_url="https://example.invalid/icon",
        product_code="",
        system="SNES",
        source_image_path=tmp_path / "icon.png",
    )
    banner_artwork = RepositoryArtwork(
        provider_name="Banner Choice",
        provider_url="https://example.invalid/banner",
        product_code="",
        system="SNES",
        source_image_path=tmp_path / "banner.png",
    )
    info = RomInfo(tmp_path / "game.sfc", "SNES", "Assignment Test")
    job = GameJob(info=info)
    app = App.__new__(App)
    app.jobs = [job]
    app.tree = Tree()
    app.log = lambda _message: None
    app._export_job = lambda *_args, **_kwargs: tmp_path / "output"
    app._refresh_tree = lambda: None
    app._update_repository_artwork_review_state = lambda: None
    app._update_preview = lambda: None
    options = ExportOptions(tmp_path / "output")

    assert App._accept_repository_artwork(
        app,
        job,
        icon_artwork,
        options,
        automatic=False,
        show_confirmation=False,
        assignment="icon",
    )
    assert App._accept_repository_artwork(
        app,
        job,
        banner_artwork,
        options,
        automatic=False,
        show_confirmation=False,
        assignment="tv_gamepad",
    )

    assert job.repository_artwork is None
    assert job.repository_icon_artwork == icon_artwork
    assert job.repository_tv_gamepad_artwork == banner_artwork


def test_v451_finished_art_assignments_do_not_remove_the_other_output_group(tmp_path: Path):
    from vc_extractor.gui import GameJob
    from vc_extractor.repository_art import RepositoryArtwork

    capture = tmp_path / "capture.png"
    icon = tmp_path / "icon.png"
    tv = tmp_path / "tv.png"
    Image.new("RGB", (640, 360), "red").save(capture)
    Image.new("RGB", (128, 128), "lime").save(icon)
    Image.new("RGB", (1280, 720), "blue").save(tv)
    icon_artwork = RepositoryArtwork(
        provider_name="Finished Icon",
        provider_url="https://example.invalid/icon",
        product_code="TEST",
        system="SNES",
        icon_path=icon,
    )
    banner_artwork = RepositoryArtwork(
        provider_name="Finished Banner",
        provider_url="https://example.invalid/banner",
        product_code="TEST",
        system="SNES",
        tv_path=tv,
    )
    sample = FrameSample(capture, 0.0)
    job = GameJob(
        info=RomInfo(tmp_path / "game.sfc", "SNES", "Finished Assignment Test"),
        selected=sample,
        candidates=[sample],
        repository_icon_artwork=icon_artwork,
        repository_tv_gamepad_artwork=banner_artwork,
    )
    app = App.__new__(App)
    output = App._export_job(
        app,
        job,
        options=ExportOptions(tmp_path / "output", screen_crop="full", include_banner_metadata=False),
    )

    with Image.open(output / "Icon Image.png") as rendered_icon:
        assert rendered_icon.getpixel((64, 64)) == (0, 255, 0)
    with Image.open(output / "TV Image.png") as rendered_tv:
        assert rendered_tv.getpixel((640, 360)) == (0, 0, 255)
    with Image.open(output / "Gamepad Image.png") as rendered_gamepad:
        assert rendered_gamepad.getpixel((427, 240)) == (0, 0, 255)


def test_v451_artwork_review_exposes_independent_assignment_buttons():
    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")

    assert 'text="Use for All Outputs"' in gui_text
    assert 'text="Use for Icon Image"' in gui_text
    assert 'text="Use for TV/Gamepad Images"' in gui_text
    assert 'self.use_selected("icon")' in gui_text
    assert 'self.use_selected("tv_gamepad")' in gui_text


def test_v453_only_original_generic_sounds_are_bundled():
    from vc_extractor.repository_art import built_in_boot_sounds

    expected = ["generic_retro_startup.wav", "generic_modern_startup.wav"]
    for system in ("Game Boy", "Nintendo DS", "GameCube", "Wii", "NES", "Genesis / Mega Drive"):
        sounds = built_in_boot_sounds(system)
        assert [sound.path.name for sound in sounds] == expected
        assert all(sound.provider_name == "Built-in sound library" for sound in sounds)
        assert all(sound.sound_kind == "Built-in generic startup" for sound in sounds)
        assert all(sound.path.is_file() and sound.path.stat().st_size > 44 for sound in sounds)

    sound_root = Path(__file__).resolve().parents[1] / "assets" / "boot_sounds"
    assert sorted(path.name for path in sound_root.iterdir() if path.is_file()) == sorted(expected)
    assert not list(sound_root.glob("*.mp3"))


def test_v453_original_generic_audio_decodes_through_normal_export_path(tmp_path: Path):
    from vc_extractor.boot_sound import convert_boot_sound_to_wav
    from vc_extractor.repository_art import built_in_boot_sounds

    generic = built_in_boot_sounds("GameCube")
    assert len(generic) == 2
    for sound in generic:
        name = sound.path.name
        destination = tmp_path / f"{Path(name).stem}.wav"
        convert_boot_sound_to_wav(sound.path, destination)
        assert destination.is_file()
        assert destination.stat().st_size > 44


def test_v453_per_game_import_uses_persistent_configuration_storage(tmp_path: Path, monkeypatch):
    import wave
    import vc_extractor.gui as gui

    monkeypatch.setattr(gui, "config_dir", lambda: tmp_path / "config")
    info = RomInfo(tmp_path / "Example Game.gba", "Game Boy Advance", "Example Game", "ABCE")
    destination = App._game_boot_sound_path(info)
    assert destination == tmp_path / "config" / "game_boot_sounds" / "game_boy_advance" / "abce.wav"
    destination.parent.mkdir(parents=True)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\x00\x00" * 400)

    sound = App._stored_game_boot_sound(info)
    assert sound is not None
    assert sound.path == destination
    assert sound.provider_name == "Stored game sound"
    assert sound.sound_kind == "Stored per-game"


def test_v453_more_sounds_persistent_import_and_packaging_are_wired():
    from vc_extractor.repository_art import myinstants_search_url

    root = Path(__file__).resolve().parents[1]
    gui_text = (root / "vc_extractor" / "gui.py").read_text(encoding="utf-8")
    build_text = (root / "scripts" / "build_windows.py").read_text(encoding="utf-8")

    assert 'text="More Sounds..."' in gui_text
    assert "def browse_myinstants_boot_sounds(self):" in gui_text
    assert "built_in_boot_sounds(job.info.system)" in gui_text
    assert 'sound_kind.startswith("Built-in")' in gui_text
    assert "convert_boot_sound_to_wav(path, destination)" in gui_text
    assert 'provider_name="Stored game sound"' in gui_text
    assert "stored_game_sound = self._stored_game_boot_sound(info)" in gui_text
    assert 'active_sound.provider_name == "Stored game sound"' in gui_text
    assert "gamecube+startup" in myinstants_search_url("GameCube")
    assert "nintendo+ds+startup" in myinstants_search_url("Nintendo DS")
    assert '"--add-data", str(ROOT / "assets") + ";assets"' in build_text
    assert len(list((root / "assets/boot_sounds").glob("*.wav"))) == 2
