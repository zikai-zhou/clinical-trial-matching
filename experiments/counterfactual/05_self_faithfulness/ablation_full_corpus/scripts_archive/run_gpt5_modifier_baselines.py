#!/usr/bin/env python3
"""Re-run gpt-5 modifier on all baseline systems (v5/v5_blockers/tg/shah/v5_gpt5)
using their NL rationales as the modifier instruction, with gpt-5 simclin
validation.

Apples-to-apples with the SMT gpt-5 modifier run.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, urllib.request, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, "/tmp")
sys.path.insert(0, str(ROOT / "experiments/counterfactual/05_self_faithfulness"))

from cf_modifier_gpt5_rationale import generate_cf_gpt5_rationale
from simulated_clinician import load_charts, load_trials, llm_call


def render_diff(orig: str, cf: str, max_segs: int = 60) -> str:
    toks_a = re.findall(r"\S+|\s+", orig)
    toks_b = re.findall(r"\S+|\s+", cf)
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    lines = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        if tag in ("delete", "replace"):
            removed = "".join(toks_a[i1:i2]).strip()
            if removed: lines.append(f"  - REMOVED: {removed[:300]}")
        if tag in ("insert", "replace"):
            added = "".join(toks_b[j1:j2]).strip()
            if added: lines.append(f"  + ADDED:   {added[:300]}")
        if len(lines) >= max_segs:
            lines.append("  ... (truncated)"); break
    return "\n".join(lines) if lines else "  (no change)"


SIMCLIN_PROMPT = """You are a senior clinical-trial coordinator at the PRESCREEN stage.

A clinical-trial matcher said the patient was INELIGIBLE. We then asked an
automated chart modifier to neutralize the cited blockers by editing the
chart. Your job: judge the EDIT, not the patient.

ORIGINAL CHART
==============
{original_chart}

INCLUSION
=========
{inclusion}

EXCLUSION
=========
{exclusion}

SYSTEM-CITED BLOCKERS (free-text reasons for ineligibility)
============================================================
{cited}

ORIGINAL SYSTEM VERDICT: ineligible

THE EDIT (DIFF — what was changed)
===================================
{diff_summary}

CHART AFTER MODIFICATION
========================
{cf_chart}

QUESTIONS — answer in JSON with exactly these keys:
  coherent (bool)            : is the modified chart internally coherent and clinically plausible?
  flips_target_atom (bool)   : does the edit neutralize ALL cited blockers?
  keeps_other_facts (bool)   : does the rest of the chart still match the original (no NEW blockers)?
  oracle_should_flip (bool)  : if a clinician saw the modified chart, would the trial be eligible now?
  oracle_verdict_on_cf (str) : "eligible" or "ineligible"
  explanation (str)          : 1-2 sentences

