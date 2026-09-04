# Wadeo USB-C Microscope Qt App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PySide6 desktop app that streams, snapshots, records, measures and enhances the image from the Wadeo ("Geek szitman supercamera", USB `2ce3:3828`) microscope on macOS.

**Architecture:** One process, two threads. A `QThread` does blocking pyusb reads and reassembles JPEG frames with a pure-Python packet parser; the GUI thread decodes, applies processing, draws in a `QGraphicsView` with measurement overlays, and taps the processed frame for snapshots and MP4 recording. Pure modules (`useeplus` parser, `processing`, `measure`, `calibration`) have no Qt dependency and are unit-tested without hardware.

**Tech Stack:** Python 3.12, PySide6 6.11, pyusb + Homebrew libusb, opencv-python 5.0, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-microscope-qt-app-design.md`

## Global Constraints

- Interpreter: `/opt/pyenvs/microscope/bin/python` for everything (`python -m pytest`, `python -m microscope`). Never `pip install` outside this env.
- Hardware access is **orchestrator-only**. Implementors never run `scripts/spike.py`, `scripts/dump_packets.py` or anything that opens the USB device. Unit tests must pass with no device attached.
- Qt tests run headless: set `QT_QPA_PLATFORM=offscreen` (a `conftest.py` does this).
- Protocol constants are fixed: VID:PID `2ce3:3828` / `0329:2022`; EP_OUT `0x01`, EP_IN `0x81`, EP_IAP_OUT `0x02`, EP_IAP_IN `0x82`; MAGIC_INIT `FF 55 FF 55 EE 10`; CONNECT_CMD `BB AA 05 00 00`; packet = `[AA BB][cid][len u16le][fid][cam][flags][gsensor i32le][chunk]`, cid ∈ {7, 11}, payload offset 12.
- Frames are 640×480 BGR from the sensor; default display rotation 90° via `DEFAULT_ROTATION` in `processing.py`.
- `microscope/useeplus.py` keeps the MIT notice: "Derived from ProbeView upp_camera.py, MIT © 2026 Everitt Chase".
- Ponytail rules apply: stdlib first, no abstractions with one implementation, no config for constants. Mark deliberate ceilings with `# ponytail:` comments.
- Commit after each task with the message given in the task (repo already `git init`ed, no commits yet).
- Existing files (do not recreate): `third_party/probeview/upp_camera.py`, `third_party/probeview/LICENSE`, `scripts/spike.py`, `scripts/dump_packets.py`, `tests/data/packets_640x480.bin` (120 raw packets → 11 decodable frames, record format `u16le length + payload`), `.gitignore`.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml`, `microscope/__init__.py`, `tests/conftest.py` | package + test scaffold | 1 |
| `microscope/useeplus.py` | `parse_packet`, `FrameAssembler`, `Camera`, `list_devices` | 1 |
| `microscope/processing.py` | `Params`, `apply`, `DEFAULT_ROTATION` | 2 |
| `microscope/measure.py` | geometry + unit conversion + scale-bar sizing | 3 |
| `microscope/calibration.py` | `Calibration`, `CalibrationStore`, `compute_um_per_px` | 4 |
| `microscope/recorder.py` | `Recorder` (cv2.VideoWriter) | 5 |
| `microscope/capture.py` | `CaptureThread(QThread)` | 6 |
| `microscope/view.py` | `MicroscopeView`, `Tool`, overlays | 7 |
| `microscope/mainwindow.py`, `microscope/__main__.py` | UI wiring, snapshot, settings | 8 |
| (orchestrator) | hardware smoke, rotation direction, README | 9 |
| `docs/re/useeplus-apk-findings.md` | optional APK static analysis | 10 |

---

### Task 1: Package scaffold + protocol driver `useeplus.py`

**Files:**
- Create: `pyproject.toml`, `microscope/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `microscope/useeplus.py`
- Test: `tests/test_useeplus.py`
- Read for reference: `third_party/probeview/upp_camera.py`

**Interfaces:**
- Consumes: `tests/data/packets_640x480.bin`
- Produces:
  - `Packet(cid: int, fid: int, cam: int, flags: int, gsensor: int, chunk: bytes)` (NamedTuple)
  - `parse_packet(pkt: bytes) -> Packet | None`
  - `class FrameAssembler: feed(p: Packet) -> bytes | None; last_flags: int; last_cam: int; last_gsensor: int`
  - `class Camera(index=0, timeout=5.0)`: `read_jpeg() -> tuple[bytes, int] | None`, `read() -> tuple[bool, ndarray | None, int]`, `release()`, `serial_number: str | None`, `resolution == (640, 480)`, context manager
  - `list_devices() -> list`
  - `BUTTON_FLAG = 0x02`
  - `iter_packet_file(path) -> Iterator[bytes]` (test helper, lives in `useeplus.py` because it is the file format `scripts/dump_packets.py` writes)

- [ ] **Step 1: Scaffold**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "microscope"
version = "0.1.0"
description = "Desktop app for the Wadeo / Geek szitman supercamera USB microscope"
requires-python = ">=3.12"
license = {text = "MIT"}
dependencies = ["pyusb>=1.2", "opencv-python>=4.9", "numpy>=1.26", "PySide6>=6.6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
microscope = "microscope.__main__:main"

[tool.setuptools.packages.find]
include = ["microscope*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`microscope/__init__.py`: empty. `tests/__init__.py`: empty.

`tests/conftest.py`:
```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

Install editable: `/opt/pyenvs/microscope/bin/pip install -e .`

- [ ] **Step 2: Write failing parser/assembler tests**

`tests/test_useeplus.py`:
```python
from pathlib import Path
import cv2
import numpy as np
from microscope.useeplus import (Packet, parse_packet, FrameAssembler,
                                 iter_packet_file, BUTTON_FLAG)

DATA = Path(__file__).parent / "data" / "packets_640x480.bin"


def make_pkt(cid=7, fid=1, cam=0, flags=0, gsensor=0, chunk=b"\xff\xd8abc"):
    body = bytes([fid, cam, flags]) + gsensor.to_bytes(4, "little", signed=True) + chunk
    return b"\xaa\xbb" + bytes([cid]) + len(body).to_bytes(2, "little") + body


def test_parse_packet_fields():
    p = parse_packet(make_pkt(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz"))
    assert p == Packet(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz")


def test_parse_packet_rejects_bad_magic_cid_short():
    assert parse_packet(b"\xbb\xaa" + make_pkt()[2:]) is None
    assert parse_packet(make_pkt(cid=3)) is None
    assert parse_packet(b"\xaa\xbb\x07\x00") is None


def test_parse_packet_truncates_chunk_to_declared_length():
    pkt = make_pkt(chunk=b"12345") + b"PADDING"
    assert parse_packet(pkt).chunk == b"12345"


def test_assembler_emits_on_fid_change_and_validates_jpeg():
    a = FrameAssembler()
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"\xff\xd8AA"))) is None
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"BB\xff\xd9"))) is None
    out = a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8CC", flags=BUTTON_FLAG)))
    assert out == b"\xff\xd8AABB\xff\xd9"
    assert a.last_flags == BUTTON_FLAG


