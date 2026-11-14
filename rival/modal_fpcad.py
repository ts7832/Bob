"""FloorPlanCAD (CAD-style drawings with labelled doors, windows and fixtures) -> Modal volume, as a second training set.

    modal run rival/modal_fpcad.py --stage download     # Hugging Face Voxel51/FloorPlanCAD -> /data/fpcad
Licence: CC BY-NC 4.0 (original dataset), non-commercial use only.
"""
import json, os

import modal

APP = modal.App("bob-fpcad")
VOL = modal.Volume.from_name("bob-data")
D = "/data"
image = modal.Image.debian_slim(python_version="3.11").pip_install("huggingface_hub==0.26.5")


@APP.function(image=image, volumes={D: VOL}, timeout=4 * 3600, cpu=8, secrets=[modal.Secret.from_name("bob-hf")])
def download():
    import collections, glob
    from huggingface_hub import snapshot_download
    out = f"{D}/fpcad"
    import time
    for attempt in range(60):          # anonymous downloads are rate limited: go slowly, resume after each refusal
        try:
            snapshot_download("Voxel51/FloorPlanCAD", repo_type="dataset", local_dir=out, max_workers=16, token=os.environ.get("HF_TOKEN"))
            break
        except Exception as e:
            n = len(glob.glob(out + "/data/*.png"))
            print(f"attempt {attempt}: {type(e).__name__}, {n} images so far; waiting", flush=True)
            VOL.commit()
            time.sleep(90)
    VOL.commit()
    files = [f for f in glob.glob(out + "/**", recursive=True) if os.path.isfile(f) and "/.cache/" not in f]
    ext = collections.Counter(os.path.splitext(f)[1] for f in files)
    info = dict(files=len(files), gb=round(sum(os.path.getsize(f) for f in files) / 1e9, 2), ext=dict(ext),
                non_png=[(os.path.relpath(f, out), os.path.getsize(f)) for f in files if not f.endswith(".png")][:20])
    for name in ("samples.json", "metadata.json", "fiftyone.yml"):
        p = f"{out}/{name}"
        if os.path.exists(p):
            info[name] = open(p).read(3000)
    return info


@APP.local_entrypoint()
def main(stage: str = "download"):
    if stage == "download":
        print(json.dumps(download.remote(), indent=1)[:6000])
