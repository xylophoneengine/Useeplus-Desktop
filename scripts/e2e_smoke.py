"""Orchestrator-only hardware smoke: builds the real app offscreen, streams from the attached
microscope for 5 s, prints connection events / frame count / clean-stop status. Exits 0 on success."""
import sys, time, faulthandler
faulthandler.dump_traceback_later(30, exit=True)
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from microscope.capture import CaptureThread
from microscope.mainwindow import MainWindow
app = QApplication(sys.argv)
cap = CaptureThread()
win = MainWindow(cap); win.show()
ev = []; nframes = [0]; t0 = time.time()
cap.connected.connect(lambda s: ev.append(("connected", s, round(time.time()-t0, 2))))
cap.disconnected.connect(lambda r: ev.append(("disconnected", r, round(time.time()-t0, 2))))
def onf(f, fl):
    nframes[0] += 1
    if nframes[0] == 1: ev.append(("first_frame", f.shape, fl, round(time.time()-t0, 2)))
cap.frameReady.connect(onf)
cap.start()
app.aboutToQuit.connect(cap.stop)
QTimer.singleShot(5000, app.quit)
rc = app.exec()
t1 = time.time()
print("events:", ev); print("frames:", nframes[0], "fps~", round(nframes[0]/4.5, 1))
print("view size:", win.view.sceneRect().width(), win.view.sceneRect().height(), "last_frame", None if win.last_frame is None else win.last_frame.shape)
print("thread finished:", cap.isFinished(), "stop took", round(time.time()-t1, 2), "s")
print("exit", rc)
