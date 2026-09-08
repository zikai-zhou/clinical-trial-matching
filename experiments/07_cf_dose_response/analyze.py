"""§4.4.3 — Dose-response curves."""
from __future__ import annotations
import json, pathlib
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
R = HERE
OUT_NUM = HERE / "out"
OUT_TAB = HERE / "out"
OUT_NUM.mkdir(parents=True, exist_ok=True)
OUT_TAB.mkdir(parents=True, exist_ok=True)


def main() -> dict:
    d = json.load(open(R/"data/cf_dose_response_60"/"all_results.json"))
    valid = [r for r in d if r.get("cf_flips")]
    buckets = defaultdict(list)
    for r in valid:
        buckets[r["dose"]].append(r)

    out = {}
    for dose in [1, 2, 3, "all"]:
        rows = buckets.get(dose, [])
        if not rows: continue
        n = len(rows)
        avg_n = sum(r.get("n_targets_applied", 0) for r in rows) / n
        out[str(dose)] = {
            "n_pairs": n, "avg_facts_applied": avg_n,
            "AEGIS": sum(1 for r in rows if r["cf_flips"].get("smt")) / n,
            "LLM_d": sum(1 for r in rows if r["cf_flips"].get("llm_d")) / n,
            "TG": sum(1 for r in rows if r["cf_flips"].get("tg")) / n,
        }

    # First-flip distribution
    by_pair = defaultdict(dict)
    for r in valid:
        by_pair[r["pair"]][r["dose"]] = r["cf_flips"]
    def first_flip(pair_doses, sys_):
        for d_ in [1, 2, 3, "all"]:
            if pair_doses.get(d_, {}).get(sys_): return d_
        return None
    first_flip_hist = {}
    for sys_ in ("smt","llm_d","tg"):
        firsts = [first_flip(pd, sys_) for pd in by_pair.values()]
        first_flip_hist[sys_] = {
            "d=1": sum(1 for f in firsts if f==1),
            "d=2": sum(1 for f in firsts if f==2),
            "d=3": sum(1 for f in firsts if f==3),
            "d=all": sum(1 for f in firsts if f=="all"),
            "never": sum(1 for f in firsts if f is None),
        }
    out["first_flip_distribution"] = first_flip_hist
    (OUT_NUM/"04c_dose_response.json").write_text(json.dumps(out, indent=2, default=str))

    md = "# §4.4.3 — Dose-response\n\n"
    md += "| dose | avg facts | AEGIS | LLM-d | TG |\n|---|---|---|---|---|\n"
    for dose in [1,2,3,"all"]:
        v = out.get(str(dose))
        if not v: continue
        md += f"| {dose} | {v['avg_facts_applied']:.1f} | {v['AEGIS']:.1%} | {v['LLM_d']:.1%} | {v['TG']:.1%} |\n"
    md += "\n## First-flip distribution\n\n"
    md += "| system | d=1 | d=2 | d=3 | d=all | never |\n|---|---|---|---|---|---|\n"
    for sys_ in ("smt","llm_d","tg"):
        h = first_flip_hist[sys_]
        md += f"| {sys_} | {h['d=1']} | {h['d=2']} | {h['d=3']} | {h['d=all']} | {h['never']} |\n"
    (OUT_TAB/"04c_dose_response.md").write_text(md)
    print(md)
    return out


if __name__ == "__main__":
    main()
