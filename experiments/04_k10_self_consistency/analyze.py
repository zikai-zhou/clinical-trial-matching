"""§4.3 — Self-consistency: k=10 run-to-run flip rate.

Reads:
  evaluation/results/data/h1_fliprate_235pairs_10repeats__smt__CORRECTED.json
  evaluation/results/data/h1_fliprate_235pairs_10repeats__llm_direct-trialgpt.json

Each JSON has a `per_pair` dict of the form
  {pair: {system: {labels: [...10 decisions...], ...}}}

Writes:
  paper/numbers/03_fliprate_k10.json
  paper/tables/03_fliprate_k10.md
"""
from __future__ import annotations
import json, math, pathlib, random
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
R = HERE
OUT_NUM = HERE / "out"
OUT_TAB = HERE / "out"
OUT_NUM.mkdir(parents=True, exist_ok=True)
OUT_TAB.mkdir(parents=True, exist_ok=True)


def pair_afr(labels):
    n = len(labels)
    if n < 2: return 0.0
    total = n * (n - 1) // 2
    disagree = sum(1 for i in range(n) for j in range(i+1, n) if labels[i] != labels[j])
    return disagree / total


def pair_entropy(labels):
    """Shannon entropy in bits (log base 2)."""
    c = Counter(labels)
    total = sum(c.values())
    if total == 0: return 0.0
    ent = 0.0
    for v in c.values():
        if v == 0: continue
        p = v / total
        ent -= p * math.log2(p)
    return ent


def summarize(per_pair_labels: dict) -> dict:
    """per_pair_labels: {pair_id: [10 decision labels]}"""
    if not per_pair_labels:
        return {"mean_afr": None, "ci_lo": None, "ci_hi": None,
                "mean_entropy": None, "pairs_with_flip": 0, "n_pairs": 0}
    afrs = {p: pair_afr(l) for p, l in per_pair_labels.items()}
    ents = {p: pair_entropy(l) for p, l in per_pair_labels.items()}
    mean_afr = sum(afrs.values()) / len(afrs)
    mean_ent = sum(ents.values()) / len(ents)
    pairs_w_flip = sum(1 for v in afrs.values() if v > 0)
    # Bootstrap
    random.seed(42)
    vals = list(afrs.values())
    rates = []
    for _ in range(1000):
        s = [random.choice(vals) for _ in vals]
        rates.append(sum(s)/len(s))
    rates.sort()
    return {"mean_afr": mean_afr, "ci_lo": rates[25], "ci_hi": rates[975],
            "mean_entropy": mean_ent,
            "pairs_with_flip": pairs_w_flip, "n_pairs": len(afrs)}


def _load_per_pair_labels(path, system_key):
    d = json.load(open(path))
    pp = d.get("per_pair") or {}
    out = {}
    for pair, systems in pp.items():
        if system_key in systems:
            labels = systems[system_key].get("labels")
            if labels:
                out[pair] = labels
    return out


def main() -> dict:
    smt_path = R / "data/h1_fliprate_235pairs_10repeats__smt__CORRECTED.json"
    if not smt_path.exists():
        smt_path = R / "data/h1_fliprate_235pairs_10repeats__smt.json"
    other_path = R / "data/h1_fliprate_235pairs_10repeats__llm_direct-trialgpt.json"

    aegis = _load_per_pair_labels(smt_path, "smt")
    llmd = _load_per_pair_labels(other_path, "llm_direct")
    tg   = _load_per_pair_labels(other_path, "trialgpt")

    results = {"AEGIS": summarize(aegis), "LLM_d": summarize(llmd), "TG": summarize(tg)}
    (OUT_NUM / "03_fliprate_k10.json").write_text(json.dumps(results, indent=2))

    md = "# §4.3 — $k{=}10$ run-to-run self-consistency (n=235 pairs × 10 repeats)\n\n"
    md += "| System | Mean AFR | 95% CI | Mean entropy | Pairs w/ flip |\n|---|---|---|---|---|\n"
    for sys_, r in results.items():
        if r["n_pairs"] == 0:
            md += f"| {sys_} | — | — | — | 0/0 (no data) |\n"; continue
        md += (f"| {sys_} | {r['mean_afr']:.3f} "
               f"| [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}] "
               f"| {r['mean_entropy']:.3f} "
               f"| {r['pairs_with_flip']}/{r['n_pairs']} |\n")
    (OUT_TAB / "03_fliprate_k10.md").write_text(md)
    print(md)
    return results


if __name__ == "__main__":
    main()
