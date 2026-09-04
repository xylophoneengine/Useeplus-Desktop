import json
import pytest
from microscope.calibration import Calibration, CalibrationStore, compute_um_per_px


def test_compute():
    assert compute_um_per_px(200.0, 1000.0) == 5.0
    with pytest.raises(ValueError):
        compute_um_per_px(0, 100)
    with pytest.raises(ValueError):
        compute_um_per_px(10, -1)


def test_missing_file_is_empty(tmp_path):
    s = CalibrationStore(tmp_path / "c.json")
    assert s.calibrations == [] and s.active is None and s.warning is None


def test_add_replace_remove_active_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    s = CalibrationStore(p)
    s.add(Calibration("4x", 2.0))
    s.add(Calibration("10x", 0.8))
    s.add(Calibration("4x", 2.1))          # replace
    s.set_active("10x")
    s.save()
    t = CalibrationStore(p)
    assert [c.name for c in t.calibrations] == ["4x", "10x"]
    assert t.calibrations[0].um_per_px == 2.1
    assert t.active == Calibration("10x", 0.8)
    t.remove("10x")
    assert t.active is None and len(t.calibrations) == 1


def test_corrupt_file_renamed(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    s = CalibrationStore(p)
    assert s.calibrations == [] and s.warning
    assert (tmp_path / "c.json.bad").exists() and not p.exists()


def test_save_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "dir" / "c.json"
    s = CalibrationStore(p)
    s.add(Calibration("a", 1.0))
    s.save()
    assert json.loads(p.read_text())["calibrations"][0]["name"] == "a"
