"""Draw the CAD-style detector's boxes on a sheet, numbered, room doors and cupboard doors in different colours.

    python rival/cad_overlay.py rival/out/<name>.cad.json <image> [--min-door-m 0.6]
A door narrower than --min-door-m (its longer box side, at the sheet's scale) is drawn as a cupboard door and not counted.
"""
import collections, json, sys
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None
COL = {"door": (220, 40, 0), "double_door": (255, 140, 0), "cupboard door": (150, 150, 150), "window": (0, 110, 255),
       "toilet": (150, 0, 200), "basin": (0, 160, 90)}


def main():
    d = json.load(open(sys.argv[1]))
    min_m = float(sys.argv[sys.argv.index("--min-door-m") + 1]) if "--min-door-m" in sys.argv else 0.6
    im = Image.open(sys.argv[2]).convert("RGB")
    dr = ImageDraw.Draw(im)
    n = collections.Counter()
    for det in sorted(d["detections"], key=lambda t: (t["bbox"][1], t["bbox"][0])):
        x0, y0, x1, y1 = det["bbox"]
        cls = det["cls"]
        if cls in ("door", "double_door") and max(x1 - x0, y1 - y0) / d["px_per_m"] < min_m:
            cls = "cupboard door"
        n[cls] += 1
        dr.rectangle([x0, y0, x1, y1], outline=COL[cls], width=8)
        dr.text((x0 + 6, y0 - 46), f"{n[cls]}", fill=COL[cls], font_size=44)
    out = sys.argv[1].replace(".cad.json", ".cad.png")
    im.thumbnail((5000, 5000))
    im.save(out)
    print(dict(n), "->", out)


if __name__ == "__main__":
    main()
