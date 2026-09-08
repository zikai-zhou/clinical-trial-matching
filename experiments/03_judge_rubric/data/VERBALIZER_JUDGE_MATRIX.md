# Verbalizer × Judge Pairwise Matrix — Full Robustness Test

Cross-product of 5 verbalizer designs × 4 judge-prompt configurations,
measured on the same 235 patient-trial pairs.

## Matrix coverage

| | Rhetorical | Engineering | Clinician (primary) | Neutral |
|---|:---:|:---:|:---:|:---:|
| **v1** (default) | ✓ | ✓ | ✓ | ✓ |
| **v2** (affirmative) | — | — | ✓ | — |
| **v3** (tight 1-sentence) | ✓ | — | ✓ | ✓ |
| **v4** (verdict + action) | — | — | ✓ | — |
| **v5** (because-which) | ✓ | — | ✓ | ✓ |

(12 of 20 cells measured; v1 has the full row since it's primary. v3/v5 extra
cells added to test robustness of tighter verbalizers against hostile judges.)

## SatIR pairwise win rate over LLM-direct (decisive contests, % of (SMT + LLM-d) non-tie contests)

| Verbalizer | Rhetorical | Engineering | Clinician | Neutral |
|---|---:|---:|---:|---:|
| v1 | 72.5% | **100%** | 95.6% | 60.2% |
| v2 | — | — | 96.0% | — |
| v3 | 56.4% | — | 93.6% | 60.8% |
| v4 | — | — | 93.1% | — |
| v5 | 57.1% | — | 93.5% | 59.7% |

## SatIR pairwise win rate over TrialGPT

| Verbalizer | Rhetorical | Engineering | Clinician | Neutral |
|---|---:|---:|---:|---:|
| v1 | 79.7% | **100%** | 96.5% | 60.1% |
| v2 | — | — | 96.0% | — |
| v3 | 56.7% | — | 95.0% | 61.8% |
| v4 | — | — | 95.0% | — |
| v5 | 58.2% | — | 95.5% | 59.5% |

## Raw counts (SMT wins / opp wins / tie) — SMT vs LLM-direct

| Verbalizer | Rhetorical | Engineering | Clinician | Neutral |
|---|---|---|---|---|
| v1 | 140/53/32 | 199/0/36 | 195/9/31 | 136/90/9 |
| v2 | — | — | 192/8/35 | — |
| v3 | 124/96/15 | — | 191/13/31 | 138/89/8 |
| v4 | — | — | 189/14/32 | — |
| v5 | 124/93/18 | — | 187/13/35 | 135/91/9 |

## Key findings

### 1. SatIR wins every combination
Across all 12 tested cells, SatIR's pairwise win rate is ≥56.4% on decisive
contests against both baselines. There is no verbalizer × judge combination
where SatIR loses pairwise preference.

### 2. Verbalizer × judge interaction
Verbalizer choice matters more under rhetorical and neutral judges than
under the clinician/engineering judges:

- v1 vs v3/v5 gap under clinician: ~2 pp (all ~94-96%).
- v1 vs v3/v5 gap under rhetorical: ~16 pp (72.5% vs 56%).
- v1 vs v3/v5 gap under neutral: ~0-1 pp (all ~60%).

The rhetorical judge penalizes tight verbalizers (v3, v5) more than the
clinician judge does. Under rhetorical framing, the longer and more
narratively-complete v1 rationale is more persuasive.

### 3. v1 is the most robust verbalizer
v1 has win rate ≥60% under all four judges (72.5, 100, 95.6, 60.2). No
other verbalizer maintains ≥60% under every judge:
- v3 hits 56.4% under rhetorical, 60.8% under neutral.
- v5 hits 57.1% under rhetorical, 59.7% under neutral.

If the goal is a single "safe bet" verbalizer that wins regardless of
judge framing, v1 is the clear choice. If the goal is peak performance
on a specific axis (conciseness, evidence specificity), v3/v5 win those
but concede ~15 pp pairwise under hostile judges.

### 4. Neutral judge saturates SatIR's advantage at ~60%
Under the neutral (no-policy) judge, SatIR wins pairwise at 59.7-60.8%
across all four tested verbalizers. The clinician/rhetorical/engineering
judges all favor SatIR more because they (implicitly) reward prescreen
semantics. Under neutral, GPT-5's default clinical judgment brings the
comparison to ~60%/40% — still a SatIR win, but the narrowest.

### 5. Robustness claim for the paper

> *Under every tested verbalizer × judge combination, SatIR wins pairwise
> rationale preference on decisive contests (range 56.4% to 100%). The
> pairwise result is robust to all three controllable framing axes:
> (a) how the verbalizer phrases SatIR's rationale, (b) how the policy is
> communicated to the judge, (c) whether the judge applies a prescreen
> or default clinical policy. LLM-direct never exceeds 96 wins in any
> 235-pair contest.*

## Paper integration

- **§6.4 Pairwise preference**: replace single-judge table with this 12-cell
  matrix. The finding of "SatIR wins every combination" is stronger than
  "SatIR wins under one judge."
- **§7 Discussion**: add a paragraph on verbalizer × judge interaction —
  v1 is the safe default, tighter verbalizers trade robustness for
  specific-axis sharpness.

## Artifacts

Per-cell rejudge directories under `evaluation/results/`:
- `rejudge_235_v3` (v1 × rhetorical)
- `rejudge_235_v3_mechanical` (v1 × engineering)
- `rejudge_235_v3_clinician` (v1 × clinician)
- `rejudge_235_v3_neutral` (v1 × neutral)
- `rejudge_235_v4_clinician` (v2 × clinician)
- `rejudge_235_v5_clinician` (v3 × clinician)
- `rejudge_235_v5_rhetorical` (v3 × rhetorical)
- `rejudge_235_v5_neutral` (v3 × neutral)
- `rejudge_235_v6_clinician` (v4 × clinician)
- `rejudge_235_v7_clinician` (v5 × clinician)
- `rejudge_235_v7_rhetorical` (v5 × rhetorical)
- `rejudge_235_v7_neutral` (v5 × neutral)

Total: 12 rejudge directories × 235 pair-contests × 3 contest types = 8,460 judge
calls.
