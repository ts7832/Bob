"""Our trained models on a Modal GPU instead of this computer (the service is tools/modal_infer.py, app 'drawqa-infer').

    DRAWQA_INFER=modal (default) -> detector, wall/room model and OCR run on Modal; DRAWQA_INFER=local -> on this machine.
The service runs the same drawqa code with the same weights and library versions, so results are the same; only the
place changes. Inside the service DRAWQA_IN_MODAL=1, so there the functions run for real. If the service cannot be
reached, the caller falls back to running locally and says so on stderr (a slow answer beats no answer).
"""
import os, sys, time

APP, CLS = "drawqa-infer", "Infer"
_cls = None
last = {}  # what the last remote call cost: {"step": seconds}


def enabled():
    return os.environ.get("DRAWQA_IN_MODAL") != "1" and os.environ.get("DRAWQA_INFER", "modal") == "modal"


def _svc():
    global _cls
    if _cls is None:
        import modal
        _cls = modal.Cls.from_name(APP, CLS)()
    return _cls


def _png(img):
    import cv2
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes()


def call(step, img, **kw):
    """Run one step (detect / outlines / ocr / textlayer) on Modal. Returns the result, or raises."""
    t0 = time.time()
    out = getattr(_svc(), step).remote(_png(img), **kw)
    last[step] = round(time.time() - t0, 1)
    return out


def try_call(step, img, **kw):
    """call(), or None (with a note on stderr) when the service is not reachable: the caller then runs locally."""
    try:
        return call(step, img, **kw)
    except Exception as e:
        print(f"[drawqa.remote] {step} on Modal failed ({type(e).__name__}: {str(e)[:160]}); running it locally", file=sys.stderr)
        return None


def warm():
    """Start the GPU container and load the models (do this before a demo). Returns seconds."""
    t0 = time.time()
    _svc().ping.remote()
    return round(time.time() - t0, 1)
