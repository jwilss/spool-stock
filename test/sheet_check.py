# Checks a sticker PDF against a label sheet's layout: each QR code must decode and sit inside a label, and no ink
# may fall in the gaps between labels. Used by test/run.js.
# Usage: python3 test/sheet_check.py file.pdf width height cols rows left top step_across step_down  (all in mm)
import json, subprocess, sys, tempfile, glob, os
import cv2, numpy as np

pdf, w, h, cols, rows, left, top, px, py = sys.argv[1], *map(float, sys.argv[2:4]), *map(int, sys.argv[4:6]), *map(float, sys.argv[6:10])
d = tempfile.mkdtemp()
subprocess.run(["pdftoppm", "-r", "300", "-png", pdf, os.path.join(d, "p")], check=True)
pages = sorted(glob.glob(os.path.join(d, "p*.png")))
im = cv2.imread(pages[0])
k = 300 / 25.4
labels = [(left + c * px, top + r * py) for r in range(rows) for c in range(cols)]
ok, vals, pts, _ = cv2.QRCodeDetector().detectAndDecodeMulti(im)
codes = [(v, p.mean(0) / k) for v, p in zip(vals, pts)] if ok else []
codes = [c for c in codes if c[0]]
inside = sum(1 for _, (x, y) in codes if any(lx <= x <= lx + w and ly <= y <= ly + h for lx, ly in labels))
ink = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) < 200
mask = np.ones_like(ink)
for lx, ly in labels:
    mask[int((ly + 0.3) * k):int((ly + h - 0.3) * k), int((lx + 0.3) * k):int((lx + w - 0.3) * k)] = 0
print(json.dumps({"pages": len(pages), "codes": len(codes), "inside": inside, "inkOutside": int((ink & mask.astype(bool)).sum() > 50)}))
