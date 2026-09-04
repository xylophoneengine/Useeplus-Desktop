"""Dump raw 1 KB bulk packets from the live device into tests/data/packets_640x480.bin.
Run once by the orchestrator; implementors use the file for hardware-free parser tests."""
import sys, pathlib, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party" / "probeview"))
import usb.core
from upp_camera import Camera, EP_IN, PKT_SIZE

N = int(sys.argv[1]) if len(sys.argv) > 1 else 120
out = ROOT / "tests" / "data" / "packets_640x480.bin"
with Camera() as cam:
    dev = cam._dev
    pkts = []
    while len(pkts) < N:
        try:
            pkts.append(bytes(dev.read(EP_IN, PKT_SIZE, timeout=1000)))
        except usb.core.USBError:
            continue
# fixed-size records: u16le length + payload
with open(out, "wb") as f:
    for p in pkts:
        f.write(len(p).to_bytes(2, "little")); f.write(p)
print(f"wrote {len(pkts)} packets, {sum(map(len, pkts))} bytes -> {out}")
