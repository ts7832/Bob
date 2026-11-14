"""Jev-style router, small version: question in any wording or language -> calibrated probability of what is asked.

Labels: the dataset's questions, labelled by the engine's own pattern router (drawqa/router.py) and mapped to a meaning
(count:door, area:one_room, ...); questions it cannot read borrow the label of the same question in another language on
the same drawing with the same answer (jev/router_seed.json, built locally).
Rewordings: an open model (Qwen2.5-7B-Instruct, vLLM) rewrites sampled questions in casual, formal and short English and
in Finnish, German, French and Swedish, keeping names and numbers; plus out-of-scope questions labelled "other".
Model: multilingual-e5-small, fine-tuned with one classification head; temperature scaling on validation questions.
Test: rewordings of source questions never seen in training, and the original questions the pattern router cannot read.

    MODAL_PROFILE=kokoro-63208 modal run jev/modal_router.py
"""
import json, os, pathlib, random

import modal

app = modal.App("bob-jev-router")
VOL = modal.Volume.from_name("bob-data")
HERE = pathlib.Path(__file__).resolve().parent
D = "/data/jev"
GEN_MODEL = "Qwen/Qwen2.5-7B-Instruct"
ENC = "intfloat/multilingual-e5-small"

gen_image = (modal.Image.debian_slim(python_version="3.11").pip_install("vllm==0.6.6.post1", "transformers==4.47.1", "huggingface_hub[hf_transfer]")
             .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"}))
train_image = (modal.Image.debian_slim(python_version="3.11")
               .pip_install("torch==2.5.1", "transformers==4.47.1", "sentencepiece", "numpy<2"))

STYLES = ["casual, as a site manager would say it out loud", "very short", "formal", "with one small typo",
          "in Finnish", "in German", "in French", "in Swedish"]
OTHER_TOPICS = ["fire rating of a door", "who the architect is", "construction cost", "the deadline", "permit status",
                "facade material colour", "the weather on site", "how to install a window", "concrete curing time",
                "the client's phone number", "electrical load of the building", "the paint brand", "parking rules",
                "insulation thickness standards", "the contractor's insurance"]


@app.function(image=gen_image, gpu="L40S", volumes={"/data": VOL}, timeout=3600, secrets=[modal.Secret.from_name("bob-hf")])
def generate(seed: list, per_label: int = 40, seed_val: int = 0):
    from vllm import LLM, SamplingParams
    rnd = random.Random(seed_val)
    by = {}
    for r in seed:
        if r["router_reads_it"]:                       # the router's unreadable questions are kept for the test only
            by.setdefault(r["label"], []).append(r["question"])
    src = [(lab, q) for lab, qs in by.items() for q in rnd.sample(qs, min(per_label, len(qs)))]
    llm = LLM(GEN_MODEL, max_model_len=2048, gpu_memory_utilization=0.9)
    sp = SamplingParams(temperature=0.9, top_p=0.95, max_tokens=900)
    tok = llm.get_tokenizer()

    def chat(msg):
        return tok.apply_chat_template([{"role": "user", "content": msg}], tokenize=False, add_generation_prompt=True)
    prompts = [chat("Rewrite this question about a building floor plan in 8 different ways, one per line, numbered 1-8. "
                    "Keep exactly the same meaning and keep every name, number and storey name unchanged. Styles, in order: "
                    + "; ".join(f"{i + 1}) {s}" for i, s in enumerate(STYLES)) + ".\nQuestion: " + q) for _, q in src]
    others = [chat(f"Write 12 different short questions a construction site manager might ask about {t}. One per line, numbered. "
                   "Some in English, some in Finnish, German or French.") for t in OTHER_TOPICS]
    outs = llm.generate(prompts + others, sp)
    rows = []
    import re
    for (lab, q), o in zip(src, outs[:len(src)]):
        for line in o.outputs[0].text.splitlines():
            m = re.match(r"\s*\d+[.)]\s*(.+)", line)
            if m and 8 < len(m.group(1)) < 300:
                rows.append(dict(question=m.group(1).strip().strip('"'), label=lab, source=q))
    for t, o in zip(OTHER_TOPICS, outs[len(src):]):
        for line in o.outputs[0].text.splitlines():
            m = re.match(r"\s*\d+[.)]\s*(.+)", line)
            if m and 8 < len(m.group(1)) < 300:
                rows.append(dict(question=m.group(1).strip().strip('"'), label="other", source="other:" + t))
    os.makedirs(D, exist_ok=True)
    json.dump(rows, open(f"{D}/paraphrases.json", "w"), ensure_ascii=False)
    VOL.commit()
    return dict(sources=len(src), rows=len(rows), sample=[r["question"] for r in rnd.sample(rows, 12)])


@app.function(image=train_image, gpu="L4", volumes={"/data": VOL}, timeout=3600)
def train(seed: list, epochs: int = 4):
    import math, numpy as np, torch, torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer
    VOL.reload()
    para = json.load(open(f"{D}/paraphrases.json"))
    rnd = random.Random(1)
    srcs = sorted({r["source"] for r in para})
    rnd.shuffle(srcs)
    n = len(srcs)
    split = {s: ("test" if i < n * .2 else "val" if i < n * .3 else "train") for i, s in enumerate(srcs)}
    readable = [r for r in seed if r["router_reads_it"]]
    used = {r["source"] for r in para}
    rows = [dict(r, split=split[r["source"]]) for r in para]
    # the original questions too: readable ones whose source went to training, plus every original for its split
    for r in readable:
        rows.append(dict(question=r["question"], label=r["label"], source=r["question"], split=split.get(r["question"], "train")))
    hard = [dict(question=r["question"], label=r["label"], split="router_cannot_read") for r in seed if not r["router_reads_it"]]
    labels = sorted({r["label"] for r in rows})
    L = {l: i for i, l in enumerate(labels)}
    tr = [r for r in rows if r["split"] == "train"]
    # balance: the room-area questions dominate the dataset
    by = {}
    for r in tr:
        by.setdefault(r["label"], []).append(r)
    cap = 400
    tr = [x for l, rs in by.items() for x in (rnd.sample(rs, cap) if len(rs) > cap else rs)]
    tok = AutoTokenizer.from_pretrained(ENC)
    enc = AutoModel.from_pretrained(ENC).cuda()
    head = torch.nn.Linear(enc.config.hidden_size, len(labels)).cuda()

    def embed(qs):
        b = tok(["query: " + q for q in qs], padding=True, truncation=True, max_length=96, return_tensors="pt").to("cuda")
        h = enc(**b).last_hidden_state
        m = b["attention_mask"].unsqueeze(-1).float()
        return (h * m).sum(1) / m.sum(1)

    opt = torch.optim.AdamW(list(enc.parameters()) + list(head.parameters()), lr=4e-5, weight_decay=0.01)
    steps = epochs * math.ceil(len(tr) / 32)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=4e-5, total_steps=steps, pct_start=0.1)
    for ep in range(epochs):
        rnd.shuffle(tr)
        enc.train()
        for i in range(0, len(tr), 32):
            b = tr[i:i + 32]
            loss = F.cross_entropy(head(embed([r["question"] for r in b])), torch.tensor([L[r["label"]] for r in b]).cuda(), label_smoothing=0.05)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()

    def logits(rs):
        enc.eval()
        out = []
        with torch.no_grad():
            for i in range(0, len(rs), 128):
                out.append(head(embed([r["question"] for r in rs[i:i + 128]])).float().cpu())
        return torch.cat(out) if out else torch.zeros(0, len(labels))

    va = [r for r in rows if r["split"] == "val"]
    lv, yv = logits(va), torch.tensor([L[r["label"]] for r in va])
    best = min((F.cross_entropy(lv / t, yv).item(), t) for t in [x / 10 for x in range(5, 41)])[1]   # temperature scaling

    def report(rs):
        if not rs:
            return {}
        p = (logits(rs) / best).softmax(1)
        conf, pred = p.max(1)
        y = torch.tensor([L.get(r["label"], -1) for r in rs])
        ok = (pred == y).float()
        bins = {}
        for c, o in zip(conf.tolist(), ok.tolist()):
            bins.setdefault(min(int(c * 10), 9), []).append((c, o))
        gap = sum(abs(sum(c for c, _ in b) / len(b) - sum(o for _, o in b) / len(b)) * len(b) for b in bins.values()) / len(rs)
        sure = conf >= 0.8
        return dict(n=len(rs), accuracy=round(ok.mean().item(), 4), confidence_gap=round(gap, 4),
                    share_confident_80=round(sure.float().mean().item(), 3), accuracy_when_confident=round(ok[sure].mean().item(), 4) if sure.any() else None,
                    wrong_examples=[(r["question"][:90], r["label"], labels[int(k)], round(float(c), 2)) for r, k, c, o in zip(rs, pred, conf, ok) if not o][:6])
    te = [r for r in rows if r["split"] == "test"]
    res = dict(labels=len(labels), train=len(tr), val=len(va), temperature=best,
               test_rewordings=report([r for r in te if r["source"] != r["question"]]),
               router_cannot_read=report(hard))
    os.makedirs(f"{D}/router1", exist_ok=True)
    torch.save(dict(head=head.state_dict(), labels=labels, temperature=best), f"{D}/router1/head.pt")
    enc.save_pretrained(f"{D}/router1/encoder")
    tok.save_pretrained(f"{D}/router1/encoder")
    json.dump(dict(res, test_rows=[dict(question=r["question"], label=r["label"]) for r in te if r["source"] != r["question"]]), open(f"{D}/router1/report.json", "w"), ensure_ascii=False)
    VOL.commit()
    return res


@app.local_entrypoint()
def main(stage: str = "all", per_label: int = 40, epochs: int = 4):
    seed = json.load(open(HERE / "router_seed.json"))
    if stage in ("all", "gen"):
        g = generate.remote(seed, per_label)
        print(json.dumps(g, indent=1, ensure_ascii=False))
    if stage in ("all", "train"):
        print(json.dumps(train.remote(seed, epochs), indent=1, ensure_ascii=False))
