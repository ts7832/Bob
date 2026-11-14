"""Build eval/test_scans.jsonl: the shared test questions (preview scans).

    python eval/build_test_set.py /path/to/junction-dataset/questions.jsonl

The test files are the 241 preview scans, taken from the file list in data/predictions/yolo_c26l_tta.jsonl.
Nobody trains or tunes on these files.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = [  # (kind, words that identify it in English or Finnish), first match wins
    ("bathroom", ["bathrooms or washrooms", "pesuhuonetta"]),
    ("wc_room", ["separate WC rooms", "erillistä WC-tilaa"]),
    ("toilet", ["toilet bowls", "WC-istuinta"]),
    ("window", ["windows", "ikkunaa"]),
    ("door", ["doors", "ovea"]),
    ("floor", ["floors (storeys)", "kerrosta"]),
    ("sink", ["sinks", "tiskiallasta"]),
    ("shower_bath", ["showers and bathtubs", "suihkua"]),
    ("sauna", ["saunas", "saunaa"]),
    ("kitchen", ["kitchens", "keittiötä"]),
    ("bedroom", ["bedrooms", "makuuhuonetta"]),
    ("balcony", ["balconies", "parveketta"]),
]


def kind_of(question):
    for kind, words in KINDS:
        if any(w in question for w in words):
            return kind
    return "other"


def main():
    names = {json.loads(l)["path"] for l in open(os.path.join(HERE, "..", "data", "predictions", "yolo_c26l_tta.jsonl"))}
    out = []
    for line in open(sys.argv[1]):
        q = json.loads(line)
        if q["path"].startswith("data/cubicasa5k/") and os.path.basename(q["path"]) in names:
            q["kind"] = kind_of(q["question"])
            q["lang"] = "fi" if q["question"].startswith("Kuinka") else "en"
            out.append(q)
    out.sort(key=lambda q: (q["path"], q["kind"], q["lang"]))
    with open(os.path.join(HERE, "test_scans.jsonl"), "w") as f:
        for i, q in enumerate(out):
            f.write(json.dumps({"id": f"s{i:04d}", **q}, ensure_ascii=False) + "\n")
    print(len(out), "questions on", len({q["path"] for q in out}), "files")


if __name__ == "__main__":
    main()