Return ONLY the JSON object.
"""


# External rationale fallback for systems whose self_faithfulness.jsonl entry
# does not carry cited_rationale/blocker_lines (Shah, TG, sometimes others).
_EXTERNAL_RATIONALE_FILES = {
    "v5":          ROOT / "matchers/systems/single_shot_llm/rationales.jsonl",
    "v5_blockers": ROOT / "matchers/systems/single_shot_llm/rationales.jsonl",
    "tg":          ROOT / "matchers/systems/trialgpt/rationales.jsonl",
    "shah":        ROOT / "matchers/systems/shahlab/rationales.jsonl",
}
_RATIONALE_INDEX_CACHE: dict = {}


def _load_external_rationale(system: str, pair: str) -> str:
    cache_key = system
    if cache_key not in _RATIONALE_INDEX_CACHE:
        path = _EXTERNAL_RATIONALE_FILES.get(system)
        if not path or not path.exists():
            _RATIONALE_INDEX_CACHE[cache_key] = {}
        else:
            idx = {}
            for ln in path.open():
                try: o = json.loads(ln)
                except: continue
                p = o.get("pair")
                if p:
                    txt = o.get("rationale") or o.get("rationale_text") or o.get("explanation") or ""
                    if isinstance(txt, list): txt = "\n".join(str(x) for x in txt)
                    idx[p] = str(txt)
            _RATIONALE_INDEX_CACHE[cache_key] = idx
    return _RATIONALE_INDEX_CACHE[cache_key].get(pair, "")


def extract_rationale_and_supports(system: str, info: dict, pair: str = "") -> tuple[str, str]:
    """Return (rationale_text, supports_text) per-system. Falls back to the
    external matchers/systems/*/rationales.jsonl when the in-record fields
    are empty."""
    if system == "v5_blockers":
        # structured_blockers uses keys: fact, side, criterion
        b = info.get("structured_blockers") or []
        if b:
            r = "\n".join(
                f"- [{(x.get('side') or 'exclusion')}] {(x.get('criterion') or '').strip()[:200]}: {(x.get('fact') or '').strip()[:300]}"
                for x in b
            )
        else:
            r = "\n".join((info.get("blocker_lines") or [])[:10])
        cr = (info.get("cited_rationale") or "").strip()
        if cr and len(r) < 200:
            r = cr + ("\n\nBlockers:\n" + r if r else "")
        if not r.strip() and pair:
            r = _load_external_rationale(system, pair)
        return r[:2500], ""
    # v5, tg, shah, v5_gpt5 — try in-record first, then external
    r = (info.get("cited_rationale") or "").strip()
    if not r:
        bl = info.get("blocker_lines") or []
        if bl: r = "\n".join(str(x) for x in bl[:10])
    if not r and pair:
        r = _load_external_rationale(system, pair)
    return r[:2500], ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="v5,v5_blockers,tg,shah,v5_gpt5")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl"))
    args = ap.parse_args()

    ep_full = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT", "")
    base = ep_full.split("/openai/")[0] if ep_full else ""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key: sys.exit("env required")

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    charts = load_charts()
    trials = load_trials()

    sf = ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"
    main_by_pair = {json.loads(l)["pair"]: json.loads(l) for l in sf.open() if l.strip()}

    v5gpt5_by_pair = {}
    for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl").open():
        try: o = json.loads(l)
        except: continue
        if o.get("pair"): v5gpt5_by_pair[o["pair"]] = o

    work = []
    for s in systems:
        if s == "v5_gpt5":
            for pair, info in v5gpt5_by_pair.items():
                # require non-empty rationale + flipped (i.e., system said ineligible originally)
                if not (info.get("cited_rationale") or "").strip(): continue
                work.append((pair, s, info))
        else:
            for pair, rec in main_by_pair.items():
                info = (rec.get("systems") or {}).get(s) or {}
                # Need ANY cited content (rationale or blockers) — try external fallback too
                has = (bool((info.get("cited_rationale") or "").strip())
                       or bool(info.get("structured_blockers"))
                       or bool(info.get("blocker_lines")))
                if not has:
                    # Try external rationales for systems that don't carry it inline
                    ext = _load_external_rationale(s, pair)
                    if ext.strip(): has = True
                if not has: continue
                work.append((pair, s, info))
    print(f"total work: {len(work)} across {len(systems)} systems")

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = set()
    if out_path.exists():
        for ln in out_path.open():
            try: o = json.loads(ln)
            except: continue
            cache.add((o.get("pair"), o.get("system")))
    todo = [w for w in work if (w[0], w[1]) not in cache]
    if args.limit: todo = todo[:args.limit]
    print(f"cached: {len(cache)}, todo: {len(todo)}")

    def run_one(item):
        pair, system, info = item
        pid, tid = pair.split("__", 1)
        chart = charts.get(pid, "")
        inc, exc = trials.get(tid, ("", ""))
        rationale, supports = extract_rationale_and_supports(system, info, pair)

        try:
            cf_chart = generate_cf_gpt5_rationale(chart, f"{inc}\n\nEXCLUSION:\n{exc}", rationale, supports)
        except Exception as e:
            return {"pair": pair, "system": system, "modifier_error": str(e)[:200]}

        diff_block = render_diff(chart[:5000], cf_chart[:5000])
        prompt = SIMCLIN_PROMPT.format(
            original_chart=chart[:5000],
            inclusion=inc[:3000], exclusion=exc[:3000],
            cited=rationale[:2000],
            diff_summary=diff_block,
            cf_chart=cf_chart[:5000],
        )
        try:
            j = llm_call("gpt-5", prompt, base, key)
        except Exception as e:
            j = {"error": str(e)[:200]}
        if not j: j = {}

        return {
            "pair": pair, "system": system,
            "cf_chart": cf_chart,
            "modifier": "gpt-5-v2-coherent-rationale",
            "diff_summary": diff_block[:2000],
            "original_system_flipped": bool(info.get("flipped")),
            "original_loose_valid": info.get("cf_valid"),
            "simclin_coherent": j.get("coherent"),
            "simclin_flips_cited": j.get("flips_target_atom"),
            "simclin_keeps_other": j.get("keeps_other_facts"),
            "simclin_oracle_should_flip": j.get("oracle_should_flip"),
            "simclin_explanation": (j.get("explanation") or "")[:500],
        }

    done = 0
    with out_path.open("a") as f, ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed({ex.submit(run_one, w): w for w in todo}):
            rec = fut.result()
            f.write(json.dumps(rec) + "\n"); f.flush()
            done += 1
            if done % 25 == 0 or done == len(todo):
                print(f"  done {done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
