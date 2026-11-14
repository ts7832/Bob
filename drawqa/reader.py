"""Room names and printed areas read off a drawing image by a vision-language model (the service in tools/modal_reader.py).

    read_rooms(path, img_bgr) -> dict(rooms=[{label, area, bbox}], model, seconds, truncated)  or  dict(error=...)
This is the open-vocabulary half of reading an image: the detector marks symbols from a fixed list, this reads whatever
names the drawing prints. It is a model reading text, so a letter or a digit can be misread: answers built on it are
labelled "read by a vision model", never "deterministic". Results are cached per file.
"""
import hashlib, json, os

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")


def _key(path):
    st = os.stat(path)
    return os.path.join(CACHE, "rooms_" + hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest() + ".json")


def cached(path):
    f = _key(path)
    return json.load(open(f)) if os.path.exists(f) else None


def rooms(path):
    """What the reader found on this file, if it was run: [dict(label, area, bbox)] or None when nothing is cached.
    area is a float in m2 or None; bbox = [x0, y0, x1, y1] in pixels of the original image, around the printed name."""
    hit = cached(path)
    return None if hit is None or hit.get("error") else hit.get("rooms", [])


def read_rooms(path, img=None):
    hit = cached(path)
    if hit is not None:
        return hit
    try:
        import cv2
        import modal
        if img is None:
            img = cv2.imread(path)
        png = cv2.imencode(".png", img)[1].tobytes()
        out = modal.Cls.from_name("drawqa-reader", "Reader")().read.remote(png)
    except Exception as e:  # no network, service not deployed, GPU not available
        return dict(error=f"room-name reader not available: {type(e).__name__}: {str(e)[:200]}")
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(_key(path), "w"))
    return out


def text(prompt, max_tokens=700, system="You are a careful assistant. Answer with JSON only."):
    """A text-only question to the same model on Modal (no image, no API key needed).
    Returns dict(text, model, seconds, truncated) or dict(error=...). 1-3 s when the service is warm; when it is cold
    the call waits for the model to load (minutes), so callers that must stay fast should check warm() first."""
    try:
        import modal
        return modal.Cls.from_name("drawqa-reader", "Reader")().text.remote(prompt, max_tokens, system)
    except Exception as e:
        return dict(error=f"language model on Modal not available: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":  # python -m drawqa.reader <image>
    import sys
    r = read_rooms(sys.argv[1])
    print(r.get("error") or f"{r['model']} | {r['seconds']} s | " + ", ".join(f"{x['label']}" + (f" {x['area']}" if x["area"] is not None else "") for x in r["rooms"]))
