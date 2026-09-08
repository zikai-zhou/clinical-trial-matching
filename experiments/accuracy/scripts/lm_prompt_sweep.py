#!/usr/bin/env python3
"""LM-only prompt sweep: trace the Pareto curve achievable by varying the LM-only prompt design.
Each prompt is a different operating point in (P, R) space.

Variants:
  V0_VANILLA      — minimal one-line prompt (already run; reference)
  V1_BASIC        — original LM-only prompt (paper baseline; not re-run, reference)
  V2_PRESCREEN    — prescreen-doctrine prompt (paper PROMPT_V2; reference)
  V3_STRICT_PRESCREEN  — even more permissive: reject ONLY on quoted contradiction
  V4_COT_CRITERION     — chain-of-thought, list criteria explicitly, mark each pass/fail/unknown, then aggregate
  V5_TWO_STEP          — Step 1 list relevant chart facts. Step 2 judge.
  V6_INFERENCE_EXPLICIT — explicit clinical inference doctrine: make defensible inferences from chart cues
  V7_CONSERVATIVE_PRESCREEN — middle ground; default forward on uncertainty but reject on weak explicit contradiction
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments/99_counterfactual_lm"))
from run_better_nl_full import get_pair_inputs, collect_pairs

PROMPTS = {
    "V11_TWO_STEP_TIGHTENED": """You are an expert clinician at PRESCREEN. Reason in two stages.

STAGE 1 — Extract relevant chart facts.
List the patient facts most relevant to this trial's criteria. Use only what is documented. Be COMPREHENSIVE — extract facts that bear on inclusion AND on exclusion. Do not skip facts that suggest a contradiction.

STAGE 2 — Judge eligibility.
Apply the trial's criteria to the extracted facts. Default to FORWARD on chart silence (prescreen-doctrine).

REJECTION TRIGGERS (mandatory checks before deciding eligible):
A. Is there an INCLUSION criterion that the chart EXPLICITLY contradicts (a documented value/diagnosis/state that fails the criterion)? If yes → REJECT.
B. Is there an EXCLUSION criterion that the chart EXPLICITLY satisfies (a documented exclusion-causing fact)? If yes → REJECT.
C. Does defensible clinical reasoning from documented chart facts indicate the patient FAILS a criterion (e.g., normal echo + no exertional dyspnea → unlikely severe heart failure → REJECT for "must have severe HF")?

You may FORWARD only after confirming none of A/B/C trigger.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

After your two-stage reasoning, output the final JSON:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V12_BIDIRECTIONAL_CHECK": """You are an expert clinician at PRESCREEN. Make a balanced decision: forward when reasonable, reject when contradicted.

Two-pass reasoning:

PASS 1 — Forward bias check: List reasons to FORWARD. Default if chart is silent on a positive criterion.

PASS 2 — Reject bias check: List reasons to REJECT. Look for:
  - Explicit chart text contradicting an inclusion criterion (e.g., "20 yo female" when trial requires age ≥ 65)
  - Explicit chart text satisfying an exclusion (e.g., "currently pregnant" excludes pregnancy-restricted trials)
  - Defensible clinical inference that the patient does not meet eligibility (e.g., normal labs + no symptoms → no severe disease for a trial requiring severe disease)

Final decision: REJECT if any explicit-or-defensibly-inferred contradiction exists. FORWARD otherwise.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

After both passes, output the final JSON:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",


    "V3_STRICT_PRESCREEN": """You are an expert clinician at PRESCREEN. The question is whether to forward this patient for in-person screening, NOT whether they are guaranteed eligible.

DECISION RULE (mandatory):
You MUST default to FORWARD. The ONLY case for REJECT is when the chart contains EXPLICIT, QUOTED text that directly contradicts a trial criterion:
- a specific lab value outside the trial's threshold (the value must be in the chart)
- a documented diagnosis explicitly excluded by the trial
- a documented medication explicitly excluded
- an explicit demographic mismatch (age, sex, etc.) that the chart states

CHART SILENCE = FORWARD. ALWAYS. Even when the trial requires positive documentation of X, silence is forward.
INFERRED CONTRADICTION = FORWARD. Only quoted contradictions count.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V4_COT_CRITERION": """You are an expert clinician evaluating a patient at PRESCREEN. Use chain-of-thought reasoning.

