import time, sys, cv2, numpy as np
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "third_party" / "probeview"))
from upp_camera import Camera, list_devices
print("devices:", list_devices())
with Camera() as cam:
    print("serial:", cam.serial_number, "res:", cam.resolution)
    t0 = time.time(); n = 0; bad = 0; sizes = []
    while n < 60 and time.time() - t0 < 15:
        j = cam.read_jpeg()
        if j is None: bad += 1; continue
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
        if img is None: bad += 1; continue
        n += 1; sizes.append(len(j))
        if n == 30: cv2.imwrite("spike_frame.jpg", img); print("shape:", img.shape)
    dt = time.time() - t0
    print(f"frames={n} bad={bad} fps={n/dt:.1f} jpeg_bytes min/med/max={min(sizes)}/{int(np.median(sizes))}/{max(sizes)}")
