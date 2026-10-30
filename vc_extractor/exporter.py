from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .roms import RomInfo, default_output_key, system_folder
from .scoring import FrameSample


@dataclass
class ExportOptions:
    output_root: Path
    screen_crop: str = "auto"  # auto, full, top, bottom, left, right
    template_root: Path | None = None
    font_path: Path | None = None
    create_icon: bool = True
    create_tv: bool = True
    create_gamepad: bool = True
    include_banner_metadata: bool = True
    include_game_title: bool = True
    include_release_date: bool = True
    include_player_count: bool = True


TV_SIZE = (1280, 720)
DRC_SIZE = (854, 480)
ICON_SIZE = (128, 128)

SCREENSHOT_ONLY_TEMPLATE = "__screenshot_only__"

ICON_TEMPLATE_CHOICES = {
    "NES": [("Light Frame", "NES alt1.png"), ("Dark Frame", "NES alt2.png")],
    "SNES": [("Light Frame", "SNES alt1.png"), ("Dark Frame", "SNES alt2.png")],
    "Nintendo DS": [("Light Frame", "NDS Alt1.png"), ("Dark Frame", "NDS Alt2.png")],
    "Game Boy": [("Light Frame", "GB alt1.png"), ("Dark Frame", "GB alt2.png")],
    "Game Boy Color": [("Light Frame", "GBC alt1.png"), ("Dark Frame", "GBC alt2.png")],
    "Game Boy Advance": [("Light Frame", "GBA alt1.png"), ("Dark Frame", "GBA alt2.png")],
    "Nintendo 64": [("Light Frame", "N64 alt1.png"), ("Dark Frame", "N64 alt2.png")],
    "GameCube": [("Light Frame", "GCN ICON2.png"), ("Dark Frame", "GCN ICON3.png")],
    "MSX": [("Light Frame", "MSX alt1.png"), ("Dark Frame", "MSX alt2.png")],
    "TurboGrafx-16 / PC Engine": [("Light Frame", "TGFX alt1.png"), ("Dark Frame", "TGFX alt2.png")],
    "Wii": [("WiiWare Frame", "WiiIcon.png"), ("Wii Frame", "Wii2.png")],
    "WiiWare": [("WiiWare Frame", "WiiIcon.png"), ("Wii Frame", "Wii2.png")],
}


def icon_template_choices(system: str) -> list[tuple[str, str]]:
    return [("Screenshot only", SCREENSHOT_ONLY_TEMPLATE)] + ICON_TEMPLATE_CHOICES.get(system, [])


def default_icon_template_for_system(system: str) -> str:
    # Wii and WiiWare share both templates, but their natural defaults differ.
    if system == "Wii":
        return "Wii2.png"
    if system == "WiiWare":
        return "WiiIcon.png"
    choices = ICON_TEMPLATE_CHOICES.get(system, [])
    return choices[0][1] if choices else ""


ICON_IMAGE_NAME = "Icon Image.png"
TV_IMAGE_NAME = "TV Image.png"
GAMEPAD_IMAGE_NAME = "Gamepad Image.png"
LEGACY_OUTPUT_NAMES = ["iconTex.png", "bootTvTex.png", "bootDrcTex.png"]
# UWUVCI BootImage.cs source geometry.
SHOT_RECTS = {
    "Game Boy Advance": (132, 260, 399, 266),
    "Game Boy": (183, 260, 296, 266),
    "Game Boy Color": (183, 260, 296, 266),
    "Wii": (224, 200, 832, 333),
}
DEFAULT_SHOT_RECT = (131, 249, 400, 300)


def default_shot_rect(system: str) -> tuple[int, int, int, int]:
    return SHOT_RECTS.get(system, DEFAULT_SHOT_RECT)


def shot_rect_for_info(info: RomInfo) -> tuple[int, int, int, int]:
    if info.shot_width > 0 and info.shot_height > 0:
        return (int(info.shot_x), int(info.shot_y), int(info.shot_width), int(info.shot_height))
    return default_shot_rect(info.system)
TITLE_RECT_SHORT = (578, 340, 640, 50)
TITLE_RECT_LONG_1 = (578, 313, 640, 50)
TITLE_RECT_LONG_2 = (578, 368, 640, 50)
RELEASE_RECT = (586, 450, 600, 40)
PLAYERS_RECT = (586, 496, 600, 40)
BANNER_TEXT_RIGHT = 1218
BANNER_TEXT_PADDING_X = 26
# 37pt and 25pt at the 96-DPI bitmap used by System.Drawing.
TITLE_FONT_PX = 49
META_FONT_PX = 33

# Wii/WiiWare keep the original UWUVCI screenshot opening at all times.
# Metadata is drawn only on the opaque frame above and below that opening.
WII_TITLE_BAND = (240, 90, 800, 88)
WII_META_BAND = (240, 556, 800, 88)
WII_SCREENSHOT_RECT = (224, 200, 832, 333)
# Compatibility/safety bounds for the fully transparent interior of WII.png.
WII_SCREENSHOT_TOP_WITH_TITLE = 201
WII_SCREENSHOT_BOTTOM_WITH_META = 532
WII_TEXT_IMAGE_MIN_GAP = 20


def output_directory(info: RomInfo, options: ExportOptions) -> Path:
    return options.output_root / system_folder(info.system) / default_output_key(info)


