"""Regenerate 3×3 cross-system CF matrix + union flip rate."""
import json, pathlib
HERE = pathlib.Path(__file__).resolve().parent
SF = HERE.parent / "05_cf_self_faithfulness" / "data"  # for llmd, tg self-flip rows

aegis_cf = {r["pair"]: r["flipped"] for r in json.load(open(HERE/"data/aegis_v3/all_results.json")) if "flipped" in r}
d_other = json.load(open(HERE/"data/llmd_tg_on_aegis_cf/all_results.json"))
aegis_llmd = {r["pair"]: r["cf_flips"]["llm_d"] for r in d_other}
aegis_tg   = {r["pair"]: r["cf_flips"]["tg"] for r in d_other}
d_llmd = json.load(open(SF/"llmd/all_results.json"))
llmd_llmd = {r["pair"]: r["cf_flips"]["llm_d"] for r in d_llmd}
llmd_tg   = {r["pair"]: r["cf_flips"]["tg"] for r in d_llmd}
llmd_aegis = {r["pair"]: r["smt_flipped"] for r in json.load(open(HERE/"data/satir_on_llmd_cf_60.json")) if "smt_flipped" in r}
d_tg = json.load(open(SF/"tg/all_results.json"))
tg_llmd = {r["pair"]: r["cf_flips"]["llm_d"] for r in d_tg}
tg_tg   = {r["pair"]: r["cf_flips"]["tg"] for r in d_tg}
tg_aegis = {r["pair"]: r["smt_flipped"] for r in json.load(open(HERE/"data/satir_on_tg_cf_60.json")) if "smt_flipped" in r}

cells = {
    "AEGIS_CF": {"AEGIS": aegis_cf, "LLM_d": aegis_llmd, "TG": aegis_tg},
    "LLM_d_CF": {"AEGIS": llmd_aegis, "LLM_d": llmd_llmd, "TG": llmd_tg},
    "TG_CF":    {"AEGIS": tg_aegis,   "LLM_d": tg_llmd,   "TG": tg_tg},
}
matrix = {s: {sys_: {"k": sum(1 for v in fl.values() if v), "n": len(fl),
                      "rate": sum(1 for v in fl.values() if v)/len(fl)}
              for sys_, fl in by.items()}
          for s, by in cells.items()}
union = {sys_: {"k": sum(matrix[s][sys_]["k"] for s in cells),
                "n": sum(matrix[s][sys_]["n"] for s in cells)}
         for sys_ in ("AEGIS","LLM_d","TG")}
for sys_ in union: union[sys_]["rate"] = union[sys_]["k"]/union[sys_]["n"]

out = {"matrix": matrix, "union": union}
out_dir = HERE/"out"; out_dir.mkdir(exist_ok=True)
(out_dir/"numbers.json").write_text(json.dumps(out, indent=2))
md = "# §4.4.2 — 3×3 CF matrix\n\n| CF source ↓ / Judged → | AEGIS | LLM-d | TG |\n|---|---|---|---|\n"
for s in ("AEGIS_CF","LLM_d_CF","TG_CF"):
    row = f"| {s} |"
    for sys_ in ("AEGIS","LLM_d","TG"):
        c = matrix[s][sys_]
        row += f" {c['k']}/{c['n']} = {c['rate']:.1%} |"
    md += row + "\n"
md += "| **Union (n=180)** |"
for sys_ in ("AEGIS","LLM_d","TG"):
    u = union[sys_]; md += f" **{u['k']}/{u['n']} = {u['rate']:.1%}** |"
md += "\n"
(out_dir/"table.md").write_text(md); print(md)
