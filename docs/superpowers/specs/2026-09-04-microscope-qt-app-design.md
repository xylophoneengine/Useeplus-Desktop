# Wadeo USB-C Digital Microscope — Qt Desktop App: Design Spec

Date: 2026-09-04
Status: approved (design), pending implementation plan
Platform: macOS (Apple Silicon), Python 3.12, conda env `/opt/pyenvs/microscope`

## 1. Problem

The Wadeo USB-C digital microscope is officially usable only through the
UseePlus iOS/Android app. Goal: a native Python/Qt desktop app that streams,
captures, records, measures and enhances the image on the laptop.

## 2. Device facts (verified 2026-09-04 on the connected unit)

| Item | Value |
|---|---|
| USB VID:PID | `2ce3:3828` ("Geek szitman" / "supercamera"), alt variant `0329:2022` |
| Firmware | bcdDevice 1.00, serial `022018050100030` |
| USB class | Composite, **not UVC**. Interface 0 = Apple iAP (class FF/F0, bulk IN `0x81`, bulk OUT `0x01`, iAP OUT `0x02`, iAP IN `0x82`). Interface 1 = External Accessory protocol string `com.useeplus.protocol`, alt-setting 1 enables streaming |
| macOS driver state | `accessoryd` holds interface 0. pyusb/libusb can still claim both interfaces, no sudo, no kext, no detach |
| Handshake | write `FF 55 FF 55 EE 10` to `0x02`, then `BB AA 05 00 00` to `0x01` |
| Packet (1024 B bulk IN) | `[AA BB][cid u8 ∈{7,11}][len u16le][fid u8][cam u8][flags u8][gsensor i32le][JPEG chunk...]`. Frame = concat chunks until `fid` changes. `flags` bit1 = device button pressed, bit0 = g-sensor valid |
| Image | JPEG 640×480 fixed, ~16.5 fps, 7–10 KB/frame, sensor mounted rotated 90° |
| Host→device controls | **None known.** LED brightness and zoom are physical knobs on this unit. Lens switch (dual-lens variants) = long-press device button |
| Spike result | `scripts/spike.py`: 60/60 frames, 0 corrupt, 16.4–16.6 fps, decodes to (480, 640, 3) |

