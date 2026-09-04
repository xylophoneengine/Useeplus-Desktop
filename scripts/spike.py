"""60-frame stream check against the attached device, using this project's driver."""
import time, cv2, numpy as np
from microscope.useeplus import Camera, list_devices
print("devices:", list_devices())
with Camera() as cam:
    print("serial:", cam.serial_number, "res:", cam.resolution)
    t0 = time.time(); n = 0; bad = 0; sizes = []
    while n < 60 and time.time() - t0 < 15:
        r = cam.read_jpeg()
        if r is None: bad += 1; continue
        j, _flags = r
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
        if img is None: bad += 1; continue
        n += 1; sizes.append(len(j))
        if n == 30: print("shape:", img.shape)
    dt = time.time() - t0
    print(f"frames={n} bad={bad} fps={n/dt:.1f} jpeg_bytes min/med/max={min(sizes)}/{int(np.median(sizes))}/{max(sizes)}")
