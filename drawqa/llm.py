"""One switch for the language model behind the answer agent (drawqa/ask.py).

    chat(messages, tools=None, max_tokens=4000) -> OpenAI-style reply dict (choices[0].message, usage, _seconds, _model)

Backend now: Kimi K3.1 Pro on our own Modal endpoint (OpenAI-style chat with tool calls and images; the key is read
from ~/.config/drawqa/modal_endpoint_auth and never printed). DRAWQA_ANSWER_LLM=kimi (default) selects it.
Later: a Claude backend (Anthropic SDK) behind the same function, chosen when ANTHROPIC_API_KEY is set.
The endpoint scales to zero: the first call after a pause can take minutes (warm it before a demo: warm()).
"""
import base64, io, json, os, time, urllib.request

KIMI_URL = os.environ.get("DRAWQA_KIMI_URL", "https://politovalexander15--ep-kimi-k3-1-pro-server.us-west.modal.direct")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshotai/Kimi-K3.1-Pro")
AUTH = os.path.expanduser("~/.config/drawqa/modal_endpoint_auth")


class LLMError(Exception):
    pass


def backend():
    return os.environ.get("DRAWQA_ANSWER_LLM", "kimi")


def available():
    return backend() == "kimi" and os.path.exists(AUTH)


def _headers():
    tok = open(AUTH).read().strip()
    key, secret = tok.split(".", 1)
    return {"Authorization": f"Bearer {tok}", "Modal-Key": key, "Modal-Secret": secret, "Content-Type": "application/json"}


QWEN_PROTOCOL = """You can use these tools. Reply with ONE JSON object and nothing else:
{"tool": "<name>", "args": {...}}
Tools:
{tools}"""


def _qwen_chat(messages, tools, max_tokens):
    """Qwen3-VL-32B on our Modal reader service (drawqa/reader.text): one text call per turn, no native tool calls,
    8k context. The conversation is flattened into one prompt and the model answers with one JSON action, which is
    turned into an OpenAI-style tool call so the caller's loop is the same as for Kimi."""
    import re
    from . import reader
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    if tools:
        spec = "\n".join(f"- {t['function']['name']}: {t['function']['description']} Arguments: "
                         f"{json.dumps(t['function']['parameters'].get('properties', {}))}" for t in tools)
        system += "\n\n" + QWEN_PROTOCOL.replace("{tools}", spec)
    parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        c = m.get("content")
        c = c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
        if m["role"] == "assistant" and m.get("tool_calls"):
            c = " ".join(json.dumps({"tool": t["function"]["name"], "args": json.loads(t["function"].get("arguments") or "{}")}) for t in m["tool_calls"])
        if m["role"] == "tool":
            c = c[:1500]  # 8k context: tool results are cut short
        parts.append(f"[{m['role']}] {c}")
    prompt = "\n\n".join(parts)[-18000:]
    t0 = time.time()
    r = reader.text(prompt, max_tokens, system)
    if r.get("error"):
        raise LLMError(r["error"])
    txt = r.get("text") or ""
    msg = {"role": "assistant", "content": txt}
    m = re.search(r"\{.*\}", txt, re.S)
    if tools and m:
        try:
            act = json.loads(m.group(0))
            if isinstance(act, dict) and act.get("tool"):
                msg = {"role": "assistant", "content": "", "tool_calls": [{"id": f"q{int(time.time() * 1000)}", "type": "function",
                       "function": {"name": act["tool"], "arguments": json.dumps(act.get("args") or {})}}]}
        except Exception:
            pass
    return {"choices": [{"message": msg}], "usage": {}, "_seconds": round(time.time() - t0, 1), "_model": r.get("model", "Qwen3-VL-32B")}


def chat(messages, tools=None, max_tokens=4000, temperature=0.2, timeout=600, backend_name=None):
    if (backend_name or backend()) == "qwen":
        return _qwen_chat(messages, tools, min(max_tokens, 1200))
    if not available():
        raise LLMError("no answer model configured (Kimi key file missing, or DRAWQA_ANSWER_LLM is not 'kimi')")
    body = dict(model=KIMI_MODEL, messages=messages, max_tokens=max_tokens, temperature=temperature)
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(KIMI_URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers=_headers())
    t0 = time.time()
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as e:  # network, cold start beyond the timeout, HTTP error
        raise LLMError(f"answer model call failed: {type(e).__name__}: {str(e)[:200]}")
    r["_seconds"] = round(time.time() - t0, 1)
    r["_model"] = KIMI_MODEL
    return r


def image_part(pil_img, fmt="PNG"):
    buf = io.BytesIO()
    pil_img.save(buf, fmt)
    return {"type": "image_url", "image_url": {"url": f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()}}


def warm():
    """One tiny call so the endpoint is up before a demo. Returns seconds taken."""
    r = chat([{"role": "user", "content": "Reply with OK."}], max_tokens=5, timeout=900)
    return r["_seconds"]
