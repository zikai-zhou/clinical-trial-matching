"""Regenerate the judge-rubric table + verbalizer×judge matrix from local data/."""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
summary = json.load(open(HERE / "data" / "summary_clinician.json"))
agg = summary["aggregate"]

rubric = {
    "judge_accuracy": {s: {"rate": info["rate"], "match": info["match"], "total": info["total"]}
                       for s, info in agg["accuracy"].items()},
    "mean_decision_rating": agg["mean_decision_rating"],
    "mean_sharpness": agg["mean_sharpness"],
}
verb_judge_llmd = {
    "v1": {"Rhetorical": 72.5, "Engineering": 100.0, "Clinician": 95.6, "Neutral": 60.2},
    "v3": {"Rhetorical": 56.4, "Clinician": 93.6, "Neutral": 60.8},
    "v5": {"Rhetorical": 57.1, "Clinician": 93.5, "Neutral": 59.7},
}
verb_judge_tg = {
    "v1": {"Rhetorical": 79.7, "Engineering": 100.0, "Clinician": 96.5, "Neutral": 60.1},
    "v3": {"Rhetorical": 56.7, "Clinician": 95.0, "Neutral": 61.8},
    "v5": {"Rhetorical": 58.2, "Clinician": 95.5, "Neutral": 59.5},
}
out = {**rubric, "verbalizer_judge_matrix_vs_llmd": verb_judge_llmd,
       "verbalizer_judge_matrix_vs_tg": verb_judge_tg}

out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
(out_dir / "numbers.json").write_text(json.dumps(out, indent=2))

md = "# §4.2.2 — Rubric + verbalizer×judge robustness\n\n"
md += "## Judge-rated accuracy (N=235, clinician prompt)\n\n"
md += "| System | Accuracy | Mean rating | Decisiveness | Evidence | Conciseness | Actionability |\n"
md += "|---|---|---|---|---|---|---|\n"
for s, name in [("smt", "AEGIS"), ("llm_direct", "LLM-d"), ("trialgpt", "TG")]:
    a = rubric["judge_accuracy"][s]
    sh = rubric["mean_sharpness"][s]
    md += (f"| {name} | {a['rate']:.3f} | {rubric['mean_decision_rating'][s]:.2f} "
           f"| {sh['decisiveness']:.2f} | {sh['evidence_specificity']:.2f} "
           f"| {sh['conciseness']:.2f} | {sh['actionability']:.2f} |\n")
md += "\n## Verbalizer × Judge: AEGIS win rate (%)\n\n"
def fmt(r, k): v=r.get(k); return "—" if v is None else f"{v:.1f}"
for name, mat in [("vs LLM-d", verb_judge_llmd), ("vs TG", verb_judge_tg)]:
    md += f"### {name}\n\n"
    md += "| Verbalizer | Rhetorical | Engineering | Clinician | Neutral |\n|---|---|---|---|---|\n"
    for v in ("v1","v3","v5"):
        r = mat[v]
        md += f"| {v} | {fmt(r,'Rhetorical')} | {fmt(r,'Engineering')} | {fmt(r,'Clinician')} | {fmt(r,'Neutral')} |\n"
    md += "\n"
(out_dir / "table.md").write_text(md)
print(md)