def test_assembler_drops_frame_without_soi_eoi():
    a = FrameAssembler()
    a.feed(parse_packet(make_pkt(fid=1, chunk=b"garbage")))
    assert a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8"))) is None


def test_recorded_packets_yield_decodable_frames():
    a = FrameAssembler()
    frames = []
    for raw in iter_packet_file(DATA):
        p = parse_packet(raw)
        if p is None:
            continue
        jpeg = a.feed(p)
        if jpeg:
            frames.append(jpeg)
    assert len(frames) >= 10
    for j in frames:
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape == (480, 640, 3)
```

- [ ] **Step 3: Run tests, expect ImportError**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_useeplus.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'microscope.useeplus'`

- [ ] **Step 4: Implement `microscope/useeplus.py`**

```python
"""com.useeplus.protocol driver for the Geek szitman "supercamera" (Wadeo microscope).

Derived from ProbeView upp_camera.py, MIT (c) 2026 Everitt Chase
(https://github.com/echase/ProbeView). See third_party/probeview/LICENSE.

Wire format, one logical packet per 1024-byte bulk read on EP 0x81:
    [AA BB][cid u8][len u16le][fid u8][cam u8][flags u8][gsensor i32le][JPEG chunk]
`len` counts the 7-byte cam header + chunk. A frame is complete when `fid` changes.
cid is 7 or 11 (device alternates head/tail of each frame). flags bit1 = device button.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, NamedTuple

import usb.core
import usb.util

KNOWN_DEVICES = [(0x2CE3, 0x3828), (0x0329, 0x2022)]
EP_OUT, EP_IN = 0x01, 0x81            # interface 1 (com.useeplus.protocol) bulk
EP_IAP_OUT, EP_IAP_IN = 0x02, 0x82    # interface 0 (iAP)
MAGIC_INIT = bytes([0xFF, 0x55, 0xFF, 0x55, 0xEE, 0x10])
CONNECT_CMD = bytes([0xBB, 0xAA, 0x05, 0x00, 0x00])

PKT_SIZE = 0x400
USB_HDR = 5
PAYLOAD_OFFSET = 12
VALID_CIDS = (7, 11)
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
BUTTON_FLAG = 0x02
RESOLUTION = (640, 480)
HOMEBREW_DYLIB = "/opt/homebrew/lib/libusb-1.0.dylib"


class Packet(NamedTuple):
    cid: int
    fid: int
    cam: int
    flags: int
    gsensor: int
    chunk: bytes


def parse_packet(pkt: bytes) -> Packet | None:
    """Parse one raw bulk packet. None if magic/cid/length invalid."""
    if len(pkt) < PAYLOAD_OFFSET or pkt[0] != 0xAA or pkt[1] != 0xBB or pkt[2] not in VALID_CIDS:
        return None
    length = pkt[3] | (pkt[4] << 8)
    return Packet(
        cid=pkt[2],
        fid=pkt[5],
        cam=pkt[6],
        flags=pkt[7],
        gsensor=int.from_bytes(pkt[8:12], "little", signed=True),
        chunk=bytes(pkt[PAYLOAD_OFFSET:USB_HDR + length]),
    )


class FrameAssembler:
    """Concatenate chunks until fid changes; emit the finished buffer if it is a full JPEG."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._fid: int | None = None
        self.last_flags = 0
        self.last_cam = 0
        self.last_gsensor = 0

    def feed(self, p: Packet) -> bytes | None:
        self.last_flags, self.last_cam, self.last_gsensor = p.flags, p.cam, p.gsensor
        out = None
        if self._fid is not None and p.fid != self._fid and self._buf:
            frame = bytes(self._buf)
            self._buf = bytearray()
            if frame.startswith(JPEG_SOI) and frame.endswith(JPEG_EOI):
                out = frame
        self._buf.extend(p.chunk)
        self._fid = p.fid
        return out


def iter_packet_file(path: str | Path) -> Iterator[bytes]:
    """Read the record file written by scripts/dump_packets.py (u16le length + payload)."""
    data = Path(path).read_bytes()
    i = 0
    while i + 2 <= len(data):
        n = int.from_bytes(data[i:i + 2], "little")
        yield data[i + 2:i + 2 + n]
        i += 2 + n


def _backend():
    import usb.backend.libusb1 as libusb1
    b = libusb1.get_backend()
    if b is None:
        b = libusb1.get_backend(find_library=lambda _: HOMEBREW_DYLIB)
    if b is None:
        raise RuntimeError("libusb not found; run `brew install libusb`")
    return b


def list_devices() -> list:
    found = []
    for vid, pid in KNOWN_DEVICES:
        found.extend(usb.core.find(find_all=True, idVendor=vid, idProduct=pid, backend=_backend()))
    return found


class Camera:
    def __init__(self, index: int = 0, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._index = index
        self._dev = None
        self._asm = FrameAssembler()
        self._open()

    # -- open / handshake -------------------------------------------------
    def _find(self):
        devs = list_devices()
        if not devs:
            raise RuntimeError("No supercamera device found (2ce3:3828 / 0329:2022)")
        if self._index >= len(devs):
            raise RuntimeError(f"index {self._index} out of range ({len(devs)} found)")
        return devs[self._index]

    def _open(self, attempts: int = 3) -> None:
        last = None
        for _ in range(attempts):
            dev = self._find()
            self._dev = dev
            try:
                self._init_device(dev)
                return
            except usb.core.USBError as e:
                last = e
                try:
                    dev.reset()
                except Exception:
                    pass
                usb.util.dispose_resources(dev)
                time.sleep(1.5)
        raise RuntimeError(f"could not open device after {attempts} attempts: {last}")

    def _init_device(self, dev) -> None:
        for intf in (0, 1):
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except Exception:
                pass
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        usb.util.claim_interface(dev, 1)
        for _ in range(30):  # drain pending iAP heartbeat
            try:
                dev.read(EP_IAP_IN, 512, timeout=100)
            except usb.core.USBError:
                break
        dev.set_interface_altsetting(interface=1, alternate_setting=1)
        dev.clear_halt(EP_OUT)
        dev.write(EP_IAP_OUT, MAGIC_INIT, timeout=1000)
        dev.write(EP_OUT, CONNECT_CMD, timeout=1000)
        time.sleep(0.3)
        self._asm = FrameAssembler()
        for _ in range(2):  # first frames after connect are partial
            self.read_jpeg()

    # -- reading -----------------------------------------------------------
    def read_jpeg(self) -> tuple[bytes, int] | None:
        """Return (jpeg_bytes, flags) for the next complete frame, or None on timeout.
        Raises usb.core.USBError on hard errors (caller decides on reconnect)."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                raw = bytes(self._dev.read(EP_IN, PKT_SIZE, timeout=1000))
            except usb.core.USBTimeoutError:
                continue
            p = parse_packet(raw)
            if p is None:
                continue
            frame = self._asm.feed(p)
            if frame is not None:
                return frame, self._asm.last_flags
        return None

    def read(self):
        """Return (ok, bgr_ndarray | None, flags)."""
        import cv2
        import numpy as np
        r = self.read_jpeg()
        if r is None:
            return False, None, 0
        jpeg, flags = r
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        return img is not None, img, flags

    # -- info / teardown --------------------------------------------------
    @property
    def resolution(self) -> tuple[int, int]:
        return RESOLUTION

    @property
    def serial_number(self) -> str | None:
        try:
            return self._dev.serial_number
        except Exception:
            return None

    def release(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.set_interface_altsetting(interface=1, alternate_setting=0)
        except Exception:
            pass
        for intf in (1, 0):
            try:
                usb.util.release_interface(self._dev, intf)
            except Exception:
                pass
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass
        self._dev = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()

    def __del__(self):
        self.release()
```