Prior art (all MIT): [echase/ProbeView](https://github.com/echase/ProbeView)
(macOS, pyusb, working reader — vendored in `third_party/probeview/`),
[hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera),
[Revise-Robotics supercamera](https://pypi.org/project/supercamera/) (buggy frame
reassembly, not used), [MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver).

## 3. Scope

In scope:
1. Live view (rotation corrected), FPS + serial in status bar
2. Snapshot (PNG + JSON sidecar) — via button in UI **and** device hardware button
3. Record MP4
4. Measurement: line, polyline, rectangle area, angle; named µm/px calibrations; scale-bar overlay
5. Image processing: rotation/flip, brightness, contrast, gamma, sharpen, freeze frame
6. Optional, time-boxed, findings-only: static analysis of UseePlus Android APK for hidden host→device commands

Out of scope (add when asked): PyInstaller `.app`, virtual camera, multi-device,
Linux/Windows packaging, focus stacking, Wi-Fi variants.

## 4. Architecture

Single process, two threads. Blocking USB reads live in a `QThread`; everything
else in the GUI thread. 640×480 @ 16 fps is small enough that processing in the
GUI thread is fine (ponytail: no worker pool).

```
digital_microscope/
  pyproject.toml                 # name microscope; deps pyusb, opencv-python, numpy, PySide6; dev: pytest
  microscope/
    __init__.py
    __main__.py                  # python -m microscope → QApplication + MainWindow
    useeplus.py                  # protocol driver (derived from ProbeView upp_camera.py, MIT header kept)
    capture.py                   # CaptureThread(QThread)
    processing.py                # pure functions, no Qt
    calibration.py               # Calibration dataclass + JSON store
    measure.py                   # geometry math (px lengths/areas/angles → µm), no Qt
    view.py                      # MicroscopeView(QGraphicsView) + overlay items + tool state machine
    recorder.py                  # Recorder wrapping cv2.VideoWriter
    mainwindow.py                # MainWindow: layout, wiring, actions
  tests/                         # pytest, hardware-free
    data/packets_640x480.bin     # raw bulk packets dumped once from the device (~3 frames)
    test_useeplus_parser.py
    test_processing.py
    test_calibration.py
    test_measure.py
  scripts/
    spike.py                     # hardware smoke test (already exists, passing)
    dump_packets.py              # writes tests/data/packets_640x480.bin from live device
  third_party/probeview/         # vendored reference: upp_camera.py + LICENSE (MIT)
  docs/superpowers/{specs,plans}/
```

## 5. Components

### 5.1 `useeplus.py` — protocol driver
- `list_devices() -> list[usb.core.Device]`
- `parse_packet(pkt: bytes) -> Packet | None` — pure function. `Packet(cid, fid, cam, flags, gsensor, chunk)`. Returns `None` for bad magic/cid/short packet. **Unit-tested with recorded packets.**
- `FrameAssembler` — pure: `feed(Packet) -> bytes | None` returns complete JPEG on `fid` change if it starts `FFD8` and ends `FFD9`, else discards. Tracks `last_flags`, `last_gsensor`, `last_cam`.
- `Camera(index=0, timeout=5.0)` — open/claim/handshake exactly as ProbeView (`_init_device`), `read_jpeg() -> tuple[bytes, int] | None` returns `(jpeg, flags)`, `read() -> (ok, bgr_ndarray, flags)`, `release()`, context manager, `serial_number`, `resolution == (640, 480)`.
- Reconnect policy lives in `capture.py`, not here.

### 5.2 `capture.py` — `CaptureThread(QThread)`
- Signals: `frameReady(object ndarray_bgr, int flags)`, `connected(str serial)`, `disconnected(str reason)`, `fps(float)`.
- Loop: try open `Camera`; on failure emit `disconnected("no device")`, sleep 2 s, retry. On `USBError` mid-stream: release, emit `disconnected`, retry from open. `stop()` sets flag, `wait()`s.
- FPS: rolling 1 s window, emitted at most 2×/s.

### 5.3 `processing.py` — pure numpy/cv2
- `@dataclass Params: rotation: int = 90` (0/90/180/270), `flip_h: bool`, `flip_v: bool`, `brightness: int` (−100..100), `contrast: float` (0.5..3.0), `gamma: float` (0.3..3.0), `sharpen: float` (0..2), `freeze: bool` (handled by caller, not here).
- `apply(frame_bgr, params) -> frame_bgr`. Gamma via cached 256-entry LUT keyed on gamma value. Sharpen = unsharp mask. Identity params must return an equal array (test).
- Default rotation 90. Direction (90 vs 270) is unverified until the orchestrator checks a ruler under the lens during the GUI smoke test; the constant `DEFAULT_ROTATION` in `processing.py` is the single place to flip it.

### 5.4 `calibration.py`
- `@dataclass Calibration: name: str; um_per_px: float`.
- `CalibrationStore(path=~/.config/microscope/calibrations.json)`: `load() -> list[Calibration]`, `save(list)`, `add`, `remove`. Missing file → empty list, no exception. Store also persists `active_name`.
- Calibration wizard logic (`compute_um_per_px(px_length: float, known_um: float) -> float`) lives here; UI in `mainwindow.py`.

### 5.5 `measure.py` — pure geometry
- `length(p0, p1)`, `polyline_length(pts)`, `polygon_area(pts)` (shoelace), `angle(a, vertex, b)` degrees.
- `to_um(px, cal)`, `to_um2(px2, cal)`.
- `scale_bar_px(cal, target_frac=0.2, img_w=640) -> (px_len, label_um)` picks a "nice" length (1/2/5·10ⁿ µm) roughly 20% of image width.

### 5.6 `view.py` — `MicroscopeView(QGraphicsView)`
- Holds `QGraphicsPixmapItem` for the frame, a scale-bar item (bottom-right, hidden when no active calibration), and measurement items.
- Tool state machine: `Tool = NONE | LINE | POLYLINE | RECT | ANGLE`. Mouse press/move/release build the shape in **image coordinates** (scene = image px, so zoom of the view does not affect measurements). Each finished shape gets a label item with µm (or px when uncalibrated).
- `set_frame(QImage)`, `set_tool(Tool)`, `clear_measurements()`, `set_calibration(Calibration | None)`, `measurements() -> list[dict]` for the snapshot sidecar.
- Wheel = zoom view, drag with middle button/space = pan. Fit-to-window action.

### 5.7 `recorder.py`
- `Recorder`: `start(path, size, fps=16.0)`, `write(frame_bgr)`, `stop()`. `cv2.VideoWriter` fourcc `mp4v`. Writes the **processed** frame (what the user sees, minus overlays). `is_recording` property.
- Fixed 16 fps timestamping (ponytail: no variable-rate mux; ffmpeg pipe if drift matters).

### 5.8 `mainwindow.py`
- Left: `MicroscopeView`. Right dock: Adjust group (sliders bound to `Params`, Reset button), Calibration group (combo of presets, "Calibrate…" wizard, delete), Measure group (tool buttons, Clear), Capture group (Snapshot, Record/Stop, output folder label). Status bar: connection state, serial, fps, recording indicator.
- Wiring: `frameReady` → if not frozen: `processing.apply` → cache `last_frame` → `QImage` → `view.set_frame`; if recording → `recorder.write`. Button flag bit1 rising edge → `snapshot()` (debounced 500 ms).
- `snapshot()`: PNG of processed frame to `~/Pictures/Microscope/YYYYmmdd_HHMMSS.png`, plus `.json` sidecar `{timestamp, serial, params, calibration, measurements}`. Optional second PNG with overlays burned in (checkbox, default off).
- Calibrate wizard: modal instruction → user draws a LINE tool measurement → dialog asks known length in µm and preset name → `compute_um_per_px` → store, set active.
- Settings (`QSettings`): last params, active calibration, output folder, window geometry.

### 5.9 `__main__.py`
`QApplication`, `MainWindow().show()`, start `CaptureThread`, stop it on `aboutToQuit`.

## 6. Data flow

```
USB bulk IN ──1 KB pkts──▶ parse_packet ─▶ FrameAssembler ─▶ (jpeg, flags)
   [CaptureThread]                cv2.imdecode ─▶ frameReady(bgr, flags)
                                                      │  (Qt queued signal)
   [GUI thread]  processing.apply(params) ─▶ last_frame ─┬─▶ QImage ─▶ MicroscopeView (+overlays)
                                                          ├─▶ Recorder.write (if recording)
                                                          └─▶ snapshot() on UI click / button flag edge
```

## 7. Error handling

| Situation | Behaviour |
|---|---|
| No device at start | Status "No microscope found — plug in", retry every 2 s |
| `USBError` during read | Release, `disconnected`, reopen loop (ProbeView's reset-on-open already handles stuck state) |
| Unplug | Same path; view keeps last frame greyed; Record auto-stops and file is finalised |
| Corrupt JPEG | Assembler drops frame; never raises |
| libusb missing | Startup dialog: `brew install libusb`, exit |
| Calibration file corrupt | Rename to `.bad`, start empty, status-bar warning |
| Recorder open fails | Message box, stay in preview |

No exception crosses the thread boundary; the thread catches everything and emits signals.

## 8. Testing

- **pytest, no hardware:** `parse_packet` / `FrameAssembler` on `tests/data/packets_640x480.bin` (must yield ≥2 valid JPEGs decoding to 480×640); `processing.apply` identity + rotation shape + LUT monotonic; `calibration` round-trip + missing file; `measure` known geometries (3-4-5 triangle, unit square, 90° angle, scale-bar nice numbers).
- **Hardware smoke (orchestrator only):** `scripts/spike.py` after driver changes; manual GUI checklist (stream visible & upright, snapshot file + sidecar, 10 s MP4 plays, calibration with ruler, device button triggers snapshot).
- Implementors never touch the device. Only the orchestrator (Fable) runs hardware steps.

## 9. Optional RE task (findings only)

Time-box: one agent, ≤1 h. Download UseePlus APK (APKMirror/APKPure), `jadx -d`, grep for `bulkTransfer`, `controlTransfer`, `0xBB`, `0xAA`, `useeplus`. Deliverable: `docs/re/useeplus-apk-findings.md` listing any host→device command sequences with source references. No code depends on it. If LED/zoom commands appear, they become a new bounded task.

## 10. Environment

- conda env `/opt/pyenvs/microscope`: Python 3.12.x, `pyusb`, `opencv-python 5.0`, `numpy`, `PySide6 6.11.2`, `pytest`.
- Homebrew `libusb` at `/opt/homebrew/lib/libusb-1.0.dylib` (already installed).
- Run: `/opt/pyenvs/microscope/bin/python -m microscope`. Tests: `/opt/pyenvs/microscope/bin/python -m pytest`.

## 11. Orchestration model

Fable (this session) = orchestrator: owns spec/plan, dispatches one Sonnet implementor per plan task via subagent-driven development, reviews each result against the task's acceptance checks, runs hardware smoke tests, integrates. Implementors get: task text, spec sections they need, file paths, acceptance tests. They do not get device access or broader context.

## 12. Licensing

`microscope/useeplus.py` derives from ProbeView `upp_camera.py` (MIT © 2026 Everitt Chase). Keep the MIT notice in the file header and `third_party/probeview/LICENSE`. Project license: MIT.
