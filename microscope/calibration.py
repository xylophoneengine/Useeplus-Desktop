"""Named µm/px presets, persisted as JSON. No Qt."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "microscope" / "calibrations.json"


@dataclass(frozen=True)
class Calibration:
    name: str
    um_per_px: float


def compute_um_per_px(px_length: float, known_um: float) -> float:
    if px_length <= 0 or known_um <= 0:
        raise ValueError("lengths must be positive")
    return known_um / px_length


class CalibrationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.calibrations: list[Calibration] = []
        self._active_name: str | None = None
        self.warning: str | None = None
        self.load()

    @property
    def active(self) -> Calibration | None:
        return next((c for c in self.calibrations if c.name == self._active_name), None)

    def add(self, cal: Calibration) -> None:
        """Replace in place if the name exists, else append."""
        for i, c in enumerate(self.calibrations):
            if c.name == cal.name:
                self.calibrations[i] = cal
                return
        self.calibrations.append(cal)

    def remove(self, name: str) -> None:
        self.calibrations = [c for c in self.calibrations if c.name != name]
        if self._active_name == name:
            self._active_name = None

    def set_active(self, name: str | None) -> None:
        self._active_name = name if any(c.name == name for c in self.calibrations) else None

    def load(self) -> None:
        self.calibrations, self._active_name = [], None
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            self.calibrations = [Calibration(c["name"], float(c["um_per_px"])) for c in d["calibrations"]]
            self._active_name = d.get("active")
        except Exception as e:  # corrupt: move aside, start empty
            bad = self.path.with_name(self.path.name + ".bad")
            self.path.replace(bad)
            self.calibrations, self._active_name = [], None
            self.warning = f"calibration file was corrupt ({e}); moved to {bad}"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"calibrations": [asdict(c) for c in self.calibrations], "active": self._active_name},
            indent=2))
