"""MP4 recording via cv2.VideoWriter. Fixed fps; no Qt."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class Recorder:
    def __init__(self) -> None:
        self._w: cv2.VideoWriter | None = None
        self._size: tuple[int, int] = (0, 0)
        self.path: Path | None = None
        self.frames_written = 0

    @property
    def is_recording(self) -> bool:
        return self._w is not None

    def start(self, path: Path, size: tuple[int, int], fps: float = 16.0) -> None:
        # ponytail: constant-fps mux; switch to an ffmpeg pipe with real timestamps if A/V drift matters
        w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        if not w.isOpened():
            raise RuntimeError(f"could not open video writer for {path}")
        self._w, self._size, self.path, self.frames_written = w, size, Path(path), 0

    def write(self, frame: np.ndarray) -> None:
        if self._w is None:
            return
        h, w = frame.shape[:2]
        if (w, h) != self._size:
            frame = cv2.resize(frame, self._size)
        self._w.write(frame)
        self.frames_written += 1

    def stop(self) -> Path | None:
        if self._w is None:
            return None
        self._w.release()
        self._w = None
        return self.path
