import json
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from microscope.mainwindow import MainWindow
from microscope.capture import CaptureThread
from microscope.calibration import CalibrationStore, Calibration
from microscope.useeplus import BUTTON_FLAG
from microscope.view import Tool


def _app():
    return QApplication.instance() or QApplication([])


def _no_device():
    raise RuntimeError("none")


def make_win(tmp_path):
    _app()
    cap = CaptureThread(camera_factory=_no_device)  # never started in tests
    store = CalibrationStore(tmp_path / "cal.json")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(cap, store=store, output_dir=tmp_path / "out", settings=settings)
    return w, store


def frame(v=0):
    return np.full((480, 640, 3), v, np.uint8)


def test_frame_is_processed_rotated_and_displayed(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(10), 0)
    assert w.last_frame.shape == (480, 640, 3)          # default rotation 180 keeps landscape
    assert w.view.sceneRect().width() == 640 and w.view.sceneRect().height() == 480


def test_snapshot_writes_png_and_sidecar(tmp_path):
    w, store = make_win(tmp_path)
    store.add(Calibration("4x", 2.0)); store.set_active("4x"); w.refresh_calibrations()
    w.on_frame(frame(77), 0)
    p = w.snapshot()
    assert p.exists() and p.suffix == ".png"
    side = json.loads(p.with_suffix(".json").read_text())
    assert side["calibration"] == {"name": "4x", "um_per_px": 2.0}
    assert side["params"]["rotation"] == 180 and side["measurements"] == []


def test_button_rising_edge_triggers_one_snapshot(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(), 0)
    w.on_frame(frame(), BUTTON_FLAG)
    w.on_frame(frame(), BUTTON_FLAG)   # held: no second snapshot
    w.on_frame(frame(), 0)
    assert len(list((tmp_path / "out").glob("*.png"))) == 1


def test_record_toggle_writes_file(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(), 0)
    w.toggle_record()
    assert w.recorder.is_recording
    for i in range(5):
        w.on_frame(frame(i), 0)
    w.toggle_record()
    mp4s = list((tmp_path / "out").glob("*.mp4"))
    assert len(mp4s) == 1 and w.recorder.frames_written == 5


def test_freeze_ignores_frames(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(1), 0)
    w.freeze_cb.setChecked(True)
    w.on_frame(frame(200), 0)
    assert w.last_frame[0, 0, 0] == 1


def test_params_persist_in_settings(tmp_path):
    w, _ = make_win(tmp_path)
    w.brightness.setValue(33)
    w.close()
    w2, _ = make_win(tmp_path)
    assert w2.params.brightness == 33


def test_calibration_wizard_cancelled_by_tool_change_does_not_hijack_later_line(tmp_path):
    w, store = make_win(tmp_path)
    w.on_frame(frame(), 0)
    w.start_calibration()
    assert w._calibrating
    w._on_tool(Tool.LINE)             # user switches tool → wizard cancelled
    assert not w._calibrating
    w.view.tool_press(0, 0); w.view.tool_release(30, 40)   # ordinary measurement
    assert store.calibrations == [] and len(w.view.measurements()) == 1


def test_calibration_wizard_start_twice_is_idempotent(tmp_path):
    w, _ = make_win(tmp_path)
    w.start_calibration(); w.start_calibration()
    assert w._calibrating
    w._cancel_calibration()
    assert not w._calibrating


def test_record_uses_measured_fps(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(), 0)
    w._on_fps(12.5)
    w.toggle_record()
    for i in range(4): w.on_frame(frame(i), 0)
    w.toggle_record()
    import cv2
    cap = cv2.VideoCapture(str(next((tmp_path / "out").glob("*.mp4"))))
    assert round(cap.get(cv2.CAP_PROP_FPS), 1) == 12.5
    cap.release()


def test_param_change_while_frozen_reprocesses_frozen_frame(tmp_path):
    w, _ = make_win(tmp_path)
    w.on_frame(frame(100), 0)
    w.freeze_cb.setChecked(True)
    w.brightness.setValue(50)
    assert w.last_frame[0, 0, 0] == 150


def test_calibration_wizard_survives_dropdown_change(tmp_path):
    w, store = make_win(tmp_path)
    store.add(Calibration("4x", 2.0)); w.refresh_calibrations()
    w.on_frame(frame(), 0)
    w.start_calibration()
    w.cal_combo.setCurrentIndex(1)         # emits measurementsChanged via set_calibration
    assert w._calibrating                  # wizard still armed
    w._cancel_calibration()
