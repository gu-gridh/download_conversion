"""Fetch annotation lists and render them onto converted images.

Supported selector types (chosen by ``target.selector.type``):

- ``SvgSelector`` (Munch): ``<polygon>``, ``<polygon data-point="true">`` and
  ``<polyline>`` with points normalised to 0–1 of the image size.
- ``FragmentSelector`` (Saint Sophia): ``xywh=percent:x,y,w,h`` with values 0–100.
"""

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageColor, ImageDraw

logger = logging.getLogger(__name__)

DEFAULT_COLOR = "#ff0000"
RECT_COLOR = "#ffffff"
POINT_FILL_OPACITY = 0.35
# CSS grayscale(1) uses Rec. 709 luma; brightness(0.45) scales it.
FOCUS_BRIGHTNESS = 0.45
FOCUS_MATRIX = (0.2126 * FOCUS_BRIGHTNESS, 0.7152 * FOCUS_BRIGHTNESS, 0.0722 * FOCUS_BRIGHTNESS, 0)
# Stroke widths are in "screen pixels" for a ~1000px image and scale up for larger output.
STROKE_REFERENCE_SIZE = 1000
STROKE_WIDTH = 1.5
DASH_LENGTH = 1.0
# Point annotations are tiny in source coordinates; keep them visible.
MIN_POINT_RADIUS = 3.0
MAX_PAGES = 1000

FRAGMENT_PATTERN = re.compile(
    r"^xywh=percent:\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*$",
    re.IGNORECASE,
)

# A shape is a plain dict so it can be hashed into a cache key:
#   {"kind": "polygon" | "point" | "polyline", "points": [[x, y], ...], "color": "#rrggbb"}   (0–1 coordinates)
#   {"kind": "rect", "x": .., "y": .., "w": .., "h": ..}                                       (0–1 coordinates)
Shape = Dict


