"""Several Claude API keys, used in order: when one runs out of credits (or is refused), the next one takes over.

    import keypool; keypool.install()       # once per process, before any Claude call

Keys come from the Modal secret `bob-anthropic-pool`: ANTHROPIC_KEY_POOL = "oliver=sk-ant-...,kokoro=sk-ant-..." (in order),
plus ANTHROPIC_API_KEY (secret `bob-anthropic`) as the last resort when present. Every Claude call in the process, ours
and Aleks's (anything that uses the anthropic SDK's messages.create), goes through the pool:
  - credit balance too low, key invalid or not permitted -> that key is marked spent for this container, next key, same call
  - rate limited (429) -> the same call is tried once on the next key; the first key stays in use
keypool.last() says which key paid for the last call (by its name, never the key), for the audit trail.
"""
import os, threading

_lock = threading.Lock()
_keys = []          # [(name, key)]
_spent = set()
_state = threading.local()
_installed = False


def _load():
    pool = os.environ.get("ANTHROPIC_KEY_POOL", "")
    out = []
    for part in pool.split(","):
        part = part.strip()
        if not part or part.lower() == "none":
            continue
        name, _, key = part.partition("=") if "=" in part else (f"key{len(out) + 1}", "", part)
        if key.startswith("sk-"):
            out.append((name.strip(), key.strip()))
    own = os.environ.get("ANTHROPIC_API_KEY", "")
    if own.startswith("sk-") and own not in {k for _, k in out}:
        out.append(("team key (last resort)", own))
    return out


def _live():
    return [(n, k) for n, k in _keys if n not in _spent]


def status():
    return dict(keys=[n for n, _ in _keys], spent=sorted(_spent), in_use=(_live()[0][0] if _live() else None))


def last():
    return getattr(_state, "last", None)


def _is_spent_error(e):
    import anthropic
    msg = str(e).lower()
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    return isinstance(e, anthropic.BadRequestError) and ("credit balance" in msg or "billing" in msg or "spend limit" in msg)


def _wrap(orig):
    def create(self, *a, **kw):
        import anthropic
        tried_rate = False
        while True:
            live = _live()
            if not live:
                raise RuntimeError("every Claude API key in the pool is out of credits or refused: " + ", ".join(sorted(_spent)))
            name, key = live[0]
            if tried_rate and len(live) > 1:
                name, key = live[1]
            self._client.api_key = key
            try:
                r = orig(self, *a, **kw)
                _state.last = name
                return r
            except Exception as e:
                if _is_spent_error(e):
                    with _lock:
                        _spent.add(name)
                    print(f"[keypool] {name}: {type(e).__name__}, switching to the next key", flush=True)
                    continue
                if isinstance(e, anthropic.RateLimitError) and not tried_rate and len(live) > 1:
                    tried_rate = True
                    print(f"[keypool] {name}: rate limited, trying the next key once", flush=True)
                    continue
                raise
    create._keypool = True
    return create


def install():
    """Patch messages.create (plain and beta, sync) so every call goes through the pool. Safe to call twice."""
    global _keys, _installed
    if _installed:
        return status()
    _keys = _load()
    if not _keys:
        return status()
    os.environ["ANTHROPIC_API_KEY"] = _keys[0][1]       # clients made later start with the first key
    from anthropic.resources.messages import Messages
    Messages.create = _wrap(Messages.create)
    try:
        from anthropic.resources.beta.messages import Messages as BetaMessages
        BetaMessages.create = _wrap(BetaMessages.create)
    except Exception:
        pass
    _installed = True
    return status()
