"""Appendix C — Parser version progression (v0 → v2 → v3)."""
from __future__ import annotations
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
R = HERE
OUT_NUM = HERE / "out"
OUT_TAB = HERE / "out"
OUT_NUM.mkdir(parents=True, exist_ok=True)
OUT_TAB.mkdir(parents=True, exist_ok=True)


def main() -> dict:
    out = {}
    for ver, dirname in [("v0", "data/v0_gpt_parser"),
                         ("v2", "data/v2_heuristic"),
                         ("v3", "data/v3_z3_maxsat")]:
        p = R / dirname / "all_results.json"
        if not p.exists(): continue
        d = json.load(open(p))
        valid = [r for r in d if "flipped" in r]
        f = sum(1 for r in valid if r["flipped"])
        avg_t = sum(r.get("n_targets", 0) for r in valid) / max(len(valid), 1)
        out[ver] = {"flipped": f, "n": len(valid), "rate": f/max(len(valid),1), "avg_targets": avg_t}

    (OUT_NUM/"05_parser_progression.json").write_text(json.dumps(out, indent=2))

    md = "# Appendix C — Parser progression\n\n"
    md += "| version | parser | flip | rate | avg targets/pair |\n|---|---|---|---|---|\n"
    parsers = {"v0": "GPT-4.1 as SMT parser",
               "v2": "Programmatic s-expr parser",
               "v3": "Z3-Optimize MaxSAT (ours)"}
    for ver, v in out.items():
        md += f"| {ver} | {parsers.get(ver,'')} | {v['flipped']}/{v['n']} | {v['rate']:.1%} | {v['avg_targets']:.1f} |\n"
    (OUT_TAB/"05_parser_progression.md").write_text(md)
    print(md)
    return out


if __name__ == "__main__":
    main()
