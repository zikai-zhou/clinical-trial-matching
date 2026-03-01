"""Regenerate the 72/75/55 self-faithfulness table + McNemar tests.
Reads from data/aegis_v2/, data/tg/, data/llmd/."""
import json, pathlib
try:
    from statsmodels.stats.contingency_tables import mcnemar
    from statsmodels.stats.proportion import proportion_confint
except ImportError:
    mcnemar = None; proportion_confint = None

HERE = pathlib.Path(__file__).resolve().parent
aegis = json.load(open(HERE/"data"/"aegis_v2"/"all_results.json"))
tg    = json.load(open(HERE/"data"/"tg"/"all_results.json"))
llmd  = json.load(open(HERE/"data"/"llmd"/"all_results.json"))

aegis_f = {r['pair']: r['flipped'] for r in aegis if 'flipped' in r}
tg_f    = {r['pair']: r['cf_flips']['tg'] for r in tg}
llmd_f  = {r['pair']: r['cf_flips']['llm_d'] for r in llmd}

def rate(fl):
    k = sum(fl.values()); n = len(fl)
    return k, n, k/n if n else 0.0

def mcn(a, b):
    if not mcnemar: return None
    pairs = sorted(set(a) & set(b))
    tbl = [[0,0],[0,0]]
    for p in pairs:
        x, y = a[p], b[p]
        tbl[0 if x else 1][0 if y else 1] += 1
    try: return mcnemar(tbl, exact=True).pvalue
    except: return None

rows = [("AEGIS", aegis_f), ("TG", tg_f), ("LLM-d", llmd_f)]
md = "# §4.4.2 — Self-faithfulness (same 60 pairs, each system flips its own cited reasons)\n\n"
md += "| System | Self-flip | 95% CI (Wilson) |\n|---|---|---|\n"
for name, fl in rows:
    k, n, r = rate(fl)
    ci = proportion_confint(k, n, method="wilson") if proportion_confint else (None, None)
    md += f"| {name} | {k}/{n} = {r:.1%} | [{ci[0]:.3f}, {ci[1]:.3f}] |\n" if ci[0] else f"| {name} | {k}/{n} = {r:.1%} | — |\n"

md += "\n## McNemar paired tests\n\n"
for (a_name, a_fl), (b_name, b_fl) in [(rows[0], rows[1]), (rows[0], rows[2]), (rows[1], rows[2])]:
    p = mcn(a_fl, b_fl)
    md += f"- {a_name} vs {b_name}: p={p:.4g}\n" if p else f"- {a_name} vs {b_name}: n/a\n"

(HERE/"out").mkdir(exist_ok=True)
(HERE/"out"/"table.md").write_text(md)
print(md)
