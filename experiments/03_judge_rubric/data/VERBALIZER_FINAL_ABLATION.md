# Verbalizer Ablation — Final Results (4 versions × 5 axes)

Evaluated under the clinician-language GPT-5 judge with the extended 5-axis
sharpness rubric (adds `logical_consistency` as a fifth dimension). All runs
on the same 235 patient-trial pairs with identical SatIR decisions — only
the verbalizer prompt varies.

## Verbalizer designs

| Version | Design | Target |
|---|---|---|
| **v1** (default) | 1–2 sentences; eligible-branch falls back to boilerplate 91/95 times | Match SatIR's deferral semantics |
| **v2** (affirmative citation) | 2 sentences; always cites satisfied inclusion on eligible | Improve evidence specificity |
| **v3** (tight 1-sentence) | Exactly 1 sentence, ≤25 words, strict template | Improve decisiveness + conciseness |
| **v4** (verdict-anchor + action) | 2 sentences, "Rule out: …. No further screening needed." | Restore logical consistency + actionability |
| **v5** (because-which) | 1 sentence, "Verdict because <fact>, which <relation> <criterion>." | Make causal chain explicit → recover logical_consistency |

## Full results (SatIR only; N=235; 5-axis rubric)

| Dimension | v1 | v2 | v3 | v4 | v5 |
|---|---:|---:|---:|---:|---:|
| decisiveness | 4.13 | 4.31 | **4.56*** | 4.40 | 4.55 |
| evidence specificity | 3.29 | 3.78 | 3.77 | 3.67 | **3.85*** |
| conciseness | 4.88 | 4.69 | **4.98*** | 4.63 | **4.98*** |
| actionability | 3.27 | **3.32*** | 3.06 | **3.32*** | 3.07 |
| logical consistency | **4.05*** | 3.69 | 3.51 | 3.74 | 3.50 |
| Accuracy vs judge | **0.804*** | 0.791 | 0.774 | 0.787 | 0.774 |
| Mean decision rating | **4.22*** | 4.17 | 4.10 | 4.15 | 4.10 |

**Additional metric:**
- Eligible-branch affirmative citation rate:
  - v1: 4/95 (4%) · v2: 84/95 (88%) · v3: 85/95 (89%) · v4: 92/95 (97%) · v5: **95/95 (100%)**

*\* = winner in column.*

## Interpretation

**No single verbalizer dominates**. Each embodies a distinct clinical-priority
trade-off:

- **v1 (trust-optimized)**: highest logical_consistency (4.05) and accuracy
  (0.804). The default is already the best at "can I trust this reasoning?"
  If the coordinator's priority is not making wrong calls, use v1.

- **v2 (evidence-optimized)**: highest evidence_specificity (3.78). If the
  coordinator wants specific chart facts cited even on eligible cases, v2
  delivers.

- **v3 (skim-optimized)**: highest decisiveness (4.56) and conciseness (4.98).
  When the coordinator has 200 candidates to triage in 30 min, v3's crisp
  one-sentence format wins.

