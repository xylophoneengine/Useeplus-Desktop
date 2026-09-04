import cv2
import numpy as np
import pytest
from microscope.recorder import Recorder


def test_record_roundtrip(tmp_path):
    out = tmp_path / "clip.mp4"
    r = Recorder()
    assert not r.is_recording
    r.start(out, (480, 640), fps=16.0)   # (w, h): portrait after 90° rotation
    assert r.is_recording
    for i in range(20):
        r.write(np.full((640, 480, 3), i * 10, np.uint8))
    r.write(np.zeros((480, 640, 3), np.uint8))  # wrong orientation -> resized, not dropped
    assert r.frames_written == 21
    assert r.stop() == out and not r.is_recording
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 21
    assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (480, 640)
    cap.release()


def test_write_when_idle_is_noop():
    r = Recorder()
    r.write(np.zeros((10, 10, 3), np.uint8))
    assert r.frames_written == 0 and r.stop() is None


def test_start_bad_path_raises(tmp_path):
    with pytest.raises(RuntimeError):
        Recorder().start(tmp_path / "no" / "such" / "dir" / "x.mp4", (64, 64))


def test_start_while_recording_finalizes_previous(tmp_path):
    r = Recorder()
    r.start(tmp_path / "a.mp4", (64, 64))
    for _ in range(3):
        r.write(np.zeros((64, 64, 3), np.uint8))
    r.start(tmp_path / "b.mp4", (64, 64))
    assert r.path == tmp_path / "b.mp4" and r.frames_written == 0
    cap = cv2.VideoCapture(str(tmp_path / "a.mp4"))
    assert cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 3
    cap.release()
    r.stop()
