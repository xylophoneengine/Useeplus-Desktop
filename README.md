# microscope — desktop app for the Wadeo / "supercamera" USB-C microscope

A PySide6 app for the cheap USB-C digital microscopes (Wadeo and many rebrands) that
officially only work with the **UseePlus** phone app. Live view, snapshots, MP4
recording, µm measurements with named calibrations, scale bar, and software image
adjustments — on macOS, without kernel extensions or `sudo`.

## The device

| | |
|---|---|
| USB ID | `2ce3:3828` "Geek szitman" / "supercamera" (also `0329:2022`) |
| Class | **Not UVC.** Enumerates as an Apple iAP accessory; interface 1 carries the proprietary `com.useeplus.protocol` |
| Image | JPEG 640×480, ~16.5 fps, sensor mounted sideways (the app rotates by default) |
| Controls | None over USB. LED brightness and zoom are physical knobs. Device button = snapshot; long-press switches lens on dual-lens variants |
| Verified | firmware 1.00, macOS 26 (Apple Silicon), 2026-09-04 |

Protocol: after `FF 55 FF 55 EE 10` on the iAP endpoint and `BB AA 05 00 00` on the
bulk endpoint, the device streams 1 KB packets `[AA BB][cid][len][fid][cam][flags][gsensor][JPEG chunk]`;
a frame is complete when `fid` changes. The driver in `microscope/useeplus.py` is derived
from [echase/ProbeView](https://github.com/echase/ProbeView) (MIT). Related work:
[hbens/geek-szitman-supercamera](https://github.com/hbens/geek-szitman-supercamera),
[Revise-Robotics supercamera](https://pypi.org/project/supercamera/),
[MAkcanca/useeplus-linux-driver](https://github.com/MAkcanca/useeplus-linux-driver).

## Install (macOS)

```bash
brew install libusb
conda create -p /opt/pyenvs/microscope python=3.12
/opt/pyenvs/microscope/bin/pip install -e ".[dev]"
```

## Run

```bash
/opt/pyenvs/microscope/bin/python -m microscope
```

Plug the microscope in before or after starting; the app reconnects on its own.
Snapshots (PNG + JSON sidecar with parameters, calibration and measurements) and
recordings go to `~/Pictures/Microscope` by default.

## Features

- **Adjust**: rotation / flip, brightness, contrast, gamma, sharpen, freeze.
- **Calibrate**: put a stage micrometer or ruler under the lens, click *Calibrate…*, draw a line
  over a known length, enter it in µm, name the preset (one per zoom-ring position).
  The scale bar appears whenever a preset is active.
- **Measure**: line, polyline (double-click to finish), rectangle area, angle (three clicks);
  labels in µm/mm when calibrated, px otherwise. *Pan* tool: left-drag pans, wheel zooms.
- **Capture**: Snapshot / Record buttons; the device's hardware button also takes a snapshot.

## Development

```bash
/opt/pyenvs/microscope/bin/python -m pytest -q          # hardware-free unit tests
/opt/pyenvs/microscope/bin/python scripts/spike.py      # 60-frame stream check (device attached)
/opt/pyenvs/microscope/bin/python scripts/e2e_smoke.py  # headless app smoke (device attached)
```

Linux should work with a udev rule granting access to `2ce3:3828`; Windows needs a
libusb driver (Zadig). Neither is tested. Design notes: `docs/superpowers/specs/`.
