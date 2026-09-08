# Experiment 11 — Case Studies (§6)

## Purpose
Qualitative comparison of reasoning patterns between AEGIS and GPT-4.1 Direct (LLM-d) on three illustrative pairs.

## Cases

### Case 1 — Silence-as-failure (prescreen policy)
**Pair**: `sigir-20145__NCT00002275` (AIDS/PCP trial)
- AEGIS: defers (miner null → lenient prescreen)
- LLM-d: ineligible (infers "absent in chart → absent in patient")

### Case 2 — Indirect inference (explicit evidence)
**Pair**: `sigir-20144__NCT01173666` (IHD required)
- AEGIS: "no IHD documented" ≠ "IHD ruled out" → defer
- LLM-d: asserts positive absence from lack of mention

### Case 3 — Miner bottleneck (AEGIS fails, LLM-d wins)
**Pair**: `sigir-20145__NCT00000402` (young pubertal girls trial)
- AEGIS: retains 56-y-o woman (miner didn't bridge age→pubertal stage)
- LLM-d: correctly catches age mismatch
- **Honest case** — shows AEGIS's miner bottleneck

## Data
Each case pulls from `evaluation/results/verbalize_judge_235_v3/shard_*/<pair>/`:
- `smt_decision.json` — AEGIS rationale
- `llm_direct_decision.json` — LLM-d rationale
- `trialgpt_decision.json` — TG rationale

## Method
Inspect each pair's rationale files side-by-side. Qualitative analysis only.

## Takeaway
AEGIS's rationale is a *constraint check*; LLM-d's is a *narrative plausibility judgment*. These encode different semantics (prescreen-lenient vs enrollment-strict). Structured rationale makes the semantic choice explicit.
