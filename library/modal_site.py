"""The team page (library/team_server.py on top of tools/ui_server.py and demo/server.py) hosted on Modal, so anyone can
open it in a browser without running anything: same code as on the laptop, one always-on container.

    MODAL_PROFILE=oliver-universitapps modal deploy library/modal_site.py

- dataset files: the bob-dataset volume (mounted as junction-dataset-preview), so BIM and Swiss plans open with their
  exact readers; uploads and finished readings are kept on the bob-site volume
- drawing reading: drawqa here, the detector/segmenter on the drawqa-infer GPU (same workspace)
- answers: the library on Modal (bob-library), as on the laptop
- Opus live reading: Claude Code in this container. Here it bills an API key (no Claude plan in the cloud): the first key
  of the bob-anthropic-pool secret, else the team key (bob-anthropic)
- one shared password: BOB_SITE_PASSWORD (default: the demo workspace password)
"""
import os, pathlib, subprocess, time

import modal

app = modal.App("bob-site")
REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = modal.Volume.from_name("bob-dataset")
SITE = modal.Volume.from_name("bob-site", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "curl", "ca-certificates", "git")
    .run_commands("curl -fsSL https://deb.nodesource.com/setup_20.x | bash -", "apt-get install -y nodejs",
                  "npm install -g @anthropic-ai/claude-code")
    .pip_install("opencv-python-headless", "pymupdf", "numpy<2", "pillow", "ezdxf", "ifcopenshell", "anthropic", "modal",
                 "rapidocr==3.10.0", "onnxruntime", "shapely", "segno")
    .env({"DRAWQA_INFER": "modal", "BOB_HOST": "0.0.0.0", "PORT": "8770", "PYTHONUNBUFFERED": "1"})
    .add_local_dir(str(REPO / "drawqa"), "/repo/drawqa", ignore=["__pycache__"])
    .add_local_dir(str(REPO / "demo"), "/repo/demo", ignore=["__pycache__"])
    .add_local_dir(str(REPO / "tools"), "/repo/tools", ignore=["__pycache__", "*.ipynb"])
    .add_local_dir(str(REPO / "library"), "/repo/library", ignore=["__pycache__", "eval_out"])
    .add_local_dir(str(REPO / "ui"), "/repo/ui", ignore=["concepts"])
    .add_local_dir(str(REPO / "viz" / "dist"), "/repo/viz/dist")
    .add_local_dir(str(REPO / "gold"), "/repo/gold")
    .add_local_dir(str(REPO / "models"), "/repo/models")
    .add_local_dir(str(REPO / "data" / "predictions"), "/repo/data/predictions")
)


def _keys():
    """Claude Code reads ANTHROPIC_API_KEY: the first key of the pool (Oliver's, then kokoro's), else the team key."""
    for part in os.environ.get("ANTHROPIC_KEY_POOL", "").split(","):
        key = part.partition("=")[2].strip() if "=" in part else part.strip()
        if key.startswith("sk-"):
            return key
    return os.environ.get("ANTHROPIC_API_KEY", "")


@app.function(image=image, volumes={"/ds": DATA, "/site": SITE}, cpu=4, memory=8192, min_containers=1, max_containers=1,
              timeout=86400, secrets=[modal.Secret.from_name("bob-anthropic"), modal.Secret.from_name("bob-anthropic-pool"),
                                      modal.Secret.from_dict({"BOB_SITE_PASSWORD": os.environ.get("BOB_SITE_PASSWORD", "123456789")})])
@modal.concurrent(max_inputs=200)
@modal.web_server(port=8770, startup_timeout=300)
def web():
    for d in ("/site/uploads", "/site/cache"):
        os.makedirs(d, exist_ok=True)
    links = {"/repo/junction-dataset-preview": "/ds", "/repo/data/uploads": "/site/uploads", "/repo/data/cache": "/site/cache"}
    for link, target in links.items():
        if not os.path.lexists(link):
            os.symlink(target, link)
    env = dict(os.environ, ANTHROPIC_API_KEY=_keys())
    subprocess.Popen(["python", "library/team_server.py"], cwd="/repo", env=env)

    def keep():                                # what the site writes (uploads, readings) reaches the volume regularly
        while True:
            time.sleep(60)
            try:
                SITE.commit()
            except Exception as e:
                print("site volume commit:", e)
    import threading
    threading.Thread(target=keep, daemon=True).start()
