"""Minimal ASCII DXF entity reader (no ezdxf dependency)."""


def entities(path):
    lines = [l.rstrip("\r\n") for l in open(path, encoding="utf-8", errors="replace")]
    pairs = list(zip([x.strip() for x in lines[0::2]], lines[1::2]))
    start = next(k for k, (c, v) in enumerate(pairs) if c == "2" and v == "ENTITIES")
    out, cur = [], None
    for c, v in pairs[start + 1:]:
        if c == "0":
            if cur:
                out.append(cur)
            if v == "ENDSEC":
                break
            cur = {"type": v, "codes": []}
        else:
            cur["codes"].append((c, v))
    for i, e in enumerate(out):
        first = {}
        for c, v in e["codes"]:
            first.setdefault(c, v)
        e["idx"] = i
        e["layer"] = first.get("8")
        e["text"] = first.get("1")
        e["xs"] = [float(v) for c, v in e["codes"] if c == "10"]
        e["ys"] = [float(v) for c, v in e["codes"] if c == "20"]
        e["r"] = float(first["40"]) if "40" in first else None
        e["a0"] = float(first["50"]) if "50" in first else None
        e["a1"] = float(first["51"]) if "51" in first else None
        # TEXT alignment point (codes 11/21) when justification is set
        if e["type"] == "TEXT" and "11" in first and (first.get("72", "0") != "0" or first.get("73", "0") != "0"):
            e["pos"] = (float(first["11"]), float(first["21"]))
        elif e["xs"]:
            e["pos"] = (e["xs"][0], e["ys"][0])
        e["height"] = float(first["40"]) if e["type"] == "TEXT" and "40" in first else None
    return out


def bbox(e):
    if e["type"] in ("CIRCLE", "ARC"):
        r = e["r"]
        return e["xs"][0] - r, e["ys"][0] - r, e["xs"][0] + r, e["ys"][0] + r
    return min(e["xs"]), min(e["ys"]), max(e["xs"]), max(e["ys"])