def fetch_annotations(url: str, timeout: int = 60) -> List[Dict]:
    """Fetch every annotation from ``url``, following ``next`` pagination links."""
    items: List[Dict] = []
    next_url: Optional[str] = url
    seen = set()
    while next_url and len(seen) < MAX_PAGES:
        if next_url in seen:
            break
        seen.add(next_url)
        try:
            response = requests.get(next_url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ValueError(f"Failed to fetch annotations from {next_url}: {exc}") from exc

        if isinstance(payload, list):
            items.extend(payload)
            next_url = None
        elif isinstance(payload, dict):
            page_items = payload.get("results")
            if page_items is None:
                page_items = payload.get("items", [])
            items.extend(page_items or [])
            next_url = payload.get("next")
        else:
            raise ValueError(f"Unexpected annotation payload type from {next_url}: {type(payload).__name__}")
    return items


def _category_color(annotation: Dict) -> str:
    body = annotation.get("body")
    bodies = body if isinstance(body, list) else [body]
    for entry in bodies:
        if not isinstance(entry, dict):
            continue
        categories = entry.get("categories") or []
        if categories and isinstance(categories[0], dict):
            color = categories[0].get("color")
            if color:
                try:
                    ImageColor.getrgb(color)
                    return color
                except ValueError:
                    logger.warning("Ignoring invalid annotation color %r", color)
    return DEFAULT_COLOR


def _parse_points(value: str) -> List[List[float]]:
    numbers = [float(n) for n in re.split(r"[\s,]+", value.strip()) if n]
    return [[numbers[i], numbers[i + 1]] for i in range(0, len(numbers) - 1, 2)]


def _parse_svg_selector(value: str, color: str) -> List[Shape]:
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        logger.warning("Skipping unparsable SvgSelector: %s", exc)
        return []

    shapes: List[Shape] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("polygon", "polyline"):
            continue
        points = _parse_points(element.get("points", ""))
        if len(points) < 2:
            continue
        if tag == "polyline":
            kind = "polyline"
        elif element.get("data-point", "").lower() == "true":
            kind = "point"
        else:
            kind = "polygon"
        shapes.append({"kind": kind, "points": points, "color": color})
    return shapes


def _parse_fragment_selector(value: str) -> List[Shape]:
    match = FRAGMENT_PATTERN.match(value or "")
    if not match:
        logger.warning("Skipping unsupported FragmentSelector value %r", value)
        return []
    x, y, w, h = (float(v) / 100.0 for v in match.groups())
    return [{"kind": "rect", "x": x, "y": y, "w": w, "h": h}]


def parse_annotations(annotations: List[Dict]) -> List[Shape]:
    """Turn raw W3C-style annotations into drawable shapes in 0–1 coordinates."""
    shapes: List[Shape] = []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        targets = annotation.get("target")
        for target in targets if isinstance(targets, list) else [targets]:
            if not isinstance(target, dict):
                continue
            selectors = target.get("selector")
            for selector in selectors if isinstance(selectors, list) else [selectors]:
                if not isinstance(selector, dict):
                    continue
                selector_type = selector.get("type")
                value = selector.get("value") or ""
                if selector_type == "SvgSelector":
                    shapes.extend(_parse_svg_selector(value, _category_color(annotation)))
                elif selector_type == "FragmentSelector":
                    shapes.extend(_parse_fragment_selector(value))
                else:
                    logger.warning("Skipping unsupported selector type %r", selector_type)
    return shapes


def render_variant_key(shapes: List[Shape], focus: bool) -> Optional[str]:
    """Stable key for an annotated/focused rendering, or None for the plain image."""
    if not shapes and not focus:
        return None
    digest = hashlib.sha256(json.dumps({"focus": bool(focus), "shapes": shapes}, sort_keys=True).encode()).hexdigest()[:16]
    return f"{'focus-' if focus else ''}{digest}"


def apply_focus(img: Image.Image) -> Image.Image:
    """Equivalent of CSS ``grayscale(1) brightness(0.45)``."""
    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
    gray = img.convert("RGB").convert("L", FOCUS_MATRIX).convert("RGB")
    if alpha is not None:
        gray.putalpha(alpha)
    return gray


def _rgba(color: str, opacity: float = 1.0) -> Tuple[int, int, int, int]:
    r, g, b = ImageColor.getrgb(color)[:3]
    return r, g, b, round(255 * opacity)


def _dashed_line(draw: ImageDraw.ImageDraw, start, end, dash: float, fill, width: int):
    (x0, y0), (x1, y1) = start, end
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if length == 0:
        return
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        draw.line([(x0 + dx * pos, y0 + dy * pos), (x0 + dx * seg_end, y0 + dy * seg_end)], fill=fill, width=width)
        pos += dash * 2


def draw_annotations(img: Image.Image, shapes: List[Shape]) -> Image.Image:
    """Draw shapes on ``img`` using its actual (already resized) dimensions."""
    if not shapes:
        return img
    width, height = img.size
    scale = max(1.0, max(width, height) / STROKE_REFERENCE_SIZE)
    stroke = max(1, round(STROKE_WIDTH * scale))
    dash = max(1.0, DASH_LENGTH * scale)
    min_radius = MIN_POINT_RADIUS * scale

    original_mode = img.mode
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for shape in shapes:
        kind = shape["kind"]
        if kind == "rect":
            x0, y0 = shape["x"] * width, shape["y"] * height
            x1, y1 = x0 + shape["w"] * width, y0 + shape["h"] * height
            fill = _rgba(RECT_COLOR)
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            for i in range(4):
                _dashed_line(draw, corners[i], corners[(i + 1) % 4], dash, fill, stroke)
            continue

        points = [(x * width, y * height) for x, y in shape["points"]]
        color = shape["color"]
        if kind == "polyline":
            draw.line(points, fill=_rgba(color), width=stroke, joint="curve")
        elif kind == "polygon":
            draw.line(points + [points[0]], fill=_rgba(color), width=stroke, joint="curve")
        elif kind == "point":
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            if max(xs) - min(xs) < 2 * min_radius and max(ys) - min(ys) < 2 * min_radius:
                cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                draw.ellipse(
                    [cx - min_radius, cy - min_radius, cx + min_radius, cy + min_radius],
                    fill=_rgba(color, POINT_FILL_OPACITY),
                    outline=_rgba(color),
                    width=stroke,
                )
            else:
                draw.polygon(points, fill=_rgba(color, POINT_FILL_OPACITY), outline=_rgba(color), width=stroke)

    composed = Image.alpha_composite(base, overlay)
    return composed if original_mode == "RGBA" else composed.convert("RGB")
