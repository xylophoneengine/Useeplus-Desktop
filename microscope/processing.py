"""Pure image adjustments. BGR uint8 in, BGR uint8 out. No Qt."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache

import cv2
import numpy as np

DEFAULT_ROTATION = 90  # sensor is mounted sideways; flip to 270 here if the smoke test shows it upside down


@dataclass
class Params:
    rotation: int = DEFAULT_ROTATION   # 0, 90, 180, 270 (clockwise)
    flip_h: bool = False
    flip_v: bool = False
    brightness: int = 0                # -100..100, added
    contrast: float = 1.0              # 0.5..3.0, multiplied around 128
    gamma: float = 1.0                 # 0.3..3.0
    sharpen: float = 0.0               # 0..2, unsharp-mask amount

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@lru_cache(maxsize=32)
def _lut(brightness: int, contrast: float, gamma: float) -> np.ndarray:
    x = np.arange(256, dtype=np.float32)
    y = (x - 128.0) * contrast + 128.0 + brightness
    y = np.clip(y, 0, 255) / 255.0
    y = np.power(y, 1.0 / gamma) * 255.0
    return np.clip(y, 0, 255).astype(np.uint8)


def apply(frame: np.ndarray, p: Params) -> np.ndarray:
    out = frame
    if p.rotation in _ROT:
        out = cv2.rotate(out, _ROT[p.rotation])
    if p.flip_h:
        out = out[:, ::-1]
    if p.flip_v:
        out = out[::-1]
    if p.brightness or p.contrast != 1.0 or p.gamma != 1.0:
        out = cv2.LUT(np.ascontiguousarray(out), _lut(p.brightness, p.contrast, p.gamma))
    if p.sharpen > 0:
        blur = cv2.GaussianBlur(out, (0, 0), 1.5)
        out = cv2.addWeighted(out, 1.0 + p.sharpen, blur, -p.sharpen, 0)
    return np.ascontiguousarray(out) if out is not frame else frame.copy()
