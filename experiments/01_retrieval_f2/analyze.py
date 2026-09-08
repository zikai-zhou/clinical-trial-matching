"""Regenerate the F2 accuracy table from data/summary.json."""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
d = json.load(open(HERE / "data" / "summary.json"))
agg = d["aggregate"]["accuracy"]
rating = d["aggregate"]["mean_decision_rating"]

out = {
    name: {"rate": agg[key]["rate"], "match": agg[key]["match"],
           "total": agg[key]["total"], "mean_rating": rating[key]}
    for key, name in [("smt", "AEGIS"), ("llm_direct", "LLM-d"), ("trialgpt", "TG")]
}

md = "# §4.2 — Retrieval accuracy (judge-rated)\n\n| System | Rate (judge acc.) | Match | Mean rating |\n|---|---|---|---|\n"
for name, v in out.items():
    md += f"| {name} | {v['rate']:.3f} | {v['match']}/{v['total']} | {v['mean_rating']:.2f} |\n"
(HERE / "out").mkdir(exist_ok=True)
(HERE / "out" / "table.md").write_text(md)
(HERE / "out" / "numbers.json").write_text(json.dumps(out, indent=2))
print(md)
