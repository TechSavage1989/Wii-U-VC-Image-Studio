from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


@dataclass
class FrameSample:
    path: Path
    timestamp: float
    score: float = 0.0
    stability: float = 0.0
    complexity: float = 0.0
    brightness: float = 0.0
    edge_strength: float = 0.0
    duplicate_hash: str = ""
    screen_label: str = ""
    title_likelihood: float = 0.0


def _analysis_image(image: Image.Image, size=(256, 192)) -> np.ndarray:
    image = image.convert("RGB")
    image = _trim_uniform_dark_border(image)
    image.thumbnail(size, Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", size, (0, 0, 0))
    x = (size[0] - image.width) // 2
    y = (size[1] - image.height) // 2
    canvas.paste(image, (x, y))
    return np.asarray(canvas, dtype=np.float32)


def _trim_uniform_dark_border(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    if arr.shape[0] < 16 or arr.shape[1] < 16:
        return image
    gray = arr.mean(axis=2)
    top, bottom = 0, gray.shape[0]
    left, right = 0, gray.shape[1]

    def dark_uniform_row(row: np.ndarray) -> bool:
        return row.mean() < 12 and row.std() < 8

    def dark_uniform_col(col: np.ndarray) -> bool:
        return col.mean() < 12 and col.std() < 8

    max_y = int(gray.shape[0] * 0.22)
    max_x = int(gray.shape[1] * 0.22)
    while top < max_y and dark_uniform_row(gray[top]):
        top += 1
    while bottom > gray.shape[0] - max_y and dark_uniform_row(gray[bottom - 1]):
        bottom -= 1
    while left < max_x and dark_uniform_col(gray[:, left]):
        left += 1
    while right > gray.shape[1] - max_x and dark_uniform_col(gray[:, right - 1]):
        right -= 1
    if right - left < image.width * 0.5 or bottom - top < image.height * 0.5:
        return image
    return image.crop((left, top, right, bottom))


def _ahash(arr: np.ndarray, bits=16) -> str:
    gray = arr.mean(axis=2)
    im = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).resize((bits, bits), Image.Resampling.BILINEAR)
    g = np.asarray(im, dtype=np.float32)
    mask = g > g.mean()
    packed = np.packbits(mask.reshape(-1).astype(np.uint8))
    return packed.tobytes().hex()


def _hash_distance(a: str, b: str) -> int:
    if not a or not b:
        return 999
    ba = bytes.fromhex(a)
    bb = bytes.fromhex(b)
    return sum((x ^ y).bit_count() for x, y in zip(ba, bb))


def _frame_features(arr: np.ndarray) -> tuple[float, float, float, float]:
    gray = arr.mean(axis=2)
    brightness = float(gray.mean() / 255.0)
    std = float(gray.std() / 72.0)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(gray[:, 1:] - gray[:, :-1])
    gy[1:, :] = np.abs(gray[1:, :] - gray[:-1, :])
    grad = (gx + gy) * 0.5
    edge = float(np.clip(grad.mean() / 42.0, 0.0, 1.0))
    complexity = float(np.clip(0.58 * np.clip(std, 0, 1) + 0.42 * edge, 0.0, 1.0))

    # Title logos and menu text tend to put a meaningful amount of high-contrast
    # structure in the central 70% of a screen. This is deliberately a weak
    # signal so illustrated title screens are not rejected.
    h, w = gray.shape
    y1, y2 = int(h * 0.15), int(h * 0.85)
    x1, x2 = int(w * 0.15), int(w * 0.85)
    center_grad = float(grad[y1:y2, x1:x2].mean()) if y2 > y1 and x2 > x1 else float(grad.mean())
    center_edge = float(np.clip(center_grad / 42.0, 0.0, 1.0))
    return brightness, complexity, edge, center_edge


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    mad = float(np.abs(a - b).mean() / 255.0)
    return float(np.clip(1.0 - mad * 3.2, 0.0, 1.0))


def score_samples(samples: Iterable[FrameSample]) -> list[FrameSample]:
    samples = list(samples)
    if not samples:
        return []
    arrays: list[np.ndarray | None] = []
    valid_samples: list[FrameSample] = []
    for sample in samples:
        try:
            with Image.open(sample.path) as im:
                arr = _analysis_image(im)
            arrays.append(arr)
            valid_samples.append(sample)
        except Exception:
            arrays.append(None)
            valid_samples.append(sample)

    max_t = max((s.timestamp for s in samples), default=1.0) or 1.0
    for i, (sample, arr) in enumerate(zip(valid_samples, arrays)):
        if arr is None:
            sample.score = -1.0
            continue
        brightness, complexity, edge, center_edge = _frame_features(arr)
        sample.brightness = brightness
        sample.complexity = complexity
        sample.edge_strength = edge
        sample.duplicate_hash = _ahash(arr)
        sims = []
        if i > 0 and arrays[i - 1] is not None:
            sims.append(_similarity(arr, arrays[i - 1]))
        if i + 1 < len(arrays) and arrays[i + 1] is not None:
            sims.append(_similarity(arr, arrays[i + 1]))
        sample.stability = sum(sims) / len(sims) if sims else 0.0

        late = min(max(sample.timestamp / max_t, 0.0), 1.0)
        blank_penalty = 0.0
        if brightness < 0.025 or brightness > 0.985:
            blank_penalty += 0.75
        if complexity < 0.06:
            blank_penalty += 0.45

        sample.title_likelihood = float(
            0.46 * sample.stability
            + 0.27 * complexity
            + 0.11 * edge
            + 0.10 * center_edge
            + 0.06 * late
            - blank_penalty
        )
        # Keep score compatible with the rest of the application.
        sample.score = sample.title_likelihood

    return samples


def _select_ranked(scored: Iterable[FrameSample], count: int) -> list[FrameSample]:
    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    selected: list[FrameSample] = []
    for sample in ranked:
        if sample.score < -0.2:
            continue
        duplicate = False
        for chosen in selected:
            if sample.screen_label == chosen.screen_label and abs(chosen.timestamp - sample.timestamp) < 0.7:
                duplicate = True
                break
            if sample.screen_label == chosen.screen_label and _hash_distance(chosen.duplicate_hash, sample.duplicate_hash) <= 10:
                duplicate = True
                break
        if not duplicate:
            selected.append(sample)
        if len(selected) >= count:
            break
    if not selected and ranked:
        selected = ranked[: min(count, len(ranked))]
    return selected


def select_candidates(samples: Iterable[FrameSample], count: int = 6) -> list[FrameSample]:
    return _select_ranked(score_samples(samples), count)


def _trim_flat_edges(image: Image.Image) -> Image.Image:
    """Conservatively remove separator/letterbox bands from one DS screen crop."""
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    if arr.shape[0] < 24 or arr.shape[1] < 24:
        return image
    gray = arr.mean(axis=2)
    top, bottom, left, right = 0, gray.shape[0], 0, gray.shape[1]
    max_y = int(gray.shape[0] * 0.10)
    max_x = int(gray.shape[1] * 0.10)

    def flat(v: np.ndarray) -> bool:
        return v.std() < 4.0 and (v.mean() < 18.0 or v.mean() > 237.0)

    while top < max_y and flat(gray[top]):
        top += 1
    while bottom > gray.shape[0] - max_y and flat(gray[bottom - 1]):
        bottom -= 1
    while left < max_x and flat(gray[:, left]):
        left += 1
    while right > gray.shape[1] - max_x and flat(gray[:, right - 1]):
        right -= 1
    if right - left < image.width * 0.75 or bottom - top < image.height * 0.75:
        return image
    return image.crop((left, top, right, bottom))


def split_nds_frame(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """Split a libretro NDS composite into the two DS screens.

    melonDS/DeSmuME layouts are normally either vertical (top/bottom) or
    horizontal (left/right). The orientation is inferred from the composite
    aspect ratio; each half is then conservatively stripped of flat separator
    bands. Returned labels describe physical placement in the captured layout.
    """
    image = image.convert("RGB")
    # Do not trim the composite before splitting. A legitimate DS screen can be
    # almost entirely black during a title sequence; trimming the composite can
    # accidentally remove part of that screen and shift the split point.
    w, h = image.size
    if w < 2 or h < 2:
        return [("full", image)]

    # Two 4:3 DS screens are ~2.67:1 side-by-side and ~0.67:1 stacked.
    # Use 1.35 as a forgiving boundary for layouts with gaps/borders.
    if (w / h) >= 1.35:
        mid = w // 2
        parts = [("left", image.crop((0, 0, mid, h))), ("right", image.crop((mid, 0, w, h)))]
    else:
        mid = h // 2
        parts = [("top", image.crop((0, 0, w, mid))), ("bottom", image.crop((0, mid, w, h)))]
    return [(label, _trim_flat_edges(part)) for label, part in parts]


def select_nds_candidates(samples: Iterable[FrameSample], work_dir: Path, count: int = 6) -> list[FrameSample]:
    """Create and rank single-screen NDS candidates instead of composite frames."""
    source = list(samples)
    if not source:
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    streams: dict[str, list[FrameSample]] = {}

    for index, sample in enumerate(source, start=1):
        try:
            with Image.open(sample.path) as im:
                parts = split_nds_frame(im)
            for label, part in parts:
                p = work_dir / f"nds_{label}_{index:04d}.png"
                part.save(p)
                streams.setdefault(label, []).append(FrameSample(p, sample.timestamp, screen_label=label))
        except Exception:
            continue

    pooled: list[FrameSample] = []
    for stream in streams.values():
        pooled.extend(score_samples(stream))
    return _select_ranked(pooled, count)
