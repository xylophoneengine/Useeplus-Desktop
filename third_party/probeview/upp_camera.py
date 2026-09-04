#!/usr/bin/env python3
"""upp_camera.py — correct com.useeplus.protocol reader for the supercamera endoscope.

Why this exists: the PyPI `supercamera` package reassembles frames by scanning for
JPEG FFD8/FFD9 across 64 KB bulk reads, stripping only the leading 12-byte header.
This device coalesces many ~1 KB protocol packets into each bulk read, each with its
OWN 12-byte header, so interior headers stay embedded in the JPEG -> truncated /
corrupt frames (the "premature end of data segment" / half-gray image).

This module ports the protocol from the hbens C++ PoC (reference/geek-szitman-
supercamera/supercamera_poc.cpp), verified against the device:

  packet = [5-byte USB header][7-byte cam header][JPEG payload chunk]
    USB header : magic uint16 LE = 0xBBAA, cid uint8, length uint16 LE
                 (length counts the cam header + payload, not the 5-byte USB header)
    cam header : fid uint8, cam_num uint8, flags uint8, g_sensor uint32 LE
  Read ONE packet per bulk transfer (0x400 bytes). Accept cid in {7, 11} (the device
  splits each frame's head/tail across both). Reassemble by fid: a frame is complete
  when fid changes. JPEG payload begins at offset 12.

API mirrors the supercamera package so the other scripts are drop-in:
    with Camera() as cam:
        ret, frame = cam.read()    # (bool, numpy BGR ndarray)
        jpeg = cam.read_jpeg()     # raw JPEG bytes
"""
import time

import usb.core
import usb.util

KNOWN_DEVICES = [(0x2CE3, 0x3828), (0x0329, 0x2022)]
EP_OUT, EP_IN = 0x01, 0x81          # interface 1 (com.useeplus.protocol) bulk
EP_IAP_OUT, EP_IAP_IN = 0x02, 0x82  # interface 0 (iAP)
MAGIC_INIT = bytes([0xFF, 0x55, 0xFF, 0x55, 0xEE, 0x10])
CONNECT_CMD = bytes([0xBB, 0xAA, 0x05, 0x00, 0x00])

PKT_SIZE = 0x400          # one logical packet per bulk read
USB_HDR = 5               # magic(2) + cid(1) + length(2)
PAYLOAD_OFFSET = 12       # USB header(5) + cam header(7)
VALID_CIDS = (7, 11)
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
HOMEBREW_DYLIB = "/opt/homebrew/lib/libusb-1.0.dylib"


