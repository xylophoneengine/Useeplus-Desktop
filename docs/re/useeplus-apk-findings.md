# UseePlus Android APK — static analysis of host→device USB writes

Time-boxed (~1 h) static analysis of the UseePlus Android app, to check for any
host→device USB command bytes beyond the connect handshake already implemented
in `microscope/useeplus.py`. No device was touched; this is decompiled-source
reading only.

## Source

- App: **UseePlus**, package `com.i4season.useeplus` (not `com.useeplus.*` as
  guessed in the task brief).
- File already present in the repo working tree (not fetched by this task):
  `Usee+Plus_1.0.106_APKPure.xapk`, obtained from APKPure.
  SHA256 (whole xapk): `daa5260cdd33e7f66ccfc59601129600dbdd70375637fa9b5b753219ea7acaac`
- version_name `1.0.106`, version_code `106`, min_sdk 24, target_sdk 36 (per the
  xapk's `manifest.json`).
- Base APK extracted from the xapk: `com.i4season.useeplus.apk`
  SHA256: `66b70b72f524329dea7daea226a750a968a8914e95d213d12a349523d10abe56`
- Native-lib split extracted: `config.armeabi_v7a.apk`
  SHA256: `6a6210cdca0192bc57878a0f78bbc364fa89dad373a5c13b3dc986ed42b3a691`
- Decompiler: `jadx 1.5.6` (installed via `brew install jadx`), run against the
  base APK only (`jadx -d out com.i4season.useeplus.apk`); finished with 4 minor
  non-fatal errors (typical jadx noise), 1567 `.java` files produced.
- All extracted/decompiled artifacts live only in the scratchpad, never
  committed: nothing under `docs/re/` besides this findings file.

## Method

```
shasum -a 256 Usee+Plus_1.0.106_APKPure.xapk
unzip -o -j Usee+Plus_1.0.106_APKPure.xapk com.i4season.useeplus.apk config.armeabi_v7a.apk manifest.json -d <scratch>/apk
brew install jadx
jadx -d <scratch>/out <scratch>/apk/com.i4season.useeplus.apk
grep -rnE 'bulkTransfer|controlTransfer|claimInterface|setInterface|UsbDeviceConnection' <scratch>/out/sources
grep -rn -- '-69, -86' <scratch>/out/sources          # 0xBB 0xAA byte-array literals
grep -rn -- '-1, 85, -1, 85' <scratch>/out/sources    # 0xFF 0x55 0xFF 0x55
unzip -l config.armeabi_v7a.apk | grep '\.so'
strings extracted/lib/armeabi-v7a/libOtgCameraLibUSB.so | grep -iE 'libusb|uvc|serenegiant'
grep -n 'vendor-id\|product-id' out/resources/res/xml/device_filter.xml
```

## Findings

The app is a multi-device SDK shell supporting several unrelated camera product
lines side by side (plain Android `UsbManager` bulk/control transfers, a
statically-linked **libusb** native path, and a separate **AOA** — Android Open
Accessory — path with its own `FF EE DD CC`-prefixed protocol for a different,
unrelated product family). Only the path in `com.jni.OTGCameraHandle.*` /
`com.jni.OtgCameraApi` matches our device's known handshake bytes; that is the
code discussed below.

| Bytes (hex) | cid | file:line | Trigger / meaning | Confidence |
|---|---|---|---|---|
| `FF 55 FF 55 EE 10` | — | `OTGCameraHandle/OTGDeviceHandle.java:117` (`testmfiusb()`) | iAP/MFi init handshake, sent once on connect | High — exact match to our driver |
| `BB AA 05 00 00` (APK sends 6 bytes: `...05 00 00 10`) | 0x05 | `OTGCameraHandle/OTGDeviceHandle.java:112`; `OtgCameraApi.java:159,907` (`send_connect`, `openvideo`) | "connect" / open video stream, sent after init | High — matches our driver's connect byte, trailing `10` unexplained (possibly a length/flags field our driver doesn't need) |
| `BB AA 06 00 00` | 0x06 | `OtgCameraApi.java:161,175,894` (`send_connect` for non-type-1 cameras, `send_disconnect` for type-1, `openvideonew`) | Alternate connect **and** disconnect encoding depending on camera sub-type; sent on app teardown / stream re-open | Medium — not implemented in our driver, which never sends anything at teardown |
| `BB AA 08 00 00` | 0x08 | `OtgCameraApi.java:178` (`send_disconnect`, other camera sub-type) | Disconnect for a different camera sub-type than ours | Low-medium — device/type this branch applies to isn't confirmed to be ours |
| `BB AA 0B 00 02 <i> <i2> 08 00` | 0x0B | `OtgCameraApi.java:465-468` (`changeCamera(int,int)`) | Software lens-switch command (dual-camera select) | Low — **no caller found anywhere in the decompiled sources**; looks unreachable from the shipped UI, likely present for other multi-lens SKUs |
| `BB AA 0C 09 00 00 ...` (14 bytes, encodes offset+length) | 0x0C | `OtgCameraApi.java:436` (`read_user_data`) | Generic "read NVRAM/user-data" request | Low — no caller found; unused in this build |
| `BB AA 0D <len> 00 ...` + payload | 0x0D | `OtgCameraApi.java:385-404` (`write_user_data`) | Generic "write NVRAM/user-data" request | Low — no caller found; unused in this build |
| Control transfer, bmRequestType=`0x20`, bRequest=`1`, wIndex=`5`, 64 zero bytes | — | `OTGCameraHandle/OTGDeviceHandle.java:62` (`camera_up`) | Turned out to be an **alternate connect** encoding, only used when `linuxtype==1` (a different device family) — not a separate feature | Confirmed via `OtgCameraApi.send_connect_command()` |
| Control transfer, bmRequestType=`0x20`, bRequest=`2`, wIndex=`5`, no data | — | `OTGCameraHandle/OTGDeviceHandle.java:73` (`camera_down`) | Alternate **disconnect** for the same `linuxtype==1` family | Same as above |