Step 1: List each inclusion criterion. For each, classify: PASS (chart supports), FAIL (chart contradicts), UNKNOWN (chart silent or unclear).
Step 2: List each exclusion criterion. For each, classify: TRIGGERED (chart contains the exclusion), NOT_TRIGGERED (chart contradicts or is silent), UNKNOWN (chart is unclear).
Step 3: Final decision: forward (eligible) iff (no inclusion is FAIL) AND (no exclusion is TRIGGERED). UNKNOWNs default to forward.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Show your step-by-step reasoning, then output the final JSON:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence summary>"}}""",

    "V5_TWO_STEP": """You are an expert clinician at PRESCREEN. Reason in two stages.

STAGE 1 — Extract relevant chart facts.
List the patient facts most relevant to this trial's criteria. Use only what is documented.

STAGE 2 — Judge eligibility.
Apply the trial's criteria to the extracted facts. Default to FORWARD on chart silence (prescreen-doctrine).

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

After your two-stage reasoning, output the final JSON:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V6_INFERENCE_EXPLICIT": """You are an expert clinician evaluating a patient at PRESCREEN. Use the highest standard of clinical inference.

Apply DEFENSIBLE clinical inference: if a fact is not explicitly in the chart but follows reliably from documented evidence (e.g., "presents with substernal pain on exertion radiating to back" → likely ACS), you may treat the inference as supporting eligibility.

You may REJECT only when:
- the chart explicitly contradicts a criterion, OR
- defensible clinical inference indicates the patient does not meet the criterion (e.g., "no exertional dyspnea, normal echo" → unlikely to have severe heart failure)

For UNCERTAIN cases (chart is genuinely silent on a criterion that is not commonly documented at prescreen), default to FORWARD.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V8_CHAIN_OF_EVIDENCE": """You are an expert clinician at PRESCREEN. Make every decision evidence-based.

For each criterion, identify the CHART TEXT (verbatim quote) that bears on it. Without explicit chart text, the criterion defaults to FORWARD (chart-silence rule).

Reject ONLY when you can quote chart text that:
- explicitly contradicts an inclusion criterion, OR
- explicitly satisfies an exclusion criterion

If you cannot produce a verbatim quote, do not reject.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence with quoted chart text if rejecting>"}}""",

    "V9_SELF_CRITIQUE": """You are an expert clinician at PRESCREEN. Use a two-pass process.

PASS 1: Make an initial eligibility judgment. Forward unless you can quote chart text that contradicts.

PASS 2: Critique your judgment. Specifically check: (a) am I rejecting based on chart silence rather than explicit chart text? (b) am I confusing "no documentation" with "explicit contradiction"? (c) am I being too strict for prescreen?

Final decision after the critique pass.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V10_RISK_STRATIFIED": """You are an expert clinician at PRESCREEN. Use risk-stratified reasoning.

For SAFETY-CRITICAL exclusions (active malignancy, pregnancy, severe organ dysfunction, contraindicated medications): apply a strict default — if there's ANY suggestive evidence in the chart, REJECT.

For NON-SAFETY-CRITICAL inclusions (specific labs, staging, prior therapies): apply prescreen-doctrine — silence forwards.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",

    "V7_CONSERVATIVE_PRESCREEN": """You are an expert clinician at PRESCREEN. Decide whether to forward this patient for in-person screening.

Default to FORWARD. You may REJECT only on EXPLICIT chart text that contradicts a criterion. Specifically:
- A documented lab value outside the trial's threshold
- A documented diagnosis explicitly excluded
- A demographic mismatch documented in the chart
- A medication or treatment history explicitly contradicted

WEAK EVIDENCE: if the chart contains only suggestive (not stated) evidence of a contradiction (e.g., "history of cancer" when the trial requires no active malignancy), DO NOT reject — this is for the in-person visit to verify.

CHART SILENCE: forward.

CLINICAL TRIAL:
{trial}

PATIENT VIGNETTE:
{note}

Output JSON ONLY:
{{"eligibility": "eligible"|"ineligible", "explanation": "<1 sentence>"}}""",
}

