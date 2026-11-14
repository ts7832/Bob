"""Cost of one answer, measured while it is computed: model calls, tokens, GPU time and dollars.

tools/ui_server.py calls instrument() once at start and wraps each /api/ask in start() / summary(). Nothing in
drawqa/ is edited: the meter hooks the two places a model can be called from.

  Anthropic API   every anthropic.Anthropic client's messages.create and beta.messages.create (drawqa/llm_router.py):
                  the response's usage gives input/output/cache-read tokens; dollars from PRICES by the model that
                  actually answered (resp.model, so a server-side fallback is priced as the fallback model)
  Modal           every modal.Cls.from_name(...)().<method>.remote call (the open model in drawqa/reader.py and
                  drawqa/schedule.py): wall-clock seconds; dollars only if DRAWQA_GPU_USD_PER_HOUR is set, because
                  the rate depends on the GPU the service was deployed on

Everything else (the PDF text layer, geometry, tables, the local detector) runs on this machine and costs no API money.
A question answered from a cached model reply makes no call and is reported as such.
"""
import os, threading, time

# Anthropic first-party API, $ per million tokens (input, output, cache read). Source: Anthropic model table, 2026-11-03.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, None),  # $0.50 / $2.50 above 100K input tokens, handled below
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-opus-5": (5.00, 25.00, None),
    "claude-sonnet-5": (2.00, 10.00, None),
    "claude-opus-4-8": (5.00, 25.00, None),
}

_local = threading.local()


def start():
    _local.calls = []


def active():
    return getattr(_local, "calls", None) is not None


def _record(entry):
    if active():
        _local.calls.append(entry)


def price_claude(model, usage):
    """(usd or None, note). None when the model or a token kind has no known price: shown as tokens only."""
    base = next((k for k in PRICES if model == k or model.startswith(k + "-")), None)
    if base is None:
        return None, f"no price known for {model}"
    inp, out, cread = PRICES[base]
    tin = getattr(usage, "input_tokens", 0) or 0
    tout = getattr(usage, "output_tokens", 0) or 0
    tread = getattr(usage, "cache_read_input_tokens", 0) or 0
    twrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if base == "claude-haiku-5-5" and tin + tread + twrite > 100_000:
        inp, out = 0.50, 2.50
    if twrite:
        return None, f"{twrite} cache-write tokens have no price in this table"
    if tread and cread is None:
        return None, f"{tread} cache-read tokens have no price for {base}"
    usd = (tin * inp + tout * out + tread * (cread or 0)) / 1e6
    return usd, ""


def _wrap_create(create, kind):
    def wrapped(*a, **kw):
        t0 = time.time()
        resp = create(*a, **kw)
        usage = getattr(resp, "usage", None)
        model = getattr(resp, "model", None) or kw.get("model") or "?"
        usd, note = price_claude(model, usage) if usage is not None else (None, "no usage in the response")
        _record(dict(kind="anthropic", model=model, seconds=round(time.time() - t0, 2), usd=usd, note=note,
                     input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
                     cache_read_tokens=getattr(usage, "cache_read_input_tokens", None)))
        return resp
    wrapped._metered = True
    return wrapped


def _instrument_anthropic():
    try:
        import anthropic
    except ImportError:
        return "anthropic not installed (no Claude calls possible)"
    init = anthropic.Anthropic.__init__
    if getattr(init, "_metered", False):
        return "anthropic: already metered"

    def metered_init(self, *a, **kw):
        init(self, *a, **kw)
        for res in (self.messages, self.beta.messages):  # resources are cached on the client: patch the instance
            if not getattr(res.create, "_metered", False):
                res.create = _wrap_create(res.create, "anthropic")
    metered_init._metered = True
    anthropic.Anthropic.__init__ = metered_init
    return "anthropic: metered"


class _RemoteProxy:
    def __init__(self, fn, label):
        self._fn, self._label = fn, label

    def remote(self, *a, **kw):
        t0 = time.time()
        try:
            return self._fn.remote(*a, **kw)
        finally:
            secs = time.time() - t0
            rate = os.environ.get("DRAWQA_GPU_USD_PER_HOUR")
            usd = secs / 3600 * float(rate) if rate else None
            _record(dict(kind="modal", model=self._label, seconds=round(secs, 2), usd=usd,
                         note="" if rate else "GPU time only: set DRAWQA_GPU_USD_PER_HOUR to price it"))

    def __getattr__(self, name):
        return getattr(self._fn, name)


class _InstanceProxy:
    def __init__(self, obj, label):
        self._obj, self._label = obj, label

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        return _RemoteProxy(attr, f"{self._label}.{name}") if hasattr(attr, "remote") else attr


class _ClsProxy:
    def __init__(self, cls, label):
        self._cls, self._label = cls, label

    def __call__(self, *a, **kw):
        return _InstanceProxy(self._cls(*a, **kw), self._label)

    def __getattr__(self, name):
        return getattr(self._cls, name)


def _instrument_modal():
    try:
        import modal
    except ImportError:
        return "modal not installed (no open-model calls possible)"
    from_name = modal.Cls.from_name
    if getattr(from_name, "_metered", False):
        return "modal: already metered"

    def metered_from_name(app, name, *a, **kw):
        return _ClsProxy(from_name(app, name, *a, **kw), f"modal {app}/{name}")
    metered_from_name._metered = True
    modal.Cls.from_name = metered_from_name
    return "modal: metered"


def instrument():
    return [_instrument_anthropic(), _instrument_modal()]


def summary():
    """The cost of the answer just computed, for the page; ends the meter for this request."""
    calls = getattr(_local, "calls", None) or []
    _local.calls = None
    priced = [c["usd"] for c in calls if c["usd"] is not None]
    unpriced = [c for c in calls if c["usd"] is None]
    total = round(sum(priced), 6) if not unpriced else None
    if not calls:
        text = "no model call: read and computed by code on this machine"
    else:
        parts = []
        for c in calls:
            if c["kind"] == "anthropic":
                parts.append(f"{c['model']}: {c['input_tokens']} in / {c['output_tokens']} out tokens")
            else:
                parts.append(f"{c['model']}: {c['seconds']} s GPU")
        text = f"{len(calls)} model call{'s' if len(calls) > 1 else ''}: " + "; ".join(parts)
    return dict(usd=0.0 if not calls else total, usd_known=sum(priced), calls=calls, model_calls=len(calls), text=text,
                incomplete=[c["note"] for c in unpriced])