**No LED/brightness command exists anywhere in the decompiled sources.** The
only "zoom" fields found (`OtgCameraPic.zoom`, `AOADeviceCameraData.zoom`) are
populated **from device status packets** (telemetry the device reports) and
fed into a physical-button/ring-state handler — they are read-only reflections
of the physical zoom ring position, never USB writes. This corroborates the
README's "LED brightness and zoom are physical knobs" and "device button =
snapshot" claims.

**Net result for our device:** beyond the already-implemented connect
handshake (`FF 55 FF 55 EE 10` + `BB AA 05 00 00`), the only additional
host→device write with a plausible, reachable trigger is the **disconnect**
notification (`BB AA 06 00 00` or `BB AA 08 00 00`, cid depends on camera
sub-type) sent when the app closes/reopens the stream. Everything else found
(lens-switch, user-data read/write) has no caller in this build and targets
other camera sub-types in the same multi-device app, not confirmed reachable
for VID:PID `2ce3:3828`.

## Android transport

**Not UVC.** No `UVCCamera`/`libuvc`/`com.serenegiant` strings anywhere in the
decompiled sources or native libraries. The app implements the proprietary
`BB AA`-framed protocol itself, via two parallel code paths:
- Pure Java: `android.hardware.usb.UsbDeviceConnection.bulkTransfer/controlTransfer`
  (`com.jni.OTGCameraHandle.*`) — this is the path matching our device's
  handshake bytes.
- Native: a statically-linked **libusb** (confirmed via `strings` on
  `lib/armeabi-v7a/libOtgCameraLibUSB.so`: `libusb_bulk_transfer`,
  `libusb_control_transfer`, `libusb_claim_interface`, `http://libusb.info`,
  etc.) used for a `linuxtype==2` ("OTG_LINUXBULK_TYPE") code path.
- A third, unrelated path (`com.jni.AOADeviceHandle`) implements Android Open
  Accessory with its own `FF EE DD CC` framing — appears to target a different
  (likely WiFi-dock) product line bundled in the same app, not our device.

`res/xml/device_filter.xml` (used only for USB-attach auto-launch matching)
lists 28 `usb-device` entries but **does not include vendor-id `11491`
(0x2ce3)** — our microscope isn't in this app's known-device list for v1.0.106.
This doesn't prevent the app's generic MFi/iAP probe code from working if the
user manually opens it while the device is attached, but it does mean Android
won't auto-launch UseePlus when this specific device is plugged in.

## Caveats

- Analysis covers the **base APK only** (`com.i4season.useeplus.apk`); the
  native library was inspected only via `strings`, not disassembled, so any
  command framing implemented purely in `libOtgCameraLibUSB.so` (the
  `linuxtype==2` path) beyond the exported symbol names is not verified.
  Given our device uses the Java `UsbManager` path (matching handshake bytes
  found there), this is unlikely to matter.
- `changeCamera`, `read_user_data`, `write_user_data` have no caller in the
  decompiled sources; they may be invoked reflectively, from an unrendered
  resource-driven UI path, or simply be dead/OEM-SDK leftovers. Time-boxing
  stopped further tracing (e.g. into `.smali`/resource layout XML) here.
  This is a low-confidence "unreachable" claim, not a proof of unreachability.
- `device_filter.xml` omitting our VID:PID means this specific hardware isn't
  officially targeted by v1.0.106 of the app; the matching handshake bytes
  found are consistent with (but not certain proof of) this being the exact
  code path exercised on our device — no live device test was performed to
  confirm the interface-index details (this build's code claims interfaces
  0 and 2, not 1, contra the driver docstring in `microscope/useeplus.py`).
- No other APK versions or the iOS counterpart were checked.

**Conclusion: no additional host-initiated LED, zoom, resolution, or lens
controls were found that are confirmed reachable for VID:PID `2ce3:3828`.**
The one plausible addition — a "disconnect" notification byte
(`BB AA 06 00 00` / `BB AA 08 00 00`) sent on stream teardown — is optional
politeness, not required for our driver's current read-only streaming use
case, and is not acted on further here per the task's "findings only, no
code depends on it" scope.