def call(engine, prompt, max_tokens=2000):
    out = engine([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=max_tokens)
    text = out[0] if isinstance(out, list) and out else out
    text = text if isinstance(text, str) else str(text)
    jm = re.search(r"\{[\s\S]*\}", text)
    if not jm: return {"eligibility": "?", "explanation": "no_json"}
    try:
        res = json.loads(jm.group(0))
        return {"eligibility": res.get("eligibility"), "explanation": (res.get("explanation") or "")[:300]}
    except Exception:
        return {"eligibility": "?", "explanation": "parse_err"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=list(PROMPTS.keys()))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    prompt_template = PROMPTS[args.variant]
    OUT = ROOT / "overnight" / f"lm_only_{args.variant}.jsonl"
    cache = {}
    if OUT.exists():
        for line in OUT.open():
            try: o = json.loads(line); cache[o["pair"]] = o
            except: pass
    print(f"[{args.variant}] cached: {len(cache)}", flush=True)

    from smt_core.inference_engine import AzureInferenceEngine
    endpoint = os.environ["OPENAI_ENDPOINT"]
    engine_obj = AzureInferenceEngine(endpoint=endpoint, model_name="gpt-4.1")
    engine = engine_obj.run

    pairs, _ = collect_pairs()
    if args.limit: pairs = pairs[:args.limit]
    todo = [p for p in pairs if p not in cache]
    print(f"[{args.variant}] {len(todo)} pairs to run", flush=True)

    # Load full SIGIR corpus to bypass the 1500-char cap in get_pair_inputs.
    sigir_corpus = {}
    sigir_path = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT/dataset/clinical_trial/sigir/corpus.jsonl')
    if sigir_path.exists():
        for ln in sigir_path.open():
            r = json.loads(ln); sigir_corpus[r['_id']] = r
    _inc_re = re.compile(r'(?i)\binclusion\s+criteria\s*:\s*')
    _exc_re = re.compile(r'(?i)\bexclusion\s+criteria\s*:\s*')
    def _split_inc_exc(text):
        if not text: return '', ''
        im = _inc_re.search(text); em = _exc_re.search(text)
        i = e = ''
        if im: i = text[im.end(): em.start() if em else len(text)].strip()
        if em: e = text[em.end():].strip()
        return i, e
    def _full_trial_text(pair):
        _, nct = pair.split('__', 1)
        if nct in sigir_corpus:
            return _split_inc_exc(sigir_corpus[nct].get('text',''))
        parent = re.sub(r'(?<=NCT\d{8})[a-z]+$', '', nct)
        if parent in sigir_corpus:
            return _split_inc_exc(sigir_corpus[parent].get('text',''))
        return None, None

    def proc(pair):
        note, inc_v6, exc_v6 = get_pair_inputs(pair)
        if note is None: return pair, {"pair": pair, "eligibility": "?", "error": "no_input"}
        inc_full, exc_full = _full_trial_text(pair)
        inc = inc_full if inc_full else inc_v6
        exc = exc_full if exc_full is not None else exc_v6
        trial = f"INCLUSION CRITERIA:\n{inc}\n\nEXCLUSION CRITERIA:\n{exc}"
        # NO TRUNCATION on trial text or patient note
        prompt = prompt_template.format(trial=trial, note=note)
        for attempt in range(3):
            try:
                r = call(engine, prompt)
                return pair, {"pair": pair, **r, "variant": args.variant}
            except Exception as e:
                last = e; time.sleep(1 + attempt)
        return pair, {"pair": pair, "eligibility": "?", "error": str(last)[:120]}

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(proc, p) for p in todo]
        with OUT.open("a") as fout:
            for f in as_completed(futs):
                pair, rec = f.result()
                cache[pair] = rec
                fout.write(json.dumps(rec) + "\n"); fout.flush()
                done += 1
                if done % 30 == 0 or done == len(todo):
                    print(f"  [{done}/{len(todo)}]", flush=True)

    n_e = sum(1 for v in cache.values() if v.get("eligibility")=="eligible")
    n_i = sum(1 for v in cache.values() if v.get("eligibility")=="ineligible")
    n_q = sum(1 for v in cache.values() if v.get("eligibility") not in ("eligible","ineligible"))
    print(f"[{args.variant}] FINAL: eligible={n_e} ineligible={n_i} unknown={n_q}", flush=True)

if __name__ == "__main__":
    main()
