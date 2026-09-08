"""Regenerate LLM-d explanation-consistency table from data/."""
import json, pathlib, statistics
HERE = pathlib.Path(__file__).resolve().parent
d = json.load(open(HERE / "data" / "exp_explanation_consistency.json"))
flipped = [r for r in d if r.get("llm_flipped") and r.get("consistency") is not None]
not_flipped = [r for r in d if r.get("llm_flipped") is False and r.get("consistency") is not None]

def summ(rows):
    if not rows: return None
    cs = [r["consistency"] for r in rows]
    return {"n": len(rows), "mean": statistics.mean(cs), "median": statistics.median(cs),
            "dist": {str(i): sum(1 for x in cs if x == i) for i in range(1, 6)},
            "low_count": sum(1 for x in cs if x <= 2)}

out = {"flipped": summ(flipped), "not_flipped": summ(not_flipped)}
out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
(out_dir / "numbers.json").write_text(json.dumps(out, indent=2))
md = "# §4.4.5 — Explanation consistency (LLM-d)\n\n| Behavior | n | Mean | Median | Low (≤2) |\n|---|---|---|---|---|\n"
for lbl, k in [("Flipped","flipped"),("Did not flip","not_flipped")]:
    v = out[k]
    if v: md += f"| {lbl} | {v['n']} | {v['mean']:.2f} | {v['median']:.1f} | {v['low_count']} |\n"
(out_dir/"table.md").write_text(md); print(md)
