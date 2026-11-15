"""Test the photo matcher with simulated phone photos: each stored drawing is printed 'on a desk' (perspective tilt,
rotation, blur, uneven light, part of the sheet cut off), sent to /api/scan, and the match is compared with the truth.

    python library/test_scan.py --n 40 <files that are in the library>
"""
import argparse, io, json, os, random, sys, urllib.request
import cv2, numpy as np, pymupdf

URL = os.environ.get("BOB_LIBRARY", "https://oliver-universityapps--bob-library-web.us-east.modal.run")
PW = os.environ["BOB_PASSWORD"]   # the workspace password (Modal secret bob-site-password; never in the repo)


def photo(path, rng, hard):
    page = pymupdf.open(path)[0]
    k = 1500 / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), alpha=False)
    im = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    h, w = im.shape[:2]
    W, H = 1600, 1200
    desk = np.full((H, W, 3), (rng.randint(60, 110), rng.randint(70, 120), rng.randint(90, 150)), np.uint8)   # brownish desk
    desk = cv2.add(desk, rng_noise(rng, desk.shape, 12))
    s = rng.uniform(0.62, 0.8) if not hard else rng.uniform(0.85, 1.15)        # hard: the sheet runs out of the picture
    cx, cy = W / 2 + rng.uniform(-80, 80), H / 2 + rng.uniform(-60, 60)
    ang = np.deg2rad(rng.uniform(-18, 18) + rng.choice([0, 0, 90, 180]))
    hw, hh = w * s * W / max(w, h) / 2, h * s * W / max(w, h) / 2
    corners = np.float32([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]])
    R = np.float32([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    tilt = rng.uniform(0.75, 0.95)                                               # far edge looks shorter
    corners[:2] *= np.float32([tilt, 1.0])
    dst = (corners @ R.T + np.float32([cx, cy])).astype(np.float32)
    dst += np.float32([[rng.uniform(-15, 15), rng.uniform(-15, 15)] for _ in range(4)])
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), dst)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    paper = cv2.warpPerspective(im, M, (W, H), flags=cv2.INTER_AREA, borderValue=(0, 0, 0))
    out = np.where(mask[:, :, None] > 0, paper, desk)
    yy, xx = np.mgrid[0:H, 0:W]
    light = 0.7 + 0.5 * (xx / W * rng.uniform(-0.6, 0.6) + yy / H * rng.uniform(-0.6, 0.6) + 0.5)   # uneven light
    out = np.clip(out.astype(np.float32) * light[:, :, None], 0, 255).astype(np.uint8)
    out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.6, 1.6))
    out = cv2.add(out, rng_noise(rng, out.shape, 6))
    ok, jpg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return jpg.tobytes(), out


def rng_noise(rng, shape, amp):
    return np.random.default_rng(rng.randint(0, 1 << 30)).integers(0, amp, shape, dtype=np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--save", default="")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    rng = random.Random(5)
    files = rng.sample(a.files, min(a.n, len(a.files)))
    res = dict(easy=[0, 0, 0], hard=[0, 0, 0])
    times = []
    for i, f in enumerate(files):
        hard = i % 2 == 1
        jpg, img = photo(f, rng, hard)
        if a.save and i < 4:
            cv2.imwrite(os.path.join(a.save, f"photo{i}.jpg"), img)
        req = urllib.request.Request(URL + "/api/scan", data=jpg, method="POST", headers={"x-bob-password": PW, "content-type": "image/jpeg"})
        r = json.load(urllib.request.urlopen(req, timeout=300))
        true = os.path.splitext(os.path.basename(f))[0]
        k = res["hard" if hard else "easy"]
        k[2] += 1
        times.append(r["seconds"])
        if r["matched"] and r["sheet"]["name"] == true:
            k[0] += 1
        elif r["matched"]:
            k[1] += 1
            print("  WRONG:", true, "->", r["sheet"]["name"], r["details"])
        elif r["look_alikes"]:
            k.append(1) if true in [c["name"] for c in r["look_alikes"]] else None
            print("  asks the user to pick:", true[-22:], "among", [(c["name"][-18:], c["details"]) for c in r["look_alikes"]])
        else:
            print("  not found:", true, [(c["name"][-18:], c["details"]) for c in r["candidates"][:3]])
    for kind, (ok, wrong, n, *_) in res.items():
        ok, wrong, n, pick = ok, wrong, n, len(_)
        print(f"{kind}: {ok}/{n} matched right, {wrong} wrong sheet, {pick} asked the user to pick (right sheet offered), {n - ok - wrong - pick} not found")
    print(f"seconds per scan: median {sorted(times)[len(times) // 2]}, max {max(times)}")


if __name__ == "__main__":
    main()
