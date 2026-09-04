"""Main window: view + side panel, wiring between capture, processing, recorder, snapshots."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (QMainWindow, QWidget, QDockWidget, QVBoxLayout, QFormLayout, QGroupBox,
                               QSlider, QCheckBox, QComboBox, QPushButton, QLabel, QHBoxLayout,
                               QInputDialog, QMessageBox, QFileDialog, QButtonGroup)

from .calibration import Calibration, CalibrationStore, compute_um_per_px
from .capture import CaptureThread
from .processing import Params, apply
from .recorder import Recorder
from .useeplus import BUTTON_FLAG
from .view import MicroscopeView, Tool

DEFAULT_OUTPUT = Path.home() / "Pictures" / "Microscope"


def _to_qimage(bgr: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class MainWindow(QMainWindow):
    def __init__(self, capture: CaptureThread, store: CalibrationStore | None = None,
                 output_dir: Path | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Microscope")
        self.capture = capture
        self.store = store or CalibrationStore()
        self.settings = settings or QSettings("zwenger", "microscope")
        self.output_dir = Path(output_dir or self.settings.value("output_dir", str(DEFAULT_OUTPUT)))
        self.recorder = Recorder()
        self.params = Params.from_dict(json.loads(self.settings.value("params", "{}")))
        self.last_frame: np.ndarray | None = None
        self._button_was_down = False
        self._last_button_snap = 0.0
        self._serial = ""

        self.view = MicroscopeView()
        self.setCentralWidget(self.view)
        self._build_panel()
        self._build_status()
        self.refresh_calibrations()
        if self.store.warning:
            self.status_msg.setText(self.store.warning)

        capture.frameReady.connect(self.on_frame)
        capture.connected.connect(self._on_connected)
        capture.disconnected.connect(self._on_disconnected)
        capture.fps.connect(lambda f: self.fps_label.setText(f"{f:.1f} fps"))
        if (g := self.settings.value("geometry")) is not None:
            self.restoreGeometry(g)

    # ---- UI construction -----------------------------------------------
    def _slider(self, lo, hi, val, cb):
        s = QSlider(Qt.Orientation.Horizontal); s.setRange(lo, hi); s.setValue(val)
        s.valueChanged.connect(cb); return s

    def _build_panel(self) -> None:
        panel = QWidget(); lay = QVBoxLayout(panel)

        adj = QGroupBox("Adjust"); f = QFormLayout(adj)
        self.rotation = QComboBox(); self.rotation.addItems(["0", "90", "180", "270"])
        self.rotation.setCurrentText(str(self.params.rotation))
        self.rotation.currentTextChanged.connect(lambda t: self._set_param("rotation", int(t)))
        self.flip_h = QCheckBox("Flip H"); self.flip_h.setChecked(self.params.flip_h)
        self.flip_h.toggled.connect(lambda b: self._set_param("flip_h", b))
        self.flip_v = QCheckBox("Flip V"); self.flip_v.setChecked(self.params.flip_v)
        self.flip_v.toggled.connect(lambda b: self._set_param("flip_v", b))
        self.brightness = self._slider(-100, 100, self.params.brightness, lambda v: self._set_param("brightness", v))
        self.contrast = self._slider(50, 300, int(self.params.contrast * 100), lambda v: self._set_param("contrast", v / 100))
        self.gamma = self._slider(30, 300, int(self.params.gamma * 100), lambda v: self._set_param("gamma", v / 100))
        self.sharpen = self._slider(0, 200, int(self.params.sharpen * 100), lambda v: self._set_param("sharpen", v / 100))
        self.freeze_cb = QCheckBox("Freeze")
        reset = QPushButton("Reset"); reset.clicked.connect(self._reset_params)
        f.addRow("Rotation", self.rotation); f.addRow(self.flip_h); f.addRow(self.flip_v)
        f.addRow("Brightness", self.brightness); f.addRow("Contrast", self.contrast)
        f.addRow("Gamma", self.gamma); f.addRow("Sharpen", self.sharpen)
        f.addRow(self.freeze_cb); f.addRow(reset)
        lay.addWidget(adj)

        cal = QGroupBox("Calibration"); c = QVBoxLayout(cal)
        self.cal_combo = QComboBox(); self.cal_combo.currentIndexChanged.connect(self._on_cal_selected)
        row = QHBoxLayout()
        b_new = QPushButton("Calibrate…"); b_new.clicked.connect(self.start_calibration)
        b_del = QPushButton("Delete"); b_del.clicked.connect(self._delete_calibration)
        row.addWidget(b_new); row.addWidget(b_del)
        c.addWidget(self.cal_combo); c.addLayout(row)
        lay.addWidget(cal)

        meas = QGroupBox("Measure"); m = QHBoxLayout(meas)
        self.tool_group = QButtonGroup(self); self.tool_group.setExclusive(True)
        for name, tool in (("Pan", Tool.NONE), ("Line", Tool.LINE), ("Poly", Tool.POLYLINE),
                           ("Rect", Tool.RECT), ("Angle", Tool.ANGLE)):
            b = QPushButton(name); b.setCheckable(True); b.setChecked(tool == Tool.NONE)
            b.clicked.connect(lambda _=False, t=tool: self.view.set_tool(t))
            self.tool_group.addButton(b); m.addWidget(b)
        clear = QPushButton("Clear"); clear.clicked.connect(self.view.clear_measurements); m.addWidget(clear)
        lay.addWidget(meas)

        capg = QGroupBox("Capture"); k = QVBoxLayout(capg)
        b_snap = QPushButton("Snapshot"); b_snap.clicked.connect(self.snapshot)
        self.rec_btn = QPushButton("Record"); self.rec_btn.setCheckable(True); self.rec_btn.clicked.connect(self.toggle_record)
        self.burn_cb = QCheckBox("Also save annotated PNG")
        self.out_label = QLabel(str(self.output_dir)); self.out_label.setWordWrap(True)
        b_out = QPushButton("Output folder…"); b_out.clicked.connect(self._choose_output)
        b_fit = QPushButton("Fit"); b_fit.clicked.connect(self.view.fit)
        for w in (b_snap, self.rec_btn, self.burn_cb, self.out_label, b_out, b_fit):
            k.addWidget(w)
        lay.addWidget(capg)
        lay.addStretch(1)

        dock = QDockWidget("Controls", self); dock.setWidget(panel)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_status(self) -> None:
        self.conn_label = QLabel("No microscope found — plug in")
        self.fps_label = QLabel("— fps")
        self.rec_label = QLabel("")
        self.status_msg = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.conn_label); sb.addWidget(self.fps_label); sb.addWidget(self.rec_label)
        sb.addPermanentWidget(self.status_msg)

    # ---- params --------------------------------------------------------
    def _set_param(self, name: str, value) -> None:
        setattr(self.params, name, value)
        self.settings.setValue("params", json.dumps(self.params.to_dict()))
        self.settings.sync()
        if self.freeze_cb.isChecked() and self.last_frame is not None:
            self.view.set_frame(_to_qimage(self.last_frame))

    def _reset_params(self) -> None:
        d = Params()
        self.rotation.setCurrentText(str(d.rotation)); self.flip_h.setChecked(d.flip_h); self.flip_v.setChecked(d.flip_v)
        self.brightness.setValue(d.brightness); self.contrast.setValue(int(d.contrast * 100))
        self.gamma.setValue(int(d.gamma * 100)); self.sharpen.setValue(int(d.sharpen * 100))

    # ---- frames --------------------------------------------------------
    def on_frame(self, frame: np.ndarray, flags: int) -> None:
        down = bool(flags & BUTTON_FLAG)
        if down and not self._button_was_down and time.monotonic() - self._last_button_snap > 0.5:
            self._last_button_snap = time.monotonic()
            self._button_was_down = down
            if self.last_frame is None:
                self.last_frame = apply(frame, self.params)
            self.snapshot()
        self._button_was_down = down
        if self.freeze_cb.isChecked():
            return
        self.last_frame = apply(frame, self.params)
        self.view.set_frame(_to_qimage(self.last_frame))
        self.recorder.write(self.last_frame)

    # ---- capture actions ------------------------------------------------
    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def snapshot(self) -> Path:
        if self.last_frame is None:
            self.status_msg.setText("no frame yet"); return Path()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        p = self.output_dir / f"{self._stamp()}.png"
        cv2.imwrite(str(p), self.last_frame)
        cal = self.store.active
        p.with_suffix(".json").write_text(json.dumps({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "serial": self._serial,
            "params": self.params.to_dict(),
            "calibration": {"name": cal.name, "um_per_px": cal.um_per_px} if cal else None,
            "measurements": self.view.measurements(),
        }, indent=2))
        if self.burn_cb.isChecked():
            img = QImage(self.view.sceneRect().size().toSize(), QImage.Format.Format_RGB888)
            img.fill(0)
            painter = QPainter(img); self.view.scene().render(painter); painter.end()
            img.save(str(p.with_name(p.stem + "_annotated.png")))
        self.status_msg.setText(f"saved {p.name}")
        return p

    def toggle_record(self) -> None:
        if self.recorder.is_recording:
            p = self.recorder.stop()
            self.rec_btn.setChecked(False); self.rec_btn.setText("Record"); self.rec_label.setText("")
            self.status_msg.setText(f"saved {p.name}" if p else "")
            return
        if self.last_frame is None:
            self.rec_btn.setChecked(False); self.status_msg.setText("no frame yet"); return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        h, w = self.last_frame.shape[:2]
        try:
            self.recorder.start(self.output_dir / f"{self._stamp()}.mp4", (w, h))
        except RuntimeError as e:
            QMessageBox.warning(self, "Record", str(e)); self.rec_btn.setChecked(False); return
        self.rec_btn.setChecked(True); self.rec_btn.setText("Stop"); self.rec_label.setText("● REC")

    def _choose_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", str(self.output_dir))
        if d:
            self.output_dir = Path(d); self.out_label.setText(d); self.settings.setValue("output_dir", d)

    # ---- calibration -----------------------------------------------------
    def refresh_calibrations(self) -> None:
        self.cal_combo.blockSignals(True)
        self.cal_combo.clear(); self.cal_combo.addItem("(none)")
        for c in self.store.calibrations:
            self.cal_combo.addItem(f"{c.name}  ({c.um_per_px:.4g} µm/px)", c.name)
        act = self.store.active
        self.cal_combo.setCurrentIndex(self.cal_combo.findData(act.name) if act else 0)
        self.cal_combo.blockSignals(False)
        self.view.set_calibration(act)

    def _on_cal_selected(self, idx: int) -> None:
        self.store.set_active(self.cal_combo.itemData(idx) if idx > 0 else None)
        self.store.save()
        self.view.set_calibration(self.store.active)

    def _delete_calibration(self) -> None:
        act = self.store.active
        if act:
            self.store.remove(act.name); self.store.save(); self.refresh_calibrations()

    def start_calibration(self) -> None:
        """Wizard: user draws one LINE over a known length, then we ask for the µm value."""
        self.view.clear_measurements()
        self.view.set_tool(Tool.LINE)
        self.status_msg.setText("Calibrate: draw a line over a known length, then release")
        self.view.measurementsChanged.connect(self._finish_calibration)

    def _finish_calibration(self) -> None:
        m = self.view.measurements()
        if not m or m[-1]["type"] != "line":
            return
        self.view.measurementsChanged.disconnect(self._finish_calibration)
        px = m[-1]["value_px"]
        um, ok = QInputDialog.getDouble(self, "Calibrate", f"Line is {px:.1f} px. Known length in µm:", 1000.0, 0.001, 1e9, 3)
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "Calibrate", "Preset name (e.g. zoom position):")
        if not ok or not name.strip():
            return
        try:
            self.store.add(Calibration(name.strip(), compute_um_per_px(px, um)))
        except ValueError as e:
            QMessageBox.warning(self, "Calibrate", str(e)); return
        self.store.set_active(name.strip()); self.store.save()
        self.refresh_calibrations(); self.view.clear_measurements()

    # ---- connection status ----------------------------------------------
    def _on_connected(self, serial: str) -> None:
        self._serial = serial
        self.conn_label.setText(f"Connected  S/N {serial}")

    def _on_disconnected(self, reason: str) -> None:
        self.conn_label.setText(reason)
        self.fps_label.setText("— fps")
        if self.recorder.is_recording:
            self.toggle_record()

    def closeEvent(self, e) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        if self.recorder.is_recording:
            self.recorder.stop()
        super().closeEvent(e)
