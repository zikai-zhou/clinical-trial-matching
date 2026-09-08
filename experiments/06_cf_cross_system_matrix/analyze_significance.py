"""§4.4.2 — Statistical significance on the 3×3 CF matrix.

Computes McNemar paired tests + Wilson 95% CIs on each cell.
"""
from __future__ import annotations
import json, pathlib
from statsmodels.stats.contingency_tables import mcnemar
from statsmodels.stats.proportion import proportion_confint

HERE = pathlib.Path(__file__).resolve().parent
R = HERE
OUT_NUM = HERE / "out"
OUT_TAB = HERE / "out"
OUT_NUM.mkdir(parents=True, exist_ok=True)
OUT_TAB.mkdir(parents=True, exist_ok=True)


def _mcnemar(flips_a: dict, flips_b: dict):
    pairs = sorted(set(flips_a) & set(flips_b))
    tbl = [[0, 0], [0, 0]]
    for p in pairs:
        a, b = flips_a[p], flips_b[p]
        tbl[0 if a else 1][0 if b else 1] += 1
    try:
        p = mcnemar(tbl, exact=True).pvalue
    except Exception:
        p = None
    return {"n": len(pairs),
            "rate_a": sum(flips_a[p] for p in pairs) / max(len(pairs), 1),
            "rate_b": sum(flips_b[p] for p in pairs) / max(len(pairs), 1),
            "p": p}


def main() -> dict:
    satir_cf_satir = {r["pair"]: r["flipped"]
                      for r in json.load(open(R/"data/aegis_v3"/"all_results.json"))
                      if "flipped" in r}
    d_o = json.load(open(R/"data/llmd_tg_on_aegis_cf"/"all_results.json"))
    satir_cf_llmd = {r["pair"]: r["cf_flips"]["llm_d"] for r in d_o}
    satir_cf_tg = {r["pair"]: r["cf_flips"]["tg"] for r in d_o}
    llmd_cf_llmd = {r["pair"]: r["cf_flips"]["llm_d"]
                    for r in json.load(open(R/"../05_cf_self_faithfulness/data/llmd"/"all_results.json"))}
    tg_cf_tg = {r["pair"]: r["cf_flips"]["tg"]
                for r in json.load(open(R/"../05_cf_self_faithfulness/data/tg"/"all_results.json"))}

    tests = {
        "satir_self_vs_llmd_self":   _mcnemar(satir_cf_satir, llmd_cf_llmd),
        "satir_self_vs_tg_self":     _mcnemar(satir_cf_satir, tg_cf_tg),
        "llmd_self_vs_tg_self":      _mcnemar(llmd_cf_llmd, tg_cf_tg),
        "satir_cf_satir_vs_llmd":    _mcnemar(satir_cf_satir, satir_cf_llmd),
        "satir_cf_satir_vs_tg":      _mcnemar(satir_cf_satir, satir_cf_tg),
        "satir_cf_llmd_vs_tg":       _mcnemar(satir_cf_llmd, satir_cf_tg),
    }

    # Wilson CIs
    cells = {
        "AEGIS_CF_to_AEGIS":  satir_cf_satir,
        "AEGIS_CF_to_LLMd":   satir_cf_llmd,
        "AEGIS_CF_to_TG":     satir_cf_tg,
        "LLMd_CF_to_LLMd":    llmd_cf_llmd,
        "TG_CF_to_TG":        tg_cf_tg,
    }
    cis = {}
    for name, flips in cells.items():
        k = sum(flips.values()); n = len(flips)
        lo, hi = proportion_confint(k, n, method="wilson")
        cis[name] = {"k": k, "n": n, "rate": k/n, "ci_lo": lo, "ci_hi": hi}

    out = {"mcnemar_tests": tests, "wilson_cis": cis}
    (OUT_NUM/"04b_cf_significance.json").write_text(json.dumps(out, indent=2, default=str))

    md = "# §4.4.2 — Statistical significance\n\n"
    md += "## McNemar paired tests\n\n"
    md += "| comparison | rate_a | rate_b | n | p | sig |\n|---|---|---|---|---|---|\n"
    for name, t in tests.items():
        p = t["p"]
        sig = "***" if p and p<1e-3 else ("**" if p and p<1e-2 else ("*" if p and p<0.05 else "ns"))
        md += f"| {name} | {t['rate_a']:.1%} | {t['rate_b']:.1%} | {t['n']} | {p:.3g} | {sig} |\n"
    md += "\n## Wilson 95% CIs\n\n| cell | rate | 95% CI |\n|---|---|---|\n"
    for name, c in cis.items():
        md += f"| {name} | {c['k']}/{c['n']} = {c['rate']:.3f} | [{c['ci_lo']:.3f}, {c['ci_hi']:.3f}] |\n"
    (OUT_TAB/"04b_cf_significance.md").write_text(md)
    print(md)
    return out


if __name__ == "__main__":
    main()