Note the deliberate difference from ProbeView: `read_jpeg` only swallows `USBTimeoutError`; other `USBError`s propagate so `CaptureThread` (Task 6) can reconnect.

- [ ] **Step 5: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_useeplus.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml microscope/__init__.py tests/__init__.py tests/conftest.py microscope/useeplus.py tests/test_useeplus.py .gitignore third_party scripts tests/data docs
git commit -m "feat: package scaffold + useeplus protocol driver with pure parser/assembler"
```

---

### Task 2: `processing.py`

**Files:**
- Create: `microscope/processing.py`
- Test: `tests/test_processing.py`

**Interfaces:**
- Produces:
  - `DEFAULT_ROTATION = 90`
  - `@dataclass Params: rotation: int = DEFAULT_ROTATION; flip_h: bool = False; flip_v: bool = False; brightness: int = 0; contrast: float = 1.0; gamma: float = 1.0; sharpen: float = 0.0`
  - `apply(frame: np.ndarray, params: Params) -> np.ndarray` (BGR uint8 in → BGR uint8 out, new array)
  - `Params.is_identity() -> bool`

- [ ] **Step 1: Failing tests**

`tests/test_processing.py`:
```python
import numpy as np
from microscope.processing import Params, apply, DEFAULT_ROTATION


def frame():
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)


def test_default_rotation_is_90():
    assert DEFAULT_ROTATION == 90
    assert Params().rotation == 90


def test_identity_returns_equal_copy():
    f = frame()
    p = Params(rotation=0)
    assert p.is_identity()
    out = apply(f, p)
    assert out is not f and np.array_equal(out, f)


def test_rotation_shapes():
    f = frame()
    assert apply(f, Params(rotation=90)).shape == (640, 480, 3)
    assert apply(f, Params(rotation=180)).shape == (480, 640, 3)
    assert apply(f, Params(rotation=270)).shape == (640, 480, 3)


def test_rotation_90_direction_matches_numpy_rot90_clockwise():
    f = frame()
    assert np.array_equal(apply(f, Params(rotation=90)), np.rot90(f, k=-1))


def test_flips():
    f = frame()
    assert np.array_equal(apply(f, Params(rotation=0, flip_h=True)), f[:, ::-1])
    assert np.array_equal(apply(f, Params(rotation=0, flip_v=True)), f[::-1])


def test_brightness_contrast_gamma_monotonic():
    ramp = np.tile(np.arange(256, dtype=np.uint8), (8, 1))[..., None].repeat(3, axis=2)
    for p in (Params(rotation=0, brightness=40), Params(rotation=0, contrast=1.8),
              Params(rotation=0, gamma=0.5), Params(rotation=0, gamma=2.2)):
        out = apply(ramp, p)[0, :, 0].astype(int)
        assert np.all(np.diff(out) >= 0), p
    assert apply(ramp, Params(rotation=0, brightness=40))[0, 100, 0] == 140
    assert apply(ramp, Params(rotation=0, brightness=-40))[0, 20, 0] == 0


