import math
import pytest
from microscope import measure as m


def test_length_345():
    assert m.length((0, 0), (3, 4)) == 5.0


def test_polyline_and_area():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert m.polyline_length(sq) == 30.0
    assert m.polygon_area(sq) == 100.0
    assert m.polygon_area(list(reversed(sq))) == 100.0
    assert m.polygon_area([(0, 0), (1, 1)]) == 0.0


def test_angle():
    assert m.angle((1, 0), (0, 0), (0, 1)) == pytest.approx(90.0)
    assert m.angle((1, 0), (0, 0), (-1, 0)) == pytest.approx(180.0)
    assert m.angle((1, 0), (0, 0), (1, 0)) == pytest.approx(0.0)


def test_units():
    assert m.to_um(10, 2.5) == 25.0
    assert m.to_um2(10, 2.0) == 40.0
    assert m.to_um(10, None) is None
    assert m.format_length(57, None) == "57.0 px"
    assert m.format_length(100, 1.234) == "123.4 µm"
    assert m.format_length(1000, 1.234) == "1.23 mm"
    assert m.format_area(100, 1.0) == "100 µm²"
    assert m.format_area(2_000_000, 1.0) == "2.00 mm²"


def test_scale_bar_nice_numbers():
    # 1 µm/px, 640 px wide -> target 128 µm -> nice 100 µm
    bar_px, label = m.scale_bar(1.0, 640)
    assert bar_px == 100.0 and label == "100 µm"
    # 0.37 µm/px -> target 47 µm -> 50 µm -> 135.1 px
    bar_px, label = m.scale_bar(0.37, 640)
    assert label == "50 µm" and bar_px == pytest.approx(50 / 0.37)
    bar_px, label = m.scale_bar(12.0, 640)   # target 1536 µm -> 2 mm
    assert label == "2 mm"
