"""Room tracing (drawqa/trace.py) against CubiCasa's true room outlines, on held-out plans.

    .venv/bin/python tools/eval_trace.py [out_dir_for_overlays]
Needs data/heldout/trace/<plan>.{F1_original.png,F1_scaled.png,model.svg} (fetched from the Modal volume).
Doors and windows come from the detector, as they would for an uploaded image. Two ways to say where a room is:
  box   : the true room's bounding rectangle (what the detector gives for a room it knows)
  label : a small box at the room's centre (what a reader of the printed room name gives)
"""
import glob, os, sys
from xml.dom import minidom

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from drawqa import convert, detect, trace  # noqa: E402
from drawqa.postprocess import apply  # noqa: E402

T = os.path.join(ROOT, "data", "heldout", "trace")


def truth(svg, sx, sy):
    out = []
    for g in minidom.parse(svg).getElementsByTagName("g"):
        c = g.getAttribute("class")
        if not c.startswith("Space "):
            continue
        pol = next((p for p in g.childNodes if p.nodeName == "polygon"), None)
        if pol is None:
            continue
        pts = [a.split(",") for a in pol.getAttribute("points").split() if "," in a]
        if len(pts) >= 3:
            out.append((c[6:], np.array([[float(x) * sx, float(y) * sy] for x, y in pts], np.float32)))
    return out


def main(out_dir=None):
    rows = []
    for f in sorted(glob.glob(os.path.join(T, "*.F1_original.png"))):
        name = os.path.basename(f)[:-len(".F1_original.png")]
        img = cv2.imread(f)
        h, w = img.shape[:2]
        ws, hs = Image.open(os.path.join(T, name + ".F1_scaled.png")).size
        rooms = truth(os.path.join(T, name + ".model.svg"), w / ws, h / hs)
        dets = apply(convert.raw_detections(f, 0, img), detect.thresholds(), detect.tuned().get("rules"))
        doors = [d["bbox"] for d in dets if d["cls"] == "door"]
        windows = [d["bbox"] for d in dets if d["cls"] == "window"]
        masks, boxes, labels = [], [], []
        for kind, pts in rooms:
            m = np.zeros((h, w), np.uint8)
            cv2.fillPoly(m, [pts.astype(np.int32)], 1)
            masks.append(m)
            boxes.append([pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()])
            dt = cv2.distanceTransform(m, cv2.DIST_L2, 3)
            cy, cx = np.unravel_index(int(dt.argmax()), dt.shape)
            labels.append([cx - 10, cy - 6, cx + 10, cy + 6])
        res = dict(box=trace.trace_rooms(img, boxes, doors, windows), label=trace.trace_from_labels(img, labels, doors, windows))
        vis = img.copy() if out_dir else None
        for i, (kind, pts) in enumerate(rooms):
            for how in ("box", "label"):
                r = res[how][i]
                iou = None
                if r["polygon"]:
                    m = np.zeros((h, w), np.uint8)
                    cv2.fillPoly(m, [np.array(r["polygon"], np.int32)], 1)
                    iou = float((m & masks[i]).sum() / max(1, (m | masks[i]).sum()))
                    if vis is not None and how == "label":
                        cv2.polylines(vis, [np.array(r["polygon"], np.int32)], True, (0, 0, 255) if iou < 0.8 else (0, 160, 0), 2)
                if how == "box":  # the rectangle alone, as the baseline every outline has to beat
                    bm = np.zeros((h, w), np.uint8)
                    b = [int(round(v)) for v in boxes[i]]
                    bm[b[1]:b[3], b[0]:b[2]] = 1
                    rows.append(dict(plan=name, kind=kind, how="rect", iou=float((bm & masks[i]).sum() / max(1, (bm | masks[i]).sum())), why=None,
                                     outdoor=kind.startswith("Outdoor"), unnamed=kind == "Undefined", mode="rect", shared=False, closed=None))
                rows.append(dict(plan=name, kind=kind, how=how, iou=iou, why=r.get("why") if iou is None else None, closed=r.get("closed"), outdoor=kind.startswith("Outdoor"),
                                 unnamed=kind == "Undefined", mode=r.get("mode"), shared=bool(r.get("shared_with"))))
        if vis is not None:
            os.makedirs(out_dir, exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, name + ".jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
    ind = [r for r in rows if not r["outdoor"] and not r["unnamed"]]
    rect = {(r["plan"], i): r for i, r in enumerate([r for r in ind if r["how"] == "rect"])}
    bx = [r for r in ind if r["how"] == "box"]
    rc = [r for r in ind if r["how"] == "rect"]
    mean = lambda v: sum(v) / max(1, len(v))
    final = [b["iou"] if b["iou"] is not None else r["iou"] for b, r in zip(bx, rc)]
    print(f"indoor named rooms: {len(bx)} | mean IoU: rectangle only {mean([r['iou'] for r in rc]):.3f} -> with tracing {mean(final):.3f}"
          f" | rooms where the outline beats the rectangle by >0.02: {sum(f > r['iou'] + 0.02 for f, r in zip(final, rc))}, worse by >0.02: {sum(f < r['iou'] - 0.02 for f, r in zip(final, rc))}")
    for tag, sel in (("closed outline", lambda b: b.get("closed") is True), ("clipped to rectangle", lambda b: b.get("closed") is False), ("rectangle kept", lambda b: b["iou"] is None)):
        pairs = [(b, r) for b, r in zip(bx, rc) if sel(b)]
        print(f"   {tag:22s} {len(pairs):3d} rooms | mean IoU {mean([(b['iou'] if b['iou'] is not None else r['iou']) for b, r in pairs]):.3f} (rectangle alone {mean([r['iou'] for b, r in pairs]):.3f})")
    for how in ("box", "label"):
        for group, sel in (("indoor rooms with a name", lambda r: not r["outdoor"] and not r["unnamed"]), ("outdoor spaces", lambda r: r["outdoor"])):
            R = [r for r in rows if r["how"] == how and sel(r)]
            tr = [r for r in R if r["iou"] is not None]
            good = sum(r["iou"] >= 0.8 for r in tr)
            bad = sum(r["iou"] < 0.5 for r in tr)
            med = sorted(r["iou"] for r in tr)[len(tr) // 2] if tr else 0
            print(f"{how:5s} | {group:24s} | rooms {len(R):3d} | traced {len(tr):3d} | IoU>=0.8: {good:3d} ({100 * good / max(1, len(R)):.0f}% of all) | "
                  f"IoU<0.5 (wrong outline): {bad} | median IoU of traced {med:.2f}")
        why = {}
        for r in rows:
            if r["how"] == how and r["iou"] is None and not r["outdoor"] and not r["unnamed"]:
                why[r["why"]] = why.get(r["why"], 0) + 1
        print("      not traced (indoor, named):", why)
    per = {}
    for r in rows:
        if r["how"] == "label" and not r["outdoor"] and not r["unnamed"]:
            a = per.setdefault(r["plan"], [0, 0, 0])
            a[0] += 1; a[1] += r["iou"] is not None and r["iou"] >= 0.8; a[2] += r["iou"] is not None and r["iou"] < 0.5
    print("per plan (label route: rooms, good, wrong):", {k.split("-")[0][:4] + k.split("-")[-1]: v for k, v in per.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
