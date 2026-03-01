"""§4.4.4 — Noise robustness (aggregate + pair-level)."""
from __future__ import annotations
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
R = HERE
OUT_NUM = HERE / "out"
OUT_TAB = HERE / "out"
OUT_NUM.mkdir(parents=True, exist_ok=True)
OUT_TAB.mkdir(parents=True, exist_ok=True)


def main() -> dict:
    noise = {r["pair"]: r["cf_flips"]
             for r in json.load(open(R/"data/cf_noise_injection_60"/"all_results.json"))
             if r.get("cf_flips")}
    base = {r["pair"]: r["cf_flips"]
            for r in json.load(open(R/"../06_cf_cross_system_matrix/data/llmd_tg_on_aegis_cf"/"all_results.json"))
            if r.get("cf_flips")}

    # aggregate
    agg = {}
    for col in ("smt", "llm_d", "tg"):
        agg[col] = {"baseline": sum(1 for f in base.values() if f.get("satir" if col == "smt" else col)),
                    "noise":    sum(1 for f in noise.values() if f.get(col)),
                    "n": len(noise)}

    # pair stability
    common = sorted(set(noise) & set(base))
    stability = {}
    for sys_noise, sys_base in [("smt","satir"),("llm_d","llm_d"),("tg","tg")]:
        same = sum(1 for p in common if noise[p].get(sys_noise) == base[p].get(sys_base))
        flip_dir = sum(1 for p in common if not base[p].get(sys_base) and noise[p].get(sys_noise))
        unflip_dir = sum(1 for p in common if base[p].get(sys_base) and not noise[p].get(sys_noise))
        stability[sys_noise] = {
            "n": len(common), "same": same, "same_rate": same/len(common),
            "noise_induced_flip_to_eligible": flip_dir,
            "noise_induced_flip_to_ineligible": unflip_dir,
        }

    out = {"aggregate": agg, "pair_stability": stability}
    (OUT_NUM/"04d_noise_injection.json").write_text(json.dumps(out, indent=2))

    md = "# §4.4.4 — Noise injection\n\n## Aggregate\n\n"
    md += "| system | baseline | +noise | Δ |\n|---|---|---|---|\n"
    for col in ("smt","llm_d","tg"):
        a = agg[col]; d = (a["noise"]-a["baseline"])/a["n"]
        md += f"| {col} | {a['baseline']}/{a['n']} = {a['baseline']/a['n']:.1%} | {a['noise']}/{a['n']} = {a['noise']/a['n']:.1%} | {d:+.1%} |\n"
    md += "\n## Pair-level stability\n\n| system | n | same decision | rate | flip toward eligible | flip toward ineligible |\n|---|---|---|---|---|---|\n"
    for sys_, s in stability.items():
        md += f"| {sys_} | {s['n']} | {s['same']} | {s['same_rate']:.1%} | {s['noise_induced_flip_to_eligible']} | {s['noise_induced_flip_to_ineligible']} |\n"
    (OUT_TAB/"04d_noise_injection.md").write_text(md)
    print(md)
    return out


if __name__ == "__main__":
    main()