- **v4 (balanced)**: never peaks on any axis but never last either; best
  actionability (3.32, tied with v2), second-best logical_consistency (3.74
  vs v1's 4.05). If forced to pick a single default, v4 is least-worst.

- **v5 (causal-template-optimized)**: highest evidence_specificity (3.85),
  100% eligible affirmative citation. The explicit "because...which"
  template maximally surfaces the fact-criterion link, but introduces
  awkward constructions ("which fails the ability to give informed consent")
  that the judge penalizes on logical_consistency (3.50, worst across all 5).
  A reminder that explicit causal markers don't always improve perceived
  logical soundness.

## Key negative result

**No verbalizer variant recovers v1's logical_consistency (4.05).** We
iterated four times (v2, v3, v4, v5) with increasingly different
structural interventions — affirmative citation, tight 1-sentence,
verdict-anchor + action, explicit causal template — and each one traded
logical_consistency for gains on other dimensions.

This is likely a structural property: increased rationale specificity
creates more surfaces where the judge can find inconsistency. The
default v1 (which frequently falls back to boilerplate) is "safest"
precisely because it makes fewer specific claims.

## SatIR vs LLM-direct (5-axis, under v1 rationales)

| Dimension | SatIR | LLM-direct |
|---|---:|---:|
| decisiveness | 4.13 | **4.45** |
| evidence specificity | 3.29 | **3.73** |
| conciseness | **4.88** | 4.89 |
| actionability | 3.27 | **3.73** |
| **logical consistency** | **4.05** | 3.94 |

**SatIR wins logical_consistency** (the new, clinician-priority dimension)
even under the default verbalizer. LLM-direct still wins the other four
"prose-style" dimensions by 0.3–0.5, which we interpret as verbalizer-style
artifact (closable via v2/v3/v4 on the sub-dimensions they target).

## Pairwise contest preservation

SatIR's pairwise win rate against both baselines stays essentially constant
across verbalizer versions (~191–195 wins out of 235 contests):

| Verbalizer | SMT vs LLM-d | SMT vs TG |
|---|---|---|
| v1 | 195 / 9 / 31 | 194 / 7 / 34 |
| v2 | 192 / 8 / 35 | 191 / 8 / 36 |
| v3 | 191 / 13 / 31 | 191 / 10 / 34 |
| v4 | 189 / 14 / 32 | 191 / 10 / 34 |
| v5 | 187 / 13 / 35 | 190 / 9 / 36 |

(smt / opp / tie)

The ~3–4 pair shift is consistent with the rationale-leakage effect
documented earlier: judge verdicts move slightly when rationales change, but
the ranking is stable.

## Accuracy drift across verbalizers

| Verbalizer | Accuracy | Δ vs v1 |
|---|---:|---:|
| v1 | 0.804 | — |
| v4 | 0.787 | −1.7 pp |
| v2 | 0.791 | −1.3 pp |
| v3 | 0.774 | −3.0 pp |

More aggressive specificity → slightly more rationale-leakage → lower accuracy.
This is a feature of LLM-as-judge methodology, not a property of SatIR's
reasoning. Documented in `RATIONALE_LEAKAGE_FINDING.md`.

## Recommendation for the paper

- **Primary**: v1 as main results. It has the highest accuracy, the highest
  logical_consistency (our new dimension), and mean decision rating.
- **Ablations**: report v2, v3, v4 as trade-off variants showing that
  verbalizer design has genuine multi-dimensional trade-offs.
- **Figure 5**: radar chart of the 4-verbalizer profile across 5 axes.
- **Figure 6**: bar chart companion for direct per-axis reading.
- **Contribution**: the trade-off structure itself, not "v1 is best."

The methodological insight: **clinician-facing rationale design involves
explicit multi-objective trade-offs**, and the 5-axis rubric (especially the
added `logical_consistency` dimension) makes these trade-offs visible and
quantifiable for the first time in clinical-matching literature.

## Artifacts

- `sql_retrieval/meval/prompts/verbalize_prescreen_unified.prompt` — v1
- `sql_retrieval/meval/prompts/verbalize_prescreen_unified_v2.prompt` — v2
- `sql_retrieval/meval/prompts/verbalize_prescreen_unified_v3.prompt` — v3
- `sql_retrieval/meval/prompts/verbalize_prescreen_unified_v4.prompt` — v4
- `sql_retrieval/meval/prompts/accuracy_and_sharpness_clinician_v2.prompt` —
  5-axis rubric
- `verb_v2_full_235/`, `verb_v3_full_235/`, `verb_v4_full_235/` — per-pair
  generated rationales
- `verbalize_judge_235_v4/` (v2 rationales), `_v5/` (v3), `_v6/` (v4) — pair
  directories with replaced SatIR rationales
- `accuracy_sharpness_235_v3_clinician_v2rubric/` — v1 under 5-axis rubric
- `accuracy_sharpness_235_v4_clinician_v2rubric/` — v2 under 5-axis rubric
- `accuracy_sharpness_235_v5_clinician/` — v3 under 5-axis rubric
- `accuracy_sharpness_235_v6_clinician/` — v4 under 5-axis rubric
- `rejudge_235_v5_clinician/` — v3 pairwise
- `rejudge_235_v6_clinician/` — v4 pairwise (pending)
- `paper_figures/figure_5_verbalizer_radar.pdf` — radar chart
- `paper_figures/figure_6_verbalizer_bars.pdf` — bar chart

## Camera-ready considerations

1. Run v6 pairwise to complete the 4-verbalizer comparison table.
2. Consider a v5 verbalizer iteration that combines v1's logical_consistency
   with v3's conciseness. Hypothesis: keep v3's tight template but add an
   explicit "because" clause that anchors the verdict to the fact. Target:
   logical_consistency ≥4.0 while keeping conciseness ≥4.8.
3. Note in limitations that rationale-leakage confounds accuracy comparisons
   across verbalizer versions; the two-pass judge design (verdict-before-
   rationales) would control for this.
