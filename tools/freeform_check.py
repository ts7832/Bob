"""Free-form questions in Stoa's style, run through solve(llm=True). One drawing per process (8 GB Mac):

    .venv/bin/python tools/freeform_check.py swiss     # or: scan, ifc, all (all = one subprocess per drawing)

Warm the Modal model first or the first question waits minutes:
    .venv/bin/python -c "from drawqa import reader; print(reader.text('Reply with {}', 20))"

`expect` is a value we know from the drawing (checked by hand or by the scoreboard tools); None = read the output.
"""
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = "junction-dataset-preview/data/"
SETS = {
    "swiss": (ROOT + "swiss_dwellings/site1102_bldg2242_floor10281.pdf", [
        ("which apartment is bigger, A or B?", "Apartment B"),
        ("How much bigger is apartment A than B?", -10.83),
        ("How many doors and windows are there altogether?", 34),
        ("Kuinka monta ovea ja ikkunaa on yhteensä?", 34),
        ("How many square meters of tiling should I order for the toilets?", 10.31),
        ("What are the areas of apartments A and B?", "Apartment A: 83.51 m²; Apartment B: 94.34 m²"),
        ("How much would it cost to renovate the kitchen?", "refuse"),
        ("What is the widest window?", 1.94),
        ("How many metres of skirting board do I need for apartment A?", 108.05),
        ("How many square metres of wall tiles for the bathrooms if the ceiling is 2.6 m high?", 59.57),
        ("How much wall area is there to paint in the kitchen?", 56.35),  # both kitchens, assumed 2.5 m: estimated
        ("Total are in m squared of all rooms minus 10", 183.48),  # arithmetic from the question, done by code
        ("Total area of all rooms in square ft", 2082.6),  # unit conversion
        ("How wide is the widest window in cm?", 194.0),
        ("How many square meters of tiling for the toilets, plus 10% for waste?", 11.34),
        ("How many bedrooms are there?", "refuse"),  # rooms are only 'Stanza' (= room): not said which are bedrooms
    ]),
    "scan": (ROOT + "cubicasa5k/high_quality_architectural-16.png", [
        ("How many square meters of tiling should I order for the toilets?", 10.0),
        ("How many bedrooms and bathrooms are there in total?", 2),
        ("Which is bigger, the kitchen or the living room?", None),
        ("How many doors and windows are there altogether?", None),
        ("combien de chambres?", None),
        ("How wide are the doors?", None),
        ("How many metres of skirting board for the bedrooms?", None),
        ("How much wall area is there to tile in the bathroom?", None),
    ]),
    "ifc": (ROOT + "ifc_models/bsc-esplanades_1807-ep-ar-v18.ifc", [
        ("How many doors and windows are in the whole building?", 508),
        ("Which storey has the largest floor area?", "1.korrus"),
        ("How many rooms are on each storey?", "Vundament: 0; 1.korrus: 50; 2.korrus: 60; 3.korrus: 60; 4.korrus: 60; 5.korrus: 55; Katus: 0"),
        ("How many doors are there on the second floor?", 42),
        ("How many doors are there?", 211),
        ("How wide are the doors on 2.korrus?", "0.80 m × 2, 0.90 m × 2, 1.00 m × 35, 1.45 m × 1, 2.20 m × 2"),
        ("What is the widest window in the building?", 2.85),
        ("What colour are the walls?", "refuse"),
    ]),
}


def check(answer, expect):
    if expect is None:
        return "  (read)"
    if expect == "refuse":
        return "  ok" if answer is None else "  WRONG: should refuse"
    if isinstance(expect, (int, float)) and isinstance(answer, (int, float)):
        return "  ok" if abs(answer - expect) <= 0.011 + 0.001 * abs(expect) else f"  WRONG: expected {expect}"
    return "  ok" if str(answer) == str(expect) else f"  WRONG: expected {expect}"


def run(name):
    from drawqa.solve import solve
    path, questions = SETS[name]
    print(f"== {name}: {path}", flush=True)
    for q, expect in questions:
        t = time.time()
        r = solve(path, q, True, llm=True)
        steps = r["intent"].get("steps") if isinstance(r.get("intent"), dict) else None
        print(f"Q: {q}\n   -> {r['answer']} {r.get('unit') or ''} | {r['honesty']['tier']} | {time.time() - t:.1f}s"
              f"{' | plan: ' + r['intent']['combine'] + ' of ' + ', '.join(s['intent'] for s in steps) if steps else ''}"
              f"{check(r['answer'], expect)}\n"
              f"   {(r.get('explanation') or '')[:300]}", flush=True)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        for name in SETS:
            subprocess.run([sys.executable, __file__, name], check=False)
    else:
        run(which)