def apply_screen_crop(image: Image.Image, mode: str) -> Image.Image:
    mode = (mode or "full").lower()
    if mode == "auto":
        return image
    w, h = image.size
    if mode == "top":
        return image.crop((0, 0, w, max(1, h // 2)))
    if mode == "bottom":
        return image.crop((0, h // 2, w, h))
    if mode == "left":
        return image.crop((0, 0, max(1, w // 2), h))
    if mode == "right":
        return image.crop((w // 2, 0, w, h))
    return image


def _clean_display_title(info: RomInfo) -> str:
    raw = (info.display_name or info.path.stem or info.title or "Game").replace("_", " ").strip()
    metadata_terms = re.compile(
        r"(?i)^(?:usa|europe|japan|world|australia|korea|germany|france|italy|spain|"
        r"rev(?:ision)?\s*\w*|v\d+(?:\.\d+)*|en(?:,[a-z]{2})*|[a-z]{2}(?:,[a-z]{2})+|"
        r"proto(?:type)?|beta|demo|sample|unl|unlicensed|virtual console|"
        r"[!btohfp]\d*|crc\s*[0-9a-f]+)$"
    )

    def clean_one_line(value: str) -> str:
        def clean_group(match):
            inner = match.group(1).strip()
            return "" if metadata_terms.match(inner) else match.group(0)

        line = re.sub(r"\(([^()]*)\)", clean_group, value)
        line = re.sub(r"\[([^\[\]]*)\]", clean_group, line)
        return re.sub(r"\s+", " ", line).strip(" -_.")

    manual_lines = [clean_one_line(line) for line in raw.splitlines()]
    manual_lines = [line for line in manual_lines if line]
    if manual_lines:
        return "\n".join(manual_lines[:3])
    cleaned = clean_one_line(raw)
    return cleaned or (info.title or "Game")


def _system_label(system: str) -> str:
    mapping = {
        "NES": "Nintendo Entertainment System",
        "SNES": "Super Nintendo Entertainment System",
        "Nintendo DS": "Nintendo DS",
        "Game Boy": "Game Boy",
        "Game Boy Color": "Game Boy Color",
        "Game Boy Advance": "Game Boy Advance",
        "Nintendo 64": "Nintendo 64",
        "GameCube": "Nintendo GameCube",
        "Wii": "Wii",
        "WiiWare": "WiiWare",
        "TurboGrafx-CD / PC Engine CD": "TurboGrafx-CD / PC Engine CD",
        "Genesis / Mega Drive": "Sega Genesis / Mega Drive",
        "Master System": "Sega Master System",
        "Game Gear": "Sega Game Gear",
        "TurboGrafx-16 / PC Engine": "TurboGrafx-16 / PC Engine",
        "MSX": "MSX",
    }
    return mapping.get(system, system)


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _manual_title_lines(title: str, max_lines: int = 3) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (title or "").splitlines()]
    return [line for line in lines if line][:max_lines]


def _normalize_fill_mode(value: str | None) -> str:
    value = (value or "Black").strip().lower()
    if value in {"blurred image", "blur", "blurred"}:
        return "Blurred Image"
    if value in {"solid color", "solid"}:
        return "Solid Color"
    return "Black"


def _parse_hex_color(value: str | None, default=(0, 0, 0, 255)):
    raw = (value or "").strip()
    if not raw:
        return default
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if len(raw) == 4:
        raw = "#" + "".join(ch * 2 for ch in raw[1:])
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return default
    return tuple(int(raw[i:i+2], 16) for i in (1, 3, 5)) + (255,)


def _background_fill_image(source: Image.Image, size: tuple[int, int], fill_mode: str = "Black", fill_color: str = "#000000") -> Image.Image:
    mode = _normalize_fill_mode(fill_mode)
    if mode == "Solid Color":
        return Image.new("RGBA", size, _parse_hex_color(fill_color))
    if mode == "Blurred Image":
        im = source.convert("RGBA")
        scale = max(size[0] / im.width, size[1] / im.height)
        new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        bg = im.resize(new_size, Image.Resampling.LANCZOS)
        left = max(0, (bg.width - size[0]) // 2)
        top = max(0, (bg.height - size[1]) // 2)
        bg = bg.crop((left, top, left + size[0], top + size[1]))
        return bg.filter(ImageFilter.GaussianBlur(radius=max(8, round(min(size) / 30))))
    return Image.new("RGBA", size, (0, 0, 0, 255))


def _default_template_root() -> Path:
    return _package_root() / "assets" / "templates"


def _template_name(info: RomInfo) -> str | None:
    region = (info.region or "").upper()
    system = info.system
    mapping = {
        "NES": "NES.png",
        "Nintendo DS": "NDS.png",
        "Game Boy Advance": "GBA.png",
        "Game Boy Color": "GBC.png",
        "Nintendo 64": "N64.png",
        "GameCube": "GCN.png",
        "TurboGrafx-16 / PC Engine": "TG16.png",
        "MSX": "MSX.png",
        "Wii": "WII.png",
        "WiiWare": "WIIWARE.png",
        "TurboGrafx-CD / PC Engine CD": "TGCD.png",
    }
    if system == "SNES":
        if region in {"JPN", "JAPAN"}:
            return "SFAM.png"
        if region in {"EUR", "EUROPE", "PAL"}:
            return "SNES-PAL.png"
        return "SNES-USA.png"
    if system == "Game Boy":
        return "newgameboy.png"
    return mapping.get(system)


def _icon_template_name(info: RomInfo) -> str | None:
    # An explicit Screenshot only selection must bypass all built-in frames.
    # An empty value means no per-game choice has been made yet, so use the
    # normal system default frame.
    if info.icon_template == SCREENSHOT_ONLY_TEMPLATE:
        return None
    if info.icon_template:
        return info.icon_template
    default_name = default_icon_template_for_system(info.system)
    return default_name or None


def _template_path(filename: str | None, options: ExportOptions | None = None) -> Path | None:
    if not filename:
        return None
    roots: list[Path] = []
    if options and options.template_root:
        roots.append(Path(options.template_root))
    roots.append(_default_template_root())
    for root in roots:
        p = root / filename
        try:
            if p.is_file():
                return p
        except OSError:
            pass
    return None


def _largest_placeholder_region_mask(template: Image.Image) -> tuple[Image.Image | None, tuple[int, int, int, int] | None]:
    rgba = template.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    visited = bytearray(w * h)
    best_coords: list[tuple[int, int]] = []
    best_bbox: tuple[int, int, int, int] | None = None
    best_count = 0

    def idx(x: int, y: int) -> int:
        return y * w + x

    for y in range(h):
        for x in range(w):
            i = idx(x, y)
            if visited[i]:
                continue
            r, g, b, a = px[x, y]
            is_placeholder = a == 0
            if not is_placeholder:
                visited[i] = 1
                continue
            stack = [(x, y)]
            visited[i] = 1
            coords: list[tuple[int, int]] = []
            minx = maxx = x
            miny = maxy = y
            while stack:
                cx, cy = stack.pop()
                coords.append((cx, cy))
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy
                for nx, ny in ((cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        ni = idx(nx, ny)
                        if visited[ni]:
                            continue
                        rr, gg, bb, aa = px[nx, ny]
                        place = aa == 0
                        visited[ni] = 1
                        if place:
                            stack.append((nx, ny))
            count = len(coords)
            if count > best_count:
                best_count = count
                best_coords = coords
                best_bbox = (minx, miny, maxx + 1, maxy + 1)

    if not best_coords or best_bbox is None:
        return None, None
    mask = Image.new("L", (w, h), 0)
    mpx = mask.load()
    for x, y in best_coords:
        mpx[x, y] = 255
    return mask, best_bbox


def _is_light_neutral(pixel: tuple[int, int, int, int]) -> bool:
    r, g, b, a = pixel
    if a == 0:
        return False
    return abs(r - g) <= 4 and abs(g - b) <= 4 and 120 <= r <= 245


def _expand_placeholder_for_inner_seams(template: Image.Image, mask: Image.Image, bbox: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Expand the screenshot fill by 1 px into light inner seam lines.

    Some bundled icon templates have a thin light gray row or column directly
    adjacent to the transparent screenshot cutout. Extending the fill into
    those seam lines avoids a visible white/gray line inside dark frames.
    """
    x0, y0, x1, y1 = bbox
    rgba = template.convert("RGBA")
    px = rgba.load()
    mpx = mask.load()

    def row_qualifies(y: int, x_start: int, x_end: int) -> bool:
        if y < 0 or y >= rgba.height or x_start >= x_end:
            return False
        samples = [px[x, y] for x in range(x_start, x_end)]
        good = sum(1 for s in samples if _is_light_neutral(s))
        return good >= max(1, int(len(samples) * 0.95))

    def col_qualifies(x: int, y_start: int, y_end: int) -> bool:
        if x < 0 or x >= rgba.width or y_start >= y_end:
            return False
        samples = [px[x, y] for y in range(y_start, y_end)]
        good = sum(1 for s in samples if _is_light_neutral(s))
        return good >= max(1, int(len(samples) * 0.95))

    if row_qualifies(y0 - 1, x0, x1):
        y0 -= 1
        for x in range(x0, x1):
            mpx[x, y0] = 255
    if row_qualifies(y1, x0, x1):
        for x in range(x0, x1):
            mpx[x, y1] = 255
        y1 += 1
    if col_qualifies(x0 - 1, y0, y1):
        x0 -= 1
        for y in range(y0, y1):
            mpx[x0, y] = 255
    if col_qualifies(x1, y0, y1):
        for y in range(y0, y1):
            mpx[x1, y] = 255
        x1 += 1
    return mask, (x0, y0, x1, y1)


def _place_scaled_image(
    image: Image.Image,
    size: tuple[int, int],
    scale: float,
    focus_x: int | float = 50,
    focus_y: int | float = 50,
    bg=(0, 0, 0, 255),
) -> Image.Image:
    """Scale an image and place/crop it into a fixed target using a focal point."""
    target_w, target_h = size
    im = image.convert("RGBA")
    new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new_size, Image.Resampling.LANCZOS)
    fx = max(0.0, min(100.0, float(focus_x))) / 100.0
    fy = max(0.0, min(100.0, float(focus_y))) / 100.0

    if im.width >= target_w:
        extra_x = im.width - target_w
        src_left = max(0, min(extra_x, round(extra_x * fx)))
        crop_w = target_w
        dst_x = 0
    else:
        src_left = 0
        crop_w = im.width
        dst_x = max(0, min(target_w - im.width, round((target_w - im.width) * fx)))

    if im.height >= target_h:
        extra_y = im.height - target_h
        src_top = max(0, min(extra_y, round(extra_y * fy)))
        crop_h = target_h
        dst_y = 0
    else:
        src_top = 0
        crop_h = im.height
        dst_y = max(0, min(target_h - im.height, round((target_h - im.height) * fy)))

    piece = im.crop((src_left, src_top, src_left + crop_w, src_top + crop_h))
    target = Image.new("RGBA", size, bg)
    target.alpha_composite(piece, (dst_x, dst_y))
    return target


def _cover_fit(
    image: Image.Image,
    size: tuple[int, int],
    focus_x: int | float = 50,
    focus_y: int | float = 50,
    zoom: int | float = 100,
    fill_mode: str = "Black",
    fill_color: str = "#000000",
) -> Image.Image:
    """Aspect-preserving cover crop with focal point and adjustable zoom."""
    target_w, target_h = size
    im = image.convert("RGBA")
    base_scale = max(target_w / im.width, target_h / im.height)
    zoom_factor = max(0.1, float(zoom) / 100.0)
    bg = _background_fill_image(im, size, fill_mode, fill_color)
    fg = _place_scaled_image(im, size, base_scale * zoom_factor, focus_x, focus_y, bg=(0, 0, 0, 0))
    return _overlay_on_bg(fg, bg)


def _contain_fit_zoom(
    image: Image.Image,
    size: tuple[int, int],
    focus_x: int | float = 50,
    focus_y: int | float = 50,
    zoom: int | float = 100,
    fill_mode: str = "Black",
    fill_color: str = "#000000",
) -> Image.Image:
    """Aspect-preserving contain fit used by screenshot-only icons, with zoom."""
    target_w, target_h = size
    im = image.convert("RGBA")
    base_scale = min(target_w / im.width, target_h / im.height)
    zoom_factor = max(0.1, float(zoom) / 100.0)
    bg = _background_fill_image(im, size, fill_mode, fill_color)
    return _overlay_on_bg(_place_scaled_image(im, size, base_scale * zoom_factor, focus_x, focus_y, bg=(0,0,0,0)), bg)


def _overlay_on_bg(fg: Image.Image, bg: Image.Image) -> Image.Image:
    out = bg.convert("RGBA")
    out.alpha_composite(fg, (0, 0))
    return out


def _find_uwuvci_font(options: ExportOptions | None = None) -> Path | None:
    """Find UWUVCI's own private banner font without redistributing it."""
    candidates: list[Path] = []
    if options and options.font_path:
        candidates.append(Path(options.font_path))
    env_home = os.environ.get("UWUVCI_HOME")
    if env_home:
        candidates.append(Path(env_home) / "bin" / "Tools" / "font.otf")
    roots = [Path.cwd(), _package_root(), _package_root().parent]
    for root in roots:
        candidates.extend([
            root / "bin" / "Tools" / "font.otf",
            root / "UWUVCI" / "bin" / "Tools" / "font.otf",
            root / "UWUVCI AIO WPF" / "bin" / "Tools" / "font.otf",
        ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    return None


def _pick_font(size: int, bold: bool = False, italic: bool = False, options: ExportOptions | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # UWUVCI loads bin\Tools\font.otf into a PrivateFontCollection. Use the
    # same file directly in Pillow when the user's UWUVCI copy is available.
    exact = _find_uwuvci_font(options)
    if exact:
        try:
            return ImageFont.truetype(str(exact), size)
        except Exception:
            pass
    candidates: list[str] = []
    if bold and italic:
        candidates += [
            "C:/Windows/Fonts/trebucbi.ttf",
            "C:/Windows/Fonts/arialbi.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        ]
    elif bold:
        candidates += [
            "C:/Windows/Fonts/trebucbd.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    elif italic:
        candidates += [
            "C:/Windows/Fonts/trebucit.ttf",
            "C:/Windows/Fonts/ariali.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/trebuc.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int], bg=(0, 0, 0, 255)) -> Image.Image:
    target = Image.new("RGBA", size, bg)
    im = image.convert("RGBA")
    scale = min(size[0] / im.width, size[1] / im.height)
    new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new_size, Image.Resampling.LANCZOS)
    target.alpha_composite(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    return target


def _rounded(im: Image.Image, radius: int = 12) -> Image.Image:
    im = im.convert("RGBA")
    mask = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1), radius=radius, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 2) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
    remaining = words[len(" ".join(lines + [current]).split()):]
    tail = current
    if remaining:
        extra = " " + " ".join(remaining)
        while True:
            test = tail + extra + "…"
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width or len(tail) < 4:
                tail = test
                break
            tail = tail[:-1].rstrip()
        lines.append(tail)
    else:
        lines.append(current)
    return lines[:max_lines]


def _draw_text_with_glow(draw: ImageDraw.ImageDraw, pos: tuple[int, int], text: str, font, fill=(48, 48, 48, 255), spacing: int = 6):
    x, y = pos
    glow_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)]
    for ox, oy in glow_offsets:
        draw.multiline_text((x + ox, y + oy), text, font=font, fill=(255, 255, 255, 170), spacing=spacing)
    draw.multiline_text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 35), spacing=spacing)
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=spacing)


def _load_template(info: RomInfo, options: ExportOptions) -> Image.Image:
    template_root = options.template_root or _default_template_root()
    name = _template_name(info)
    if name:
        path = Path(template_root) / name
        if path.exists():
            return Image.open(path).convert("RGBA")
    # generic fallback if a specific template does not exist
    canvas = Image.new("RGBA", TV_SIZE, (232, 232, 232, 255))
    d = ImageDraw.Draw(canvas)
    vc_font = _pick_font(58, bold=True, italic=True, options=options)
    d.text((45, 80), "Virtual Console", font=vc_font, fill=(145, 145, 151, 255))
    panel = (64, 197, 1218, 605)
    d.rounded_rectangle(panel, radius=26, fill=(246, 246, 246, 160), outline=(212, 212, 212, 180), width=2)
    sys_font = _pick_font(34, bold=True, options=options)
    d.text((590, 230), _system_label(info.system), font=sys_font, fill=(96, 96, 96, 255))
    return canvas


def _wrap_without_ellipsis(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 2) -> list[str] | None:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                return None
    lines.append(current)
    if len(lines) > max_lines:
        return None
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        if bbox[2] - bbox[0] > max_width:
            return None
    return lines


def _rendered_text_width(draw: ImageDraw.ImageDraw, text: str, font, *, bold: bool, scale: float = 1.0) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    base = bbox[2] - bbox[0]
    stroke = max(1, round((7 if bold else 6) * scale)) * 2
    extra = max(1, round(scale)) if bold else 0
    return base + stroke + extra


def _fit_single_line_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    options: ExportOptions,
    *,
    base_size: int,
    min_size: int,
    max_width: int,
    bold: bool = False,
    scale: float = 1.0,
):
    text = (text or '').strip()
    if not text:
        return _pick_font(max(min_size, 1), bold=bold, options=options)
    step = max(1, round(base_size / 24))
    for size in range(base_size, min_size - 1, -step):
        font = _pick_font(size, bold=bold, options=options)
        if _rendered_text_width(draw, text, font, bold=bold, scale=scale) <= max_width:
            return font
    return _pick_font(min_size, bold=bold, options=options)


def _fit_title_lines(
    draw: ImageDraw.ImageDraw,
    title: str,
    options: ExportOptions,
    base_size: int = TITLE_FONT_PX,
    max_width: int = TITLE_RECT_SHORT[2],
    min_size: int = 24,
    scale: float = 1.0,
) -> tuple[ImageFont.ImageFont, list[str]]:
    step = max(1, round(base_size / 24))
    stroke_pad = max(6, max(1, round(7 * scale)) * 2 + max(1, round(scale)))
    wrap_width = max(20, max_width - stroke_pad)
    manual = _manual_title_lines(title, 3)
    if manual:
        for size in range(base_size, min_size - 1, -step):
            font = _pick_font(size, bold=True, options=options)
            if all(_rendered_text_width(draw, line, font, bold=True, scale=scale) <= max_width for line in manual):
                return font, manual
        return _pick_font(min_size, bold=True, options=options), manual
    for size in range(base_size, min_size - 1, -step):
        font = _pick_font(size, bold=True, options=options)
        lines = _wrap_without_ellipsis(draw, title, font, wrap_width, max_lines=3)
        if lines and all(_rendered_text_width(draw, line, font, bold=True, scale=scale) <= max_width for line in lines):
            return font, lines
    smallest = _pick_font(min_size, bold=True, options=options)
    return smallest, _wrap_text(draw, title, smallest, wrap_width, max_lines=3)


def _uwuvci_layered_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, *, bold: bool, scale: float = 1.0):
    """Reproduce BootImage.cs nested shadow/outline/fill treatment."""
    if not text:
        return
    shadow_w = max(1, round((7 if bold else 6) * scale))
    outline_w = max(1, round((5 if bold else 4) * scale))
    shadow = (190, 190, 190, 255)
    outline = (222, 222, 222, 255)
    fill = (32, 32, 32, 255)
    # Draw a wide gray path, then the narrower light outline, then the dark fill.
    draw.text(xy, text, font=font, fill=shadow, stroke_width=shadow_w, stroke_fill=shadow)
    draw.text(xy, text, font=font, fill=outline, stroke_width=outline_w, stroke_fill=outline)
    # Slight synthetic emboldening for the title. System.Drawing asks the same
    # private family for FontStyle.Bold even though the source loads one OTF.
    if bold:
        draw.text((xy[0] + max(1, round(scale)), xy[1]), text, font=font, fill=fill)
    draw.text(xy, text, font=font, fill=fill)


def _scale_rect(rect: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    sx = size[0] / TV_SIZE[0]
    sy = size[1] / TV_SIZE[1]
    x, y, w, h = rect
    return (round(x * sx), round(y * sy), max(1, round(w * sx)), max(1, round(h * sy)))


def _text_box(draw: ImageDraw.ImageDraw, text: str, font, *, bold: bool, scale: float = 1.0):
    if not text:
        return (0, 0, 0, 0)
    shadow_w = max(1, round((7 if bold else 6) * scale))
    extra = max(1, round(scale)) if bold else 0
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=shadow_w)
    return (bbox[0], bbox[1], bbox[2] + extra, bbox[3])


def _draw_centered_layered_text(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], text: str, font, *, bold: bool, scale: float = 1.0):
    if not text:
        return
    x, y, w, h = rect
    bbox = _text_box(draw, text, font, bold=bold, scale=scale)
    text_w = max(1, bbox[2] - bbox[0])
    text_h = max(1, bbox[3] - bbox[1])
    px = x + max(0, (w - text_w) // 2)
    py = y + max(0, (h - text_h) // 2)
    _uwuvci_layered_text(draw, (px, py), text, font, bold=bold, scale=scale)


def _fit_single_line_font_to_rect(
    draw: ImageDraw.ImageDraw,
    text: str,
    options: ExportOptions,
    *,
    base_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    bold: bool = False,
    scale: float = 1.0,
):
    text = (text or "").strip()
    step = max(1, round(base_size / 24))
    for size in range(base_size, min_size - 1, -step):
        font = _pick_font(size, bold=bold, options=options)
        bbox = _text_box(draw, text, font, bold=bold, scale=scale)
        if (bbox[2] - bbox[0]) <= max_width and (bbox[3] - bbox[1]) <= max_height:
            return font
    return _pick_font(min_size, bold=bold, options=options)


def _fit_title_lines_to_rect(
    draw: ImageDraw.ImageDraw,
    title: str,
    options: ExportOptions,
    *,
    base_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    scale: float = 1.0,
) -> tuple[ImageFont.ImageFont, list[str]]:
    step = max(1, round(base_size / 24))
    spacing = max(1, round(3 * scale))
    manual = _manual_title_lines(title, 3)
    for size in range(base_size, min_size - 1, -step):
        font = _pick_font(size, bold=True, options=options)
        stroke_pad = max(6, max(1, round(7 * scale)) * 2 + max(1, round(scale)))
        lines = manual or _wrap_without_ellipsis(draw, title, font, max(20, max_width - stroke_pad), max_lines=3)
        if not lines:
            continue
        boxes = [_text_box(draw, line, font, bold=True, scale=scale) for line in lines]
        widths = [b[2] - b[0] for b in boxes]
        heights = [b[3] - b[1] for b in boxes]
        total_h = sum(heights) + spacing * max(0, len(lines) - 1)
        if max(widths, default=0) <= max_width and total_h <= max_height:
            return font, lines
    font = _pick_font(min_size, bold=True, options=options)
    lines = manual or _wrap_text(draw, title, font, max(20, max_width - 12), max_lines=3)
    return font, lines


def _draw_centered_multiline_layered_text(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    lines: list[str],
    font,
    *,
    bold: bool,
    scale: float = 1.0,
):
    if not lines:
        return
    x, y, w, h = rect
    spacing = max(1, round(3 * scale))
    boxes = [_text_box(draw, line, font, bold=bold, scale=scale) for line in lines]
    heights = [max(1, b[3] - b[1]) for b in boxes]
    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    cy = y + max(0, (h - total_h) // 2)
    for line, bbox, line_h in zip(lines, boxes, heights):
        line_w = max(1, bbox[2] - bbox[0])
        px = x + max(0, (w - line_w) // 2)
        _uwuvci_layered_text(draw, (px, cy), line, font, bold=bold, scale=scale)
        cy += line_h + spacing


def _composite_clipped_region(canvas: Image.Image, layer: Image.Image, rect: tuple[int, int, int, int]) -> None:
    x, y, w, h = rect
    region = layer.crop((x, y, x + w, y + h))
    canvas.alpha_composite(region, (x, y))


def _banner_text_strings(info: RomInfo, options: ExportOptions) -> tuple[str, str, str]:
    title = _clean_display_title(info) if options.include_game_title else ""
    year = (getattr(info, "release_year", "") or "").strip()
    players = (getattr(info, "players", "") or "").strip()
    release_text = f"Released: {year}" if options.include_release_date and year and year.isdigit() and int(year) > 0 else ""
    players_text = ""
    if options.include_player_count and players:
        p = players.strip()
        if p.isdigit():
            n = int(p)
            p = "1-4" if n >= 4 else ("1" if n == 1 else f"1-{n}")
        players_text = f"Players: {p}"
    return title, release_text, players_text


def _render_banner(image: Image.Image, info: RomInfo, options: ExportOptions, size: tuple[int, int], focus_x: int = 50, focus_y: int = 50, zoom: int = 100) -> Image.Image:
    """Render a banner without changing the screenshot's aspect ratio.

    The template geometry scales to the requested Wii U asset size, while the
    title screenshot itself uses cover-fit behavior: uniform scaling followed
    by centered cropping only when necessary.
    """
    sx = size[0] / TV_SIZE[0]
    sy = size[1] / TV_SIZE[1]
    text_scale = min(sx, sy)

    template = _load_template(info, options)
    if template.size != size:
        template = template.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))

    show_text = options.include_banner_metadata
    title, release_text, players_text = _banner_text_strings(info, options if show_text else ExportOptions(output_root=options.output_root, include_banner_metadata=False, include_game_title=False, include_release_date=False, include_player_count=False))

    use_wii_layout = info.system in {"Wii", "WiiWare"} and show_text and (title or release_text or players_text)
    # Wii always uses the template's full transparent screenshot opening. Text
    # never changes or shrinks this rectangle; it lives on the opaque frame.
    shot_rect = WII_SCREENSHOT_RECT if info.system in {"Wii", "WiiWare"} else shot_rect_for_info(info)

    x, y, w, h = _scale_rect(shot_rect, size)
    if size == TV_SIZE:
        fill_mode = getattr(info, "tv_fill_mode", "Black")
        fill_color = getattr(info, "tv_fill_color", "#000000")
    elif size == DRC_SIZE:
        fill_mode = getattr(info, "gamepad_fill_mode", "Black")
        fill_color = getattr(info, "gamepad_fill_color", "#000000")
    else:
        fill_mode = "Black"
        fill_color = "#000000"
    fitted = _cover_fit(image, (w, h), focus_x=focus_x, focus_y=focus_y, zoom=zoom, fill_mode=fill_mode, fill_color=fill_color)
    canvas.alpha_composite(fitted, (x, y))
    canvas.alpha_composite(template, (0, 0))
    draw = ImageDraw.Draw(canvas)

    if not show_text:
        return canvas.convert("RGB")

    if use_wii_layout:
        # Render text on separate layers and clip those layers to fixed safe bands.
        # This makes overlap with the screenshot mathematically impossible, even
        # when the title font has a wide gray stroke.
        if title:
            title_rect = _scale_rect(WII_TITLE_BAND, size)
            title_layer = Image.new("RGBA", size, (0, 0, 0, 0))
            title_draw = ImageDraw.Draw(title_layer)
            title_base = max(18, round((TITLE_FONT_PX + 2) * text_scale))
            title_min = max(11, round(18 * text_scale))
            title_font, title_lines = _fit_title_lines_to_rect(
                title_draw, title, options,
                base_size=title_base, min_size=title_min,
                max_width=max(40, title_rect[2]), max_height=max(30, title_rect[3]),
                scale=text_scale,
            )
            _draw_centered_multiline_layered_text(
                title_draw, title_rect, title_lines, title_font, bold=True, scale=text_scale
            )
            _composite_clipped_region(canvas, title_layer, title_rect)

        if release_text or players_text:
            meta_rect = _scale_rect(WII_META_BAND, size)
            meta_layer = Image.new("RGBA", size, (0, 0, 0, 0))
            meta_draw = ImageDraw.Draw(meta_layer)
            gap = max(1, round(2 * text_scale))
            line_count = int(bool(release_text)) + int(bool(players_text))
            line_h = max(10, (meta_rect[3] - gap * max(0, line_count - 1)) // max(1, line_count))
            current_y = meta_rect[1]
            meta_base = max(10, round(25 * text_scale))
            meta_min = max(8, round(14 * text_scale))
            for meta_text in (release_text, players_text):
                if not meta_text:
                    continue
                row_rect = (meta_rect[0], current_y, meta_rect[2], line_h)
                font = _fit_single_line_font_to_rect(
                    meta_draw, meta_text, options,
                    base_size=meta_base, min_size=meta_min,
                    max_width=max(30, row_rect[2]), max_height=max(10, row_rect[3]),
                    bold=False, scale=text_scale,
                )
                _draw_centered_layered_text(meta_draw, row_rect, meta_text, font, bold=False, scale=text_scale)
                current_y += line_h + gap
            _composite_clipped_region(canvas, meta_layer, meta_rect)

        return canvas.convert("RGB")

    def pos(rect):
        return (round(rect[0] * sx), round(rect[1] * sy))

    title_base = max(16, round(TITLE_FONT_PX * text_scale))
    title_min = max(12, round(24 * text_scale))
    title_max_width = max(40, round((BANNER_TEXT_RIGHT - TITLE_RECT_SHORT[0] - BANNER_TEXT_PADDING_X) * sx))
    if title:
        title_font, title_lines = _fit_title_lines(
            draw, title, options, base_size=title_base, max_width=title_max_width, min_size=title_min, scale=text_scale
        )
        if len(title_lines) > 2:
            title_rect = (
                round(TITLE_RECT_SHORT[0] * sx),
                round((TITLE_RECT_LONG_1[1] - 8) * sy),
                title_max_width,
                max(40, round(128 * sy)),
            )
            _draw_centered_multiline_layered_text(draw, title_rect, title_lines, title_font, bold=True, scale=text_scale)
        elif len(title_lines) > 1:
            _uwuvci_layered_text(draw, pos(TITLE_RECT_LONG_1), title_lines[0], title_font, bold=True, scale=text_scale)
            _uwuvci_layered_text(draw, pos(TITLE_RECT_LONG_2), title_lines[1], title_font, bold=True, scale=text_scale)
        elif len(title_lines) == 1:
            _uwuvci_layered_text(draw, pos(TITLE_RECT_SHORT), title_lines[0], title_font, bold=True, scale=text_scale)

    meta_base = max(12, round(META_FONT_PX * text_scale))
    meta_min = max(10, round(18 * text_scale))
    meta_max_width = max(30, round((BANNER_TEXT_RIGHT - RELEASE_RECT[0] - BANNER_TEXT_PADDING_X) * sx))
    if release_text:
        release_font = _fit_single_line_font(draw, release_text, options, base_size=meta_base, min_size=meta_min, max_width=meta_max_width, bold=False, scale=text_scale)
        _uwuvci_layered_text(draw, pos(RELEASE_RECT), release_text, release_font, bold=False, scale=text_scale)
    if players_text:
        players_font = _fit_single_line_font(draw, players_text, options, base_size=meta_base, min_size=meta_min, max_width=meta_max_width, bold=False, scale=text_scale)
        _uwuvci_layered_text(draw, pos(PLAYERS_RECT), players_text, players_font, bold=False, scale=text_scale)
    return canvas.convert("RGB")


def _render_banner_tv(image: Image.Image, info: RomInfo, options: ExportOptions) -> Image.Image:
    return _render_banner(image, info, options, TV_SIZE, getattr(info, "tv_focus_x", 50), getattr(info, "tv_focus_y", 50), getattr(info, "tv_zoom", 100))


def _render_banner_drc(image: Image.Image, info: RomInfo, options: ExportOptions) -> Image.Image:
    return _render_banner(image, info, options, DRC_SIZE, getattr(info, "gamepad_focus_x", 50), getattr(info, "gamepad_focus_y", 50), getattr(info, "gamepad_zoom", 100))


def make_icon_tex(image: Image.Image, info: RomInfo, options: ExportOptions | None = None) -> Image.Image:
    """Create the Icon Image using the bundled 128x128 UWUVCI-style icon border when available.

    The selected screenshot is cropped and sized to fill the designated placeholder area inside
    the corresponding icon template. Screenshot only explicitly bypasses templates and uses the
    processed title screenshot with aspect-preserving sizing and black letterboxing as needed.
    """
    template_path = _template_path(_icon_template_name(info), options)
    if template_path and template_path.exists():
        template = Image.open(template_path).convert("RGBA")
        mask, bbox = _largest_placeholder_region_mask(template)
        if mask is not None and bbox is not None:
            mask, bbox = _expand_placeholder_for_inner_seams(template, mask, bbox)
            x0, y0, x1, y1 = bbox
            shot = _cover_fit(
                image, (x1 - x0, y1 - y0),
                getattr(info, "icon_focus_x", 50), getattr(info, "icon_focus_y", 50), getattr(info, "icon_zoom", 100),
                getattr(info, "icon_fill_mode", "Black"), getattr(info, "icon_fill_color", "#000000"),
            )
            canvas = Image.new("RGBA", template.size, (0, 0, 0, 0))
            canvas.paste(shot, (x0, y0), mask.crop((x0, y0, x1, y1)))
            # Remove any light inner seam pixels from the overlay so the filled screenshot shows cleanly.
            overlay = template.copy()
            opx = overlay.load()
            mpx = mask.load()
            for yy in range(template.height):
                for xx in range(template.width):
                    if mpx[xx, yy] > 0 and _is_light_neutral(opx[xx, yy]):
                        opx[xx, yy] = (0, 0, 0, 0)
            canvas.alpha_composite(overlay, (0, 0))
            return canvas.convert("RGB")
    return _contain_fit_zoom(
        image, ICON_SIZE, getattr(info, "icon_focus_x", 50), getattr(info, "icon_focus_y", 50), getattr(info, "icon_zoom", 100),
        getattr(info, "icon_fill_mode", "Black"), getattr(info, "icon_fill_color", "#000000"),
    ).convert("RGB")


def render_icon_from_source(
    source_path: Path,
    info: RomInfo,
    options: ExportOptions,
    destination: Path,
) -> Path:
    """Render only the standard Icon output from an independently selected source image."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as raw:
        cropped = apply_screen_crop(raw.convert("RGB"), options.screen_crop)
        make_icon_tex(cropped, info, options).save(destination, optimize=True)
    return destination


def render_banners_from_source(
    source_path: Path,
    info: RomInfo,
    options: ExportOptions,
    output_directory: Path,
    *,
    create_tv: bool = True,
    create_gamepad: bool = True,
) -> list[Path]:
    """Render only TV/Gamepad outputs from an independently selected source image."""
    output_directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with Image.open(source_path) as raw:
        cropped = apply_screen_crop(raw.convert("RGB"), options.screen_crop)
        if create_tv:
            destination = output_directory / TV_IMAGE_NAME
            _render_banner_tv(cropped, info, options).save(destination, optimize=True)
            written.append(destination)
        if create_gamepad:
            destination = output_directory / GAMEPAD_IMAGE_NAME
            _render_banner_drc(cropped, info, options).save(destination, optimize=True)
            written.append(destination)
    return written


def make_metadata_text(info: RomInfo, sample: FrameSample | None = None) -> str:
    def value(text: str) -> str:
        text = (text or "").strip()
        return text if text else "Not available"

    source_type = "Imported image" if (info.backend or "").lower() == "image" else "ROM"
    lines = [
        "UWUVCI Game Metadata",
        "====================",
        "",
        "Copy these values into UWUVCI if manual adjustment is needed:",
        "",
        f"Game Name: {_clean_display_title(info)}",
        f"Console: {_system_label(info.system)}",
        f"Release Year: {value(getattr(info, 'release_year', ''))}",
        f"Players: {value(getattr(info, 'players', ''))}",
        "",
        "Additional Reference Information",
        "--------------------------------",
        f"Product Code: {value(info.product_code)}",
        f"Region: {value(info.region)}",
        f"Developer: {value(getattr(info, 'developer', ''))}",
        f"Publisher: {value(getattr(info, 'publisher', ''))}",
        f"Metadata Source: {value(getattr(info, 'metadata_source', ''))}",
        f"Input Type: {source_type}",
        f"Input File: {info.path.name}",
    ]
    if sample is not None:
        if source_type == "ROM":
            lines.append(f"Selected Capture Time: {sample.timestamp:.2f} seconds")
        if sample.screen_label:
            lines.append(f"Selected Capture Screen: {sample.screen_label}")
        else:
            source_area = str(
                getattr(info, "source_area_selection", getattr(info, "nds_screen_selection", "auto")) or "auto"
            ).strip().lower()
            if source_area in {"full", "top", "bottom", "left", "right"}:
                label = {
                    "full": "Full Image",
                    "top": "Top",
                    "bottom": "Bottom",
                    "left": "Left",
                    "right": "Right",
                }[source_area]
                lines.append(f"Selected Source Area: {label}")
    else:
        lines.append("Title Screen: Not captured/imported yet")
    lines.extend([
        "",
        "Wii U VC Image Builder output files:",
        "title_screen.png",
        ICON_IMAGE_NAME,
        TV_IMAGE_NAME,
        GAMEPAD_IMAGE_NAME,
        "metadata.txt",
        "",
    ])
    return "\n".join(lines)


def export_metadata_only(info: RomInfo, options: ExportOptions, sample: FrameSample | None = None) -> Path:
    """Write metadata immediately, even before a ROM has been captured."""
    out = output_directory(info, options)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metadata.txt").write_text(make_metadata_text(info, sample), encoding="utf-8")
    return out


def export_selected(info: RomInfo, sample: FrameSample, candidates: list[FrameSample], options: ExportOptions, banner_sample: FrameSample | None = None) -> Path:
    out = output_directory(info, options)
    out.mkdir(parents=True, exist_ok=True)

    try:
        old_candidates = out / "candidates"
        if old_candidates.exists() and old_candidates.is_dir():
            import shutil
            shutil.rmtree(old_candidates)
    except OSError:
        pass

    # Remove legacy filenames and stale optional outputs so the folder always reflects
    # the current checkbox selection.
    for stale_name in LEGACY_OUTPUT_NAMES + [ICON_IMAGE_NAME, TV_IMAGE_NAME, GAMEPAD_IMAGE_NAME]:
        try:
            (out / stale_name).unlink(missing_ok=True)
        except OSError:
            pass

    with Image.open(sample.path) as raw:
        cropped = apply_screen_crop(raw.convert("RGB"), options.screen_crop)
        cropped.save(out / "title_screen.png", optimize=True)
        banner_image = cropped
        if banner_sample is not None:
            try:
                with Image.open(banner_sample.path) as banner_raw:
                    banner_image = banner_raw.convert("RGB")
            except Exception:
                banner_image = cropped
        if options.create_icon:
            make_icon_tex(cropped, info, options).save(out / ICON_IMAGE_NAME, optimize=True)
        if options.create_tv:
            tv = _render_banner_tv(banner_image, info, options)
            tv.save(out / TV_IMAGE_NAME, optimize=True)
        if options.create_gamepad:
            drc = _render_banner_drc(banner_image, info, options)
            drc.save(out / GAMEPAD_IMAGE_NAME, optimize=True)

    (out / "metadata.txt").write_text(make_metadata_text(info, sample), encoding="utf-8")
    return out