def _bundled_libusb():
    """When frozen by PyInstaller the recipient has no Homebrew; the libusb dylib is
    bundled inside the app. Look for it next to the frozen executable / in _MEIPASS."""
    import os
    import sys
    if not getattr(sys, "frozen", False):
        return None
    roots = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable),
             os.path.join(os.path.dirname(sys.executable), "..", "Frameworks")]
    for root in roots:
        if not root:
            continue
        for name in ("libusb-1.0.0.dylib", "libusb-1.0.dylib"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                return p
    return None


def _backend():
    import usb.backend.libusb1 as libusb1
    bundled = _bundled_libusb()
    if bundled:
        b = libusb1.get_backend(find_library=lambda _: bundled)
        if b is not None:
            return b
    b = libusb1.get_backend()
    if b is None:
        b = libusb1.get_backend(find_library=lambda _: HOMEBREW_DYLIB)
    return b


def list_devices():
    found = []
    for vid, pid in KNOWN_DEVICES:
        found.extend(usb.core.find(find_all=True, idVendor=vid, idProduct=pid,
                                   backend=_backend()))
    return found


class Camera:
    def __init__(self, index=0, timeout=5.0):
        self._timeout = timeout
        self._index = index
        self._dev = None
        self._buf = bytearray()
        self._cur_fid = None
        self._frames_read = 0
        self._open()

    def _find(self):
        devs = list_devices()
        if not devs:
            raise RuntimeError(
                "No supercamera device found. Known IDs: "
                + ", ".join(f"{v:04x}:{p:04x}" for v, p in KNOWN_DEVICES)
            )
        if self._index >= len(devs):
            raise RuntimeError(f"index {self._index} out of range ({len(devs)} found)")
        return devs[self._index]

    def _open(self, attempts=3):
        """Open + handshake, with recovery. Back-to-back runs can catch the device
        mid-re-enumeration; on a transient USB error we reset, settle, re-find, retry."""
        last = None
        for attempt in range(attempts):
            dev = self._find()
            self._dev = dev
            try:
                self._init_device(dev)
                return
            except usb.core.USBError as e:
                last = e
                try:
                    dev.reset()           # recovery only — not a routine teardown step
                except Exception:
                    pass
                usb.util.dispose_resources(dev)
                time.sleep(1.5)           # let it re-enumerate
        raise RuntimeError(f"could not open device after {attempts} attempts: {last}")

    def _init_device(self, dev):
        for intf in (0, 1):
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except Exception:
                pass
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        usb.util.claim_interface(dev, 1)
        # drain pending iAP heartbeat
        for _ in range(30):
            try:
                dev.read(EP_IAP_IN, 512, timeout=100)
            except usb.core.USBError:
                break
        dev.set_interface_altsetting(interface=1, alternate_setting=1)
        dev.clear_halt(EP_OUT)
        dev.write(EP_IAP_OUT, MAGIC_INIT, timeout=1000)
        dev.write(EP_OUT, CONNECT_CMD, timeout=1000)
        time.sleep(0.3)
        self._buf = bytearray()
        self._cur_fid = None
        # discard the first couple of (partial) frames after connect
        for _ in range(2):
            self.read_jpeg()

    def read_jpeg(self):
        """Return one complete JPEG frame as bytes, or None on timeout."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                pkt = bytes(self._dev.read(EP_IN, PKT_SIZE, timeout=1000))
            except usb.core.USBError:
                continue
            if len(pkt) < PAYLOAD_OFFSET or pkt[0] != 0xAA or pkt[1] != 0xBB \
                    or pkt[2] not in VALID_CIDS:
                continue
            length = pkt[3] | (pkt[4] << 8)
            fid = pkt[5]
            chunk = pkt[PAYLOAD_OFFSET:USB_HDR + length]
            if self._cur_fid is not None and fid != self._cur_fid and self._buf:
                frame = bytes(self._buf)
                self._buf = bytearray(chunk)   # seed next frame
                self._cur_fid = fid
                if frame.startswith(JPEG_SOI) and frame.endswith(JPEG_EOI):
                    self._frames_read += 1
                    return frame
                continue  # partial / corrupt boundary frame, skip
            self._buf.extend(chunk)
            self._cur_fid = fid
        return None

    def read(self):
        """Return (success, numpy BGR ndarray)."""
        import cv2
        import numpy as np
        jpeg = self.read_jpeg()
        if jpeg is None:
            return False, None
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        return (img is not None), img

    @property
    def resolution(self):
        return (640, 480)

    @property
    def frames_read(self):
        return self._frames_read

    @property
    def serial_number(self):
        try:
            return self._dev.serial_number
        except Exception:
            return None

    def release(self):
        if self._dev is None:
            return
        # Stop the stream (alt 0) and release cleanly. No routine reset() — a reset
        # forces re-enumeration and makes the *next* open race; keep teardown gentle.
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


if __name__ == "__main__":
    # Smoke test: list devices, open, grab one frame, report.
    import sys

    devs = list_devices()
    if not devs:
        sys.exit("no supercamera device found")
    print(f"found {len(devs)} device(s): " + ", ".join(repr(d) for d in devs))
    with Camera() as cam:
        jpeg = cam.read_jpeg()
        if not jpeg:
            sys.exit("no frame within timeout")
        ok = jpeg.startswith(JPEG_SOI) and jpeg.endswith(JPEG_EOI)
        print(f"serial={cam.serial_number} resolution={cam.resolution}")
        print(f"frame: {len(jpeg)} bytes  valid_jpeg={ok}")
        print("upp_camera.py OK — import this module and use the Camera class "
              "(see grab.py / view.py / vcam.py)")
