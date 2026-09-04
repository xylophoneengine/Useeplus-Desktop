"""Geometry in image pixels + µm conversion. No Qt."""
from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]


def length(p0: Point, p1: Point) -> float:
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def polyline_length(pts: Sequence[Point]) -> float:
    return sum(length(a, b) for a, b in zip(pts, pts[1:]))


def polygon_area(pts: Sequence[Point]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, list(pts[1:]) + list(pts[:1])):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def angle(a: Point, vertex: Point, b: Point) -> float:
    ax, ay = a[0] - vertex[0], a[1] - vertex[1]
    bx, by = b[0] - vertex[0], b[1] - vertex[1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na == 0 or nb == 0:
        return 0.0
    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(c))


def to_um(px: float, um_per_px: float | None) -> float | None:
    return None if um_per_px is None else px * um_per_px


def to_um2(px2: float, um_per_px: float | None) -> float | None:
    return None if um_per_px is None else px2 * um_per_px * um_per_px


def format_length(px: float, um_per_px: float | None) -> str:
    um = to_um(px, um_per_px)
    if um is None:
        return f"{px:.1f} px"
    return f"{um / 1000:.2f} mm" if um >= 1000 else f"{um:.1f} µm"


def format_area(px2: float, um_per_px: float | None) -> str:
    um2 = to_um2(px2, um_per_px)
    if um2 is None:
        return f"{px2:.0f} px²"
    return f"{um2 / 1e6:.2f} mm²" if um2 >= 1e6 else f"{um2:.0f} µm²"


def _nice(x: float) -> float:
    """Closest of {1,2,5}·10^n to x (in log space)."""
    e = math.floor(math.log10(x))
    best = min((1, 2, 5), key=lambda m: abs(math.log10(m * 10 ** e) - math.log10(x)))
    return best * 10 ** e


def scale_bar(um_per_px: float, img_w_px: int, target_frac: float = 0.2) -> tuple[float, str]:
    um = _nice(img_w_px * target_frac * um_per_px)
    label = f"{um / 1000:g} mm" if um >= 1000 else f"{um:g} µm"
    return um / um_per_px, label
