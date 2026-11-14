"""Download our trained models into models/ (they are not in git: too big, and the weights have their own licence).

    python tools/get_models.py            # needs the GitHub CLI (gh) logged in with access to ts7832/Bob

Files come from the GitHub release v0.1-models of ts7832/Bob and are checked against the checksums below. Files that
are already present with the right checksum are kept. Without these files the code still runs: by default our models
run on Modal (app 'drawqa-infer', DRAWQA_INFER=modal), which only needs access to that Modal workspace.

Models:
  detector.pt (+ detector.thresholds.json)   element detector e26l: YOLO26-l, doors, windows, fixtures, room types
  segmenter.pt (+ segmenter.eval.json)       walls and room outlines (YOLO segmentation)
Licence: trained with Ultralytics (AGPL-3.0) on CubiCasa5k (CC BY-NC-SA 4.0), Swiss Dwellings (CC BY 4.0) and BIM
sample models; treat the weights as non-commercial, share-alike (CC BY-NC-SA 4.0).
"""
import hashlib, os, shutil, subprocess, sys

REPO, TAG = "ts7832/Bob", "v0.1-models"
FILES = {
    "detector.pt": "f4ea8e0c1a31d0cb9fbd982193049cbabdd4cca7a1d85f456ebfcb910f4b954b",
    "detector.thresholds.json": "1ed52573538f7a22a7bfdcc6b1c47015f70ddbaa8eb9d3a95493c89c4428bbaa",
    "segmenter.pt": "de5374c9560a0551dafc33b6c91e7f1b2c206f92acf938dc2fe08d908b43b2ac",
    "segmenter.eval.json": "998649c3fd4573b28d9f41a9cf59d3d7d60febafe55c96627723945bbdc6cbc6",
}
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(DEST, exist_ok=True)
    need = [n for n, s in FILES.items() if not (os.path.exists(os.path.join(DEST, n)) and sha(os.path.join(DEST, n)) == s)]
    if not need:
        print("models/ is up to date"); return 0
    if not shutil.which("gh"):
        print(f"Install the GitHub CLI (gh) and log in, or download {', '.join(need)} from the release {TAG} of {REPO} into models/")
        return 1
    for n in need:
        r = subprocess.run(["gh", "release", "download", TAG, "--repo", REPO, "--pattern", n, "--dir", DEST, "--clobber"])
        if r.returncode:
            print(f"could not download {n}"); return 1
        if sha(os.path.join(DEST, n)) != FILES[n]:
            print(f"{n}: checksum does not match"); return 1
        print(f"{n}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
