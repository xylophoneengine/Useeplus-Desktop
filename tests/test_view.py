from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from microscope.view import MicroscopeView, Tool
from microscope.calibration import Calibration


def _app():
    return QApplication.instance() or QApplication([])


def make_view():
    _app()
    v = MicroscopeView()
    v.set_frame(QImage(480, 640, QImage.Format.Format_RGB888))
    return v


def test_line_measurement_px_and_um():
    v = make_view()
    v.set_tool(Tool.LINE)
    v.tool_press(10, 10); v.tool_move(40, 50); v.tool_release(40, 50)
    m = v.measurements()
    assert len(m) == 1 and m[0]["type"] == "line" and m[0]["value_px"] == 50.0
    assert m[0]["label"] == "50.0 px"
    v.set_calibration(Calibration("x", 2.0))
    assert v.measurements()[0]["label"] == "100.0 µm"


def test_rect_area_and_polyline_and_angle():
    v = make_view()
    v.set_tool(Tool.RECT)
    v.tool_press(0, 0); v.tool_release(10, 20)
    v.set_tool(Tool.POLYLINE)
    v.tool_press(0, 0); v.tool_release(0, 0)
    v.tool_press(3, 4); v.tool_release(3, 4)
    v.tool_double_click(6, 8)
    v.set_tool(Tool.ANGLE)
    for p in ((10, 0), (0, 0), (0, 10)):
        v.tool_press(*p); v.tool_release(*p)
    m = v.measurements()
    assert [x["type"] for x in m] == ["rect", "polyline", "angle"]
    assert m[0]["value_px"] == 200.0 and m[0]["label"] == "200 px²"
    assert m[1]["value_px"] == 10.0
    assert round(m[2]["value_px"]) == 90 and m[2]["label"] == "90.0°"


def test_clear_and_signal():
    v = make_view()
    n = []
    v.measurementsChanged.connect(lambda: n.append(1))
    v.set_tool(Tool.LINE)
    v.tool_press(0, 0); v.tool_release(1, 0)
    v.clear_measurements()
    assert v.measurements() == [] and len(n) == 2


def test_scale_bar_visible_only_with_calibration():
    v = make_view()
    assert not v.scale_bar_visible()
    v.set_calibration(Calibration("x", 1.0))
    assert v.scale_bar_visible() and v.scale_bar_label() == "50 µm"
    v.set_calibration(None)
    assert not v.scale_bar_visible()


def test_double_click_on_last_vertex_does_not_duplicate():
    v = make_view()
    v.set_tool(Tool.POLYLINE)
    v.tool_press(0, 0); v.tool_release(0, 0)
    v.tool_press(3, 4); v.tool_release(3, 4)
    v.tool_press(6, 8); v.tool_release(6, 8)      # what a real double-click's first press does
    v.tool_double_click(6, 8)
    m = v.measurements()
    assert len(m) == 1 and m[0]["points"] == [[0.0, 0.0], [3.0, 4.0], [6.0, 8.0]] and m[0]["value_px"] == 10.0


def test_scale_bar_appears_when_calibration_set_before_first_frame():
    _app()
    v = MicroscopeView()
    v.set_calibration(Calibration("x", 1.0))
    assert not v.scale_bar_visible()              # no frame yet
    v.set_frame(QImage(480, 640, QImage.Format.Format_RGB888))
    assert v.scale_bar_visible() and v.scale_bar_label() == "50 µm"