def test_sharpen_keeps_dtype_and_shape():
    f = frame()
    out = apply(f, Params(rotation=0, sharpen=1.0))
    assert out.dtype == np.uint8 and out.shape == f.shape
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_processing.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/processing.py`:
```python
"""Pure image adjustments. BGR uint8 in, BGR uint8 out. No Qt."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache

import cv2
import numpy as np

DEFAULT_ROTATION = 90  # sensor is mounted sideways; flip to 270 here if the smoke test shows it upside down


@dataclass
class Params:
    rotation: int = DEFAULT_ROTATION   # 0, 90, 180, 270 (clockwise)
    flip_h: bool = False
    flip_v: bool = False
    brightness: int = 0                # -100..100, added
    contrast: float = 1.0              # 0.5..3.0, multiplied around 128
    gamma: float = 1.0                 # 0.3..3.0
    sharpen: float = 0.0               # 0..2, unsharp-mask amount

    def is_identity(self) -> bool:
        return self == Params(rotation=0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@lru_cache(maxsize=32)
def _lut(brightness: int, contrast: float, gamma: float) -> np.ndarray:
    x = np.arange(256, dtype=np.float32)
    y = (x - 128.0) * contrast + 128.0 + brightness
    y = np.clip(y, 0, 255) / 255.0
    y = np.power(y, 1.0 / gamma) * 255.0
    return np.clip(y, 0, 255).astype(np.uint8)


def apply(frame: np.ndarray, p: Params) -> np.ndarray:
    out = frame
    if p.rotation in _ROT:
        out = cv2.rotate(out, _ROT[p.rotation])
    if p.flip_h:
        out = out[:, ::-1]
    if p.flip_v:
        out = out[::-1]
    if p.brightness or p.contrast != 1.0 or p.gamma != 1.0:
        out = cv2.LUT(np.ascontiguousarray(out), _lut(p.brightness, p.contrast, p.gamma))
    if p.sharpen > 0:
        blur = cv2.GaussianBlur(out, (0, 0), 1.5)
        out = cv2.addWeighted(out, 1.0 + p.sharpen, blur, -p.sharpen, 0)
    return np.ascontiguousarray(out) if out is not frame else frame.copy()
```

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_processing.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/processing.py tests/test_processing.py
git commit -m "feat: pure image processing params and apply()"
```

---

### Task 3: `measure.py`

**Files:**
- Create: `microscope/measure.py`
- Test: `tests/test_measure.py`

**Interfaces:**
- Produces (points are `(x, y)` tuples of floats in image pixels):
  - `length(p0, p1) -> float`
  - `polyline_length(pts: Sequence) -> float`
  - `polygon_area(pts: Sequence) -> float`
  - `angle(a, vertex, b) -> float` (degrees, 0..180)
  - `to_um(px: float, um_per_px: float | None) -> float | None`
  - `to_um2(px2: float, um_per_px: float | None) -> float | None`
  - `format_length(px: float, um_per_px: float | None) -> str` → e.g. `"123.4 µm"`, `"1.23 mm"`, or `"57.0 px"` when uncalibrated
  - `format_area(px2, um_per_px) -> str`
  - `scale_bar(um_per_px: float, img_w_px: int, target_frac: float = 0.2) -> tuple[float, str]` → `(bar_px, label)`

- [ ] **Step 1: Failing tests**

`tests/test_measure.py`:
```python
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
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_measure.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/measure.py`:
```python
"""Geometry in image pixels + µm conversion. No Qt."""
from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]


def length(p0: Point, p1: Point) -> float:
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def polyline_length(pts: Sequence[Point]) -> float:
    return sum(length(a, b) for a, b in zip(pts, pts[1:]))


def polygon_area(pts: Sequence[Point]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + list(pts[:1])):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def angle(a: Point, vertex: Point, b: Point) -> float:
    ax, ay = a[0] - vertex[0], a[1] - vertex[1]
    bx, by = b[0] - vertex[0], b[1] - vertex[1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na == 0 or nb == 0:
        return 0.0
    c = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(c))


def to_um(px: float, um_per_px: float | None) -> float | None:
    return None if um_per_px is None else px * um_per_px


def to_um2(px2: float, um_per_px: float | None) -> float | None:
    return None if um_per_px is None else px2 * um_per_px * um_per_px


def format_length(px: float, um_per_px: float | None) -> str:
    um = to_um(px, um_per_px)
    if um is None:
        return f"{px:.1f} px"
    return f"{um / 1000:.2f} mm" if um >= 1000 else f"{um:.1f} µm"


def format_area(px2: float, um_per_px: float | None) -> str:
    um2 = to_um2(px2, um_per_px)
    if um2 is None:
        return f"{px2:.0f} px²"
    return f"{um2 / 1e6:.2f} mm²" if um2 >= 1e6 else f"{um2:.0f} µm²"


def _nice(x: float) -> float:
    """Closest of {1,2,5}·10^n to x (in log space)."""
    e = math.floor(math.log10(x))
    best = min((1, 2, 5), key=lambda m: abs(math.log10(m * 10 ** e) - math.log10(x)))
    return best * 10 ** e


def scale_bar(um_per_px: float, img_w_px: int, target_frac: float = 0.2) -> tuple[float, str]:
    um = _nice(img_w_px * target_frac * um_per_px)
    label = f"{um / 1000:g} mm" if um >= 1000 else f"{um:g} µm"
    return um / um_per_px, label
```

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_measure.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/measure.py tests/test_measure.py
git commit -m "feat: measurement geometry and unit formatting"
```

---

### Task 4: `calibration.py`

**Files:**
- Create: `microscope/calibration.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces:
  - `@dataclass Calibration: name: str; um_per_px: float`
  - `compute_um_per_px(px_length: float, known_um: float) -> float` (raises `ValueError` on non-positive input)
  - `class CalibrationStore(path: Path | None = None)`: `calibrations: list[Calibration]`, `active: Calibration | None`, `add(cal)` (replaces same name), `remove(name)`, `set_active(name | None)`, `save()`, `load()`; default path `~/.config/microscope/calibrations.json`
  - Missing file → empty; corrupt file → renamed `<path>.bad`, empty, `store.warning: str | None` set.

- [ ] **Step 1: Failing tests**

`tests/test_calibration.py`:
```python
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
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_calibration.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/calibration.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_calibration.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/calibration.py tests/test_calibration.py
git commit -m "feat: calibration presets with JSON store"
```

---

### Task 5: `recorder.py`

**Files:**
- Create: `microscope/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Produces: `class Recorder`: `start(path: Path, size: tuple[int, int], fps: float = 16.0) -> None` (raises `RuntimeError` if writer fails to open), `write(frame_bgr: np.ndarray) -> None` (no-op when not recording; resizes if frame size ≠ `size`), `stop() -> Path | None`, `is_recording: bool`, `frames_written: int`, `path: Path | None`.

- [ ] **Step 1: Failing tests**

`tests/test_recorder.py`:
```python
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
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_recorder.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/recorder.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_recorder.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/recorder.py tests/test_recorder.py
git commit -m "feat: MP4 recorder"
```

---

### Task 6: `capture.py` — `CaptureThread`

**Files:**
- Create: `microscope/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `microscope.useeplus.Camera` (`read()` → `(ok, bgr, flags)`, `serial_number`, `release()`), `usb.core.USBError`
- Produces: `class CaptureThread(QThread)`:
  - `__init__(camera_factory=Camera, retry_s: float = 2.0, parent=None)` — `camera_factory()` must return an object with `read()`, `release()`, `serial_number`; may raise `RuntimeError` (no device) or `usb.core.USBError`.
  - Signals: `frameReady = Signal(object, int)` (BGR ndarray, flags), `connected = Signal(str)` (serial or ""), `disconnected = Signal(str)` (reason), `fps = Signal(float)`
  - `stop()` — sets flag, `wait(5000)`.

- [ ] **Step 1: Failing tests**

`tests/test_capture.py`:
```python
import time
import numpy as np
import usb.core
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from microscope.capture import CaptureThread


def _app():
    return QCoreApplication.instance() or QCoreApplication([])


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class FakeCam:
    def __init__(self, frames=5, fail_after=None):
        self.n, self.fail_after, self.released = 0, fail_after, False
        self.serial_number = "S123"
        self._frames = frames

    def read(self):
        if self.fail_after is not None and self.n >= self.fail_after:
            raise usb.core.USBError("boom")
        if self.n >= self._frames:
            time.sleep(0.01)
            return False, None, 0
        self.n += 1
        return True, np.zeros((480, 640, 3), np.uint8), 0x02 if self.n == 3 else 0

    def release(self):
        self.released = True


def test_emits_frames_and_connected():
    _app()
    cam = FakeCam(frames=5)
    t = CaptureThread(camera_factory=lambda: cam, retry_s=0.05)
    got, conn = [], []
    t.frameReady.connect(lambda f, fl: got.append((f.shape, fl)))
    t.connected.connect(conn.append)
    t.start(); spin(300); t.stop()
    assert conn == ["S123"]
    assert len(got) == 5 and got[2][1] == 0x02 and got[0][0] == (480, 640, 3)
    assert cam.released


def test_no_device_retries_and_reports():
    _app()
    calls = []
    def factory():
        calls.append(1)
        raise RuntimeError("No supercamera device found")
    t = CaptureThread(camera_factory=factory, retry_s=0.05)
    dis = []
    t.disconnected.connect(dis.append)
    t.start(); spin(250); t.stop()
    assert len(calls) >= 3 and dis and "No supercamera" in dis[0]


def test_usb_error_midstream_reconnects():
    _app()
    cams = [FakeCam(frames=10, fail_after=2), FakeCam(frames=3)]
    t = CaptureThread(camera_factory=lambda: cams.pop(0), retry_s=0.05)
    got, dis, conn = [], [], []
    t.frameReady.connect(lambda f, fl: got.append(1))
    t.disconnected.connect(dis.append); t.connected.connect(conn.append)
    t.start(); spin(400); t.stop()
    assert len(got) == 5 and len(dis) == 1 and len(conn) == 2
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_capture.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/capture.py`:
```python
"""Background USB reader. Owns the Camera; emits frames and connection state to the GUI thread."""
from __future__ import annotations

import time
from collections import deque

import usb.core
from PySide6.QtCore import QThread, Signal

from .useeplus import Camera


class CaptureThread(QThread):
    frameReady = Signal(object, int)   # BGR ndarray, flags
    connected = Signal(str)            # serial
    disconnected = Signal(str)         # reason
    fps = Signal(float)

    def __init__(self, camera_factory=Camera, retry_s: float = 2.0, parent=None) -> None:
        super().__init__(parent)
        self._factory = camera_factory
        self._retry_s = retry_s
        self._run = False

    def stop(self) -> None:
        self._run = False
        self.wait(5000)

    def _sleep(self, s: float) -> None:
        end = time.monotonic() + s
        while self._run and time.monotonic() < end:
            time.sleep(0.02)

    def run(self) -> None:
        self._run = True
        while self._run:
            cam = None
            try:
                cam = self._factory()
            except (RuntimeError, usb.core.USBError) as e:
                self.disconnected.emit(str(e))
                self._sleep(self._retry_s)
                continue
            self.connected.emit(cam.serial_number or "")
            stamps: deque[float] = deque(maxlen=64)
            last_fps_emit = 0.0
            try:
                while self._run:
                    ok, frame, flags = cam.read()
                    if not ok:
                        continue
                    self.frameReady.emit(frame, flags)
                    now = time.monotonic()
                    stamps.append(now)
                    if now - last_fps_emit > 0.5 and len(stamps) > 1:
                        self.fps.emit((len(stamps) - 1) / (stamps[-1] - stamps[0]))
                        last_fps_emit = now
            except usb.core.USBError as e:
                self.disconnected.emit(f"USB error: {e}")
            finally:
                try:
                    cam.release()
                except Exception:
                    pass
            if self._run:
                self._sleep(self._retry_s)
```

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_capture.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/capture.py tests/test_capture.py
git commit -m "feat: CaptureThread with reconnect and fps"
```

---

### Task 7: `view.py` — `MicroscopeView` with measurement overlays

**Files:**
- Create: `microscope/view.py`
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `microscope.measure` (`length`, `polyline_length`, `polygon_area`, `angle`, `format_length`, `format_area`, `scale_bar`), `microscope.calibration.Calibration`
- Produces:
  - `class Tool(Enum): NONE, LINE, POLYLINE, RECT, ANGLE`
  - `class MicroscopeView(QGraphicsView)`:
    - `set_frame(img: QImage) -> None` (scene rect = image rect; first frame triggers fit)
    - `set_tool(tool: Tool) -> None`; `tool: Tool`
    - `set_calibration(cal: Calibration | None) -> None`
    - `clear_measurements() -> None`
    - `measurements() -> list[dict]` — each `{"type": "line"|"polyline"|"rect"|"angle", "points": [[x,y],...], "value_px": float, "label": str}`
    - `fit() -> None`
    - Signal `measurementsChanged = Signal()`
    - Tool input in image coordinates, also callable directly for tests: `tool_press(x, y)`, `tool_move(x, y)`, `tool_release(x, y)`, `tool_double_click(x, y)` (finishes POLYLINE).
  - Interaction: LINE/RECT = press-drag-release. ANGLE = three clicks (a, vertex, b). POLYLINE = click per vertex, double-click ends. Wheel zooms around cursor; middle-drag or space+drag pans (`ScrollHandDrag`).

- [ ] **Step 1: Failing tests**

`tests/test_view.py`:
```python
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
    assert v.scale_bar_visible() and v.scale_bar_label() == "100 µm"
    v.set_calibration(None)
    assert not v.scale_bar_visible()
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_view.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`microscope/view.py`:
```python
"""QGraphicsView showing the frame with a scale bar and measurement overlays.
Scene coordinates == image pixels, so measurements are independent of view zoom."""
from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QPen, QColor, QFont, QPainter, QPolygonF
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItemGroup,
                               QGraphicsLineItem, QGraphicsRectItem, QGraphicsPathItem,
                               QGraphicsSimpleTextItem)
from PySide6.QtGui import QPainterPath

from . import measure
from .calibration import Calibration


class Tool(Enum):
    NONE = auto()
    LINE = auto()
    POLYLINE = auto()
    RECT = auto()
    ANGLE = auto()


PEN = QPen(QColor(255, 230, 0), 0)          # cosmetic width 0 = 1 device px at any zoom
PEN.setCosmetic(True)
TEXT_COLOR = QColor(255, 230, 0)


class MicroscopeView(QGraphicsView):
    measurementsChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QColor(30, 30, 30))
        self._pix = QGraphicsPixmapItem()
        self._pix.setZValue(0)
        self._scene.addItem(self._pix)
        self._bar_group = QGraphicsItemGroup(); self._bar_group.setZValue(10); self._bar_group.setVisible(False)
        self._scene.addItem(self._bar_group)
        self._bar_label = ""
        self._items: list[QGraphicsItemGroup] = []
        self._labels: list[QGraphicsSimpleTextItem] = []   # parallel to _items
        self._meas: list[dict] = []
        self._cal: Calibration | None = None
        self.tool = Tool.NONE
        self._pts: list[tuple[float, float]] = []   # in-progress points
        self._preview: QGraphicsItemGroup | None = None
        self._fitted = False
        self._panning = False

    # ---- frame ---------------------------------------------------------
    def set_frame(self, img: QImage) -> None:
        self._pix.setPixmap(QPixmap.fromImage(img))
        rect = QRectF(0, 0, img.width(), img.height())
        if self._scene.sceneRect() != rect:
            self._scene.setSceneRect(rect)
            self._update_scale_bar()
            self._fitted = False
        if not self._fitted:
            self.fit()

    def fit(self) -> None:
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._fitted:
            self.fit()

    # ---- calibration / scale bar -------------------------------------
    def set_calibration(self, cal: Calibration | None) -> None:
        self._cal = cal
        self._update_scale_bar()
        for m, lbl in zip(self._meas, self._labels):
            m["label"] = self._label_for(m["type"], m["value_px"])
            lbl.setText(m["label"])
        self.measurementsChanged.emit()

    def scale_bar_visible(self) -> bool:
        return self._bar_group.isVisible()

    def scale_bar_label(self) -> str:
        return self._bar_label

    def _update_scale_bar(self) -> None:
        for c in self._bar_group.childItems():
            self._scene.removeItem(c)
        w, h = self._scene.sceneRect().width(), self._scene.sceneRect().height()
        if self._cal is None or w == 0:
            self._bar_group.setVisible(False)
            self._bar_label = ""
            return
        bar_px, self._bar_label = measure.scale_bar(self._cal.um_per_px, int(w))
        margin = w * 0.03
        x1, y = w - margin, h - margin
        line = QGraphicsLineItem(x1 - bar_px, y, x1, y)
        pen = QPen(QColor(255, 255, 255), 3); pen.setCosmetic(True); line.setPen(pen)
        txt = QGraphicsSimpleTextItem(self._bar_label)
        txt.setBrush(QColor(255, 255, 255)); txt.setFont(QFont("Helvetica", 14))
        txt.setPos(x1 - bar_px, y - 22)
        self._bar_group.addToGroup(line); self._bar_group.addToGroup(txt)
        self._bar_group.setVisible(True)

    # ---- measurements -----------------------------------------------
    def set_tool(self, tool: Tool) -> None:
        self._cancel_in_progress()
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool != Tool.NONE else Qt.CursorShape.ArrowCursor)

    def measurements(self) -> list[dict]:
        return [dict(m) for m in self._meas]

    def clear_measurements(self) -> None:
        for g in self._items:
            self._scene.removeItem(g)
        self._items.clear(); self._labels.clear(); self._meas.clear()
        self._cancel_in_progress()
        self.measurementsChanged.emit()

    def _label_for(self, kind: str, value_px: float) -> str:
        upp = self._cal.um_per_px if self._cal else None
        if kind == "rect":
            return measure.format_area(value_px, upp)
        if kind == "angle":
            return f"{value_px:.1f}°"
        return measure.format_length(value_px, upp)

    def _shape_group(self, kind: str, pts: list[tuple[float, float]]) -> QGraphicsItemGroup:
        g = QGraphicsItemGroup(); g.setZValue(5)
        if kind == "rect":
            (x0, y0), (x1, y1) = pts
            item = QGraphicsRectItem(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        else:
            path = QPainterPath(QPointF(*pts[0]))
            for p in pts[1:]:
                path.lineTo(QPointF(*p))
            item = QGraphicsPathItem(path)
        item.setPen(PEN); g.addToGroup(item)
        return g

    def _value(self, kind: str, pts) -> float:
        if kind == "line":
            return measure.length(pts[0], pts[1])
        if kind == "polyline":
            return measure.polyline_length(pts)
        if kind == "rect":
            (x0, y0), (x1, y1) = pts
            return abs(x1 - x0) * abs(y1 - y0)
        return measure.angle(pts[0], pts[1], pts[2])

    def _commit(self, kind: str, pts: list[tuple[float, float]]) -> None:
        value = self._value(kind, pts)
        label = self._label_for(kind, value)
        g = self._shape_group(kind, pts)
        txt = QGraphicsSimpleTextItem(label); txt.setBrush(TEXT_COLOR); txt.setFont(QFont("Helvetica", 12))
        anchor = pts[1] if kind == "angle" else pts[-1]
        txt.setPos(anchor[0] + 4, anchor[1] + 4)
        g.addToGroup(txt)
        self._scene.addItem(g)
        self._items.append(g); self._labels.append(txt)
        self._meas.append({"type": kind, "points": [[float(x), float(y)] for x, y in pts],
                           "value_px": float(value), "label": label})
        self.measurementsChanged.emit()

    def _cancel_in_progress(self) -> None:
        self._pts = []
        if self._preview is not None:
            self._scene.removeItem(self._preview)
            self._preview = None

    def _show_preview(self, kind: str, pts) -> None:
        if self._preview is not None:
            self._scene.removeItem(self._preview)
        self._preview = self._shape_group(kind, pts)
        self._scene.addItem(self._preview)

    # tool input, image coordinates -------------------------------------
    def tool_press(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT):
            self._pts = [(x, y)]
        elif self.tool == Tool.POLYLINE:
            self._pts.append((x, y))
            if len(self._pts) > 1:
                self._show_preview("polyline", self._pts)
        elif self.tool == Tool.ANGLE:
            self._pts.append((x, y))
            if len(self._pts) == 3:
                pts, self._pts = self._pts, []
                self._cancel_in_progress()
                self._commit("angle", pts)
            elif len(self._pts) == 2:
                self._show_preview("line", self._pts)

    def tool_move(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT) and self._pts:
            self._show_preview("line" if self.tool == Tool.LINE else "rect", [self._pts[0], (x, y)])
        elif self.tool == Tool.POLYLINE and self._pts:
            self._show_preview("polyline", self._pts + [(x, y)])
        elif self.tool == Tool.ANGLE and len(self._pts) == 2:
            self._show_preview("polyline", self._pts + [(x, y)])

    def tool_release(self, x: float, y: float) -> None:
        if self.tool in (Tool.LINE, Tool.RECT) and self._pts:
            pts = [self._pts[0], (x, y)]
            self._cancel_in_progress()
            if pts[0] != pts[1]:
                self._commit("line" if self.tool == Tool.LINE else "rect", pts)

    def tool_double_click(self, x: float, y: float) -> None:
        if self.tool == Tool.POLYLINE:
            pts = self._pts + [(x, y)]
            self._cancel_in_progress()
            if len(pts) >= 2:
                self._commit("polyline", pts)

    # ---- Qt events → tool input --------------------------------------
    def _img(self, e) -> tuple[float, float]:
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

    def mousePressEvent(self, e) -> None:
        if self.tool == Tool.NONE:   # Pan tool: left-drag pans via ScrollHandDrag
            self._panning = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            super().mousePressEvent(e)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.tool_press(*self._img(e))
        elif e.button() == Qt.MouseButton.RightButton:
            self._cancel_in_progress()
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._panning:
            super().mouseMoveEvent(e); return
        self.tool_move(*self._img(e))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if self._panning:
            super().mouseReleaseEvent(e)
            self._panning = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.tool_release(*self._img(e))
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.tool == Tool.POLYLINE:
            self.tool_double_click(*self._img(e))
        e.accept()

    def wheelEvent(self, e) -> None:
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self._fitted = False
        e.accept()
```

Panning: only the Pan tool (`Tool.NONE`) pans, with left-drag. No middle-button or space+drag remapping (out of scope).

- [ ] **Step 4: Run tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_view.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add microscope/view.py tests/test_view.py
git commit -m "feat: MicroscopeView with scale bar and measurement tools"
```

---

### Task 8: `mainwindow.py` + `__main__.py`

**Files:**
- Create: `microscope/mainwindow.py`, `microscope/__main__.py`
- Test: `tests/test_mainwindow.py`

**Interfaces:**
- Consumes: `CaptureThread` (signals `frameReady(object,int)`, `connected(str)`, `disconnected(str)`, `fps(float)`, `stop()`), `Params`/`apply`, `MicroscopeView`/`Tool`, `CalibrationStore`/`Calibration`/`compute_um_per_px`, `Recorder`, `BUTTON_FLAG`.
- Produces:
  - `class MainWindow(QMainWindow)`: `__init__(capture: CaptureThread, store: CalibrationStore | None = None, output_dir: Path | None = None, settings: QSettings | None = None)`; public for tests: `on_frame(frame, flags)`, `snapshot() -> Path`, `toggle_record()`, `params: Params`, `view: MicroscopeView`, `last_frame: np.ndarray | None`, `output_dir: Path`.
  - `__main__.main() -> int`.
  - Snapshot files: `<output_dir>/YYYYmmdd_HHMMSS_ffffff.png` + same stem `.json` with `{"timestamp", "serial", "params", "calibration": {"name","um_per_px"} | null, "measurements": [...]}`; optional `<stem>_annotated.png` when the "Burn in overlays" checkbox is on (render `view.scene()` to a `QImage`).
  - Button flag: rising edge of `flags & BUTTON_FLAG` → `snapshot()`, debounced 500 ms.
  - Freeze: checkbox; when on, incoming frames are ignored by the view (still recorded? **no** — recording also pauses; keep semantics simple: freeze = stop consuming frames).

- [ ] **Step 1: Failing tests**

`tests/test_mainwindow.py`:
```python
import json
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from microscope.mainwindow import MainWindow
from microscope.capture import CaptureThread
from microscope.calibration import CalibrationStore, Calibration
from microscope.useeplus import BUTTON_FLAG


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
    assert w.last_frame.shape == (640, 480, 3)          # default rotation 90
    assert w.view.sceneRect().width() == 480 and w.view.sceneRect().height() == 640


def test_snapshot_writes_png_and_sidecar(tmp_path):
    w, store = make_win(tmp_path)
    store.add(Calibration("4x", 2.0)); store.set_active("4x"); w.refresh_calibrations()
    w.on_frame(frame(77), 0)
    p = w.snapshot()
    assert p.exists() and p.suffix == ".png"
    side = json.loads(p.with_suffix(".json").read_text())
    assert side["calibration"] == {"name": "4x", "um_per_px": 2.0}
    assert side["params"]["rotation"] == 90 and side["measurements"] == []


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
```

- [ ] **Step 2: Run, expect failure**

Run: `/opt/pyenvs/microscope/bin/python -m pytest tests/test_mainwindow.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `microscope/mainwindow.py`**

```python
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
```

`microscope/__main__.py`:
```python
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .capture import CaptureThread
from .mainwindow import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    try:
        from .useeplus import _backend
        _backend()
    except RuntimeError as e:
        QMessageBox.critical(None, "Microscope", str(e))
        return 1
    cap = CaptureThread()
    win = MainWindow(cap)
    win.resize(1100, 760)
    win.show()
    cap.start()
    app.aboutToQuit.connect(cap.stop)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests**

Run: `/opt/pyenvs/microscope/bin/python -m pytest -v`
Expected: all pass (≈33 tests). The suite must complete with no USB device attached.

- [ ] **Step 5: Smoke launch headless** (orchestrator runs this — it opens the device if attached)

Run `QT_QPA_PLATFORM=offscreen /opt/pyenvs/microscope/bin/python scripts/e2e_smoke.py`: the script builds `QApplication`, `CaptureThread`, `MainWindow`, connects the capture signals to a list, schedules `QTimer.singleShot(5000, app.quit)` AFTER the app exists, runs `app.exec()`, prints events / frame count / `cap.isFinished()` and exits 0. (A `QTimer.singleShot` created before `QApplication` never fires — the original wording of this step hung for that reason.)

- [ ] **Step 6: Commit**

```bash
git add microscope/mainwindow.py microscope/__main__.py tests/test_mainwindow.py
git commit -m "feat: main window wiring, snapshots, recording, calibration wizard"
```

---

### Task 9: Hardware smoke + rotation direction + README (ORCHESTRATOR ONLY)

**Files:**
- Modify: `microscope/processing.py` (only `DEFAULT_ROTATION` if needed)
- Create: `README.md`

- [ ] **Step 1:** `/opt/pyenvs/microscope/bin/python scripts/spike.py` → ≥55/60 frames, 0 bad, ≥14 fps.
- [ ] **Step 2:** `/opt/pyenvs/microscope/bin/python -m microscope`. Put a ruler / printed text under the lens. Checklist:
  - live stream visible, ≥14 fps in status bar, S/N shown
  - text reads upright with default rotation; if mirrored/upside down, change `DEFAULT_ROTATION` to 270 (or set flip) and re-run `pytest`
  - Snapshot → PNG + JSON in `~/Pictures/Microscope`
  - Record 10 s → MP4 plays in QuickTime (`open <file>`)
  - Calibrate over 1 mm of ruler → scale bar appears, Line tool reports ≈1000 µm on the same span
  - press device button → snapshot saved once
  - unplug → status shows USB error, replug → stream resumes within ~3 s
- [ ] **Step 3:** Write `README.md` (install: `conda create -p /opt/pyenvs/microscope python=3.12`, `pip install -e .[dev]`, `brew install libusb`; run; features; device facts one paragraph; credits ProbeView MIT).
- [ ] **Step 4:** Commit: `git commit -am "docs: README, verified on hardware"`.

---

### Task 10 (optional, time-boxed ≤1 h): UseePlus APK static analysis — findings only

**Files:**
- Create: `docs/re/useeplus-apk-findings.md`

- [ ] **Step 1:** Obtain UseePlus Android APK (package name likely `com.useeplus.*`; search APKMirror / APKPure; note exact version + SHA256 in the report). Do not commit the APK.
- [ ] **Step 2:** `brew install jadx` (or download jadx release zip into the scratchpad) → `jadx -d out UseePlus.apk`.
- [ ] **Step 3:** `grep -rnE 'bulkTransfer|controlTransfer|0xBB|0xAA|(-69|-86)\b|useeplus|setInterface|claimInterface' out/sources | head -200`. Also check for native libs (`lib/arm64-v8a/*.so`) and `strings` them for `\xbb\xaa`.
- [ ] **Step 4:** Report: every distinct byte sequence written to the device, with file:line, and what UI action triggers it (LED, zoom, resolution, snapshot, lens switch). If only the known `BB AA 05 00 00` appears, say so plainly: "no host→device controls beyond connect".
- [ ] **Step 5:** Commit: `git add docs/re && git commit -m "docs: UseePlus APK static analysis findings"`.

Nothing else depends on this task. If it finds LED/zoom commands, they become a new bounded task via brainstorming.

---

## Self-review

- **Spec coverage:** §3.1 live view → T6+T8; §3.2 snapshot + sidecar + device button → T8; §3.3 record → T5+T8; §3.4 measurement/calibration/scale bar → T3+T4+T7+T8; §3.5 processing → T2+T8; §3.6 APK → T10; §7 error table → T6 (retry/reconnect), T8 (`_on_disconnected` stops recording, libusb check in `__main__`, corrupt calibration warning), T4 (`.bad` rename), T5 (writer failure → RuntimeError → message box in T8); §8 tests → each task; §9/§11 → T9/T10 orchestrator-only.
- **Placeholders:** none. Space+drag pan explicitly dropped (Pan tool button instead).
- **Type consistency:** `Camera.read()` returns 3-tuple everywhere (T1, T6 FakeCam); `CaptureThread(camera_factory=..., retry_s=...)` (T6, T8 tests); `measurements()` dict keys `type/points/value_px/label` (T7, T8 sidecar); `Params.to_dict/from_dict` (T2, T8); `CalibrationStore.warning/active/add/remove/set_active/save` (T4, T8); `Recorder.start(path, (w, h))` (T5, T8 passes `(w, h)`).
