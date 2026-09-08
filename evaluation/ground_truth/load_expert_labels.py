"""
Load expert-labeled (patient, trial) pairs from the SIGIR criterion-level splits.

The SIGIR dataset provides criterion-level eligibility labels from both a
GPT-4 baseline (`inclusion_gpt4_eligibility`) and human experts
(`inclusion_expert_eligibility`), per inclusion/exclusion criterion.

Use the expert labels as Tier 2 ground truth for:
  - Calibrating the LLM-as-judge against human labels
  - Computing per-criterion accuracy of SMT matcher / LLM-direct / TrialGPT

Usage:
    from evaluation.ground_truth import load_expert_labels
    pairs = load_expert_labels(split="val")  # or "test", "train", "all"
    for row in pairs:
        for i, (criterion, exp, gpt4) in enumerate(zip(
            row["inclusion_criteria"],
            row["inclusion_expert_eligibility"],
            row["inclusion_gpt4_eligibility"],
        )):
            # criterion-level fields
            ...
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Dict, Iterable, List, Optional

# Try both criterion_level and criterion_level_new; the _new dir uses
# TrialGPT label taxonomy (included / not included / not applicable / not enough information).
DEFAULT_DATASET_ROOTS = [
    "criterion_level_new/splits",
    "criterion_level/splits",
]


def _find_splits_dir(data_root: pathlib.Path) -> Optional[pathlib.Path]:
    for rel in DEFAULT_DATASET_ROOTS:
        p = data_root / rel
        if p.exists() and any(p.glob("trial_criteria_*.jsonl")):
            return p
    return None


def _iter_jsonl(path: pathlib.Path) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def load_expert_labels(
    data_root: str | pathlib.Path | None = None,
    split: str = "val",
) -> List[Dict[str, Any]]:
    """
    Load expert-labeled pairs from the SIGIR criterion-level splits.

    Args:
        data_root: path to dataset/clinical_trial/. Defaults to the
            TRIAL_DATA env var or the repo's canonical dataset location.
        split: "train", "val", "test", or "all".

    Returns:
        List of dicts with fields:
          trial_id, patient_id,
          inclusion_criteria (List[str]),
          inclusion_expert_eligibility (List[str]),
          inclusion_gpt4_eligibility (List[str]),
          exclusion_criteria, exclusion_expert_eligibility, exclusion_gpt4_eligibility,
          plus any explanation fields present.
    """
    if data_root is None:
        data_root = os.environ.get("TRIAL_DATA", "../dataset/clinical_trial")
    data_root = pathlib.Path(data_root).resolve()

    splits_dir = _find_splits_dir(data_root)
    if splits_dir is None:
        # Also try the original TrialGPT-SMT dataset location
        fallback = pathlib.Path("${SATIR_DATA_ROOT}/dataset/clinical_trial")
        splits_dir = _find_splits_dir(fallback)
        if splits_dir is None:
            raise FileNotFoundError(
                f"Could not find criterion-level splits under {data_root}. "
                f"Expected one of: {DEFAULT_DATASET_ROOTS}"
            )

    if split == "all":
        files = sorted(splits_dir.glob("trial_criteria_*.jsonl"))
    else:
        candidate = splits_dir / f"trial_criteria_{split}.jsonl"
        if not candidate.exists():
            raise FileNotFoundError(f"No split file: {candidate}")
        files = [candidate]

    rows: List[Dict[str, Any]] = []
    for f in files:
        for row in _iter_jsonl(f):
            if "trial_id" in row and "patient_id" in row:
                row["_source_split"] = f.stem.replace("trial_criteria_", "")
                row["_source_path"] = str(f)
                rows.append(row)
    return rows


def flatten_to_criteria(
    pairs: List[Dict[str, Any]],
    sides: Iterable[str] = ("inclusion", "exclusion"),
) -> List[Dict[str, Any]]:
    """
    Flatten per-pair dicts to per-criterion rows. Each output row has:
        trial_id, patient_id, side, criterion_idx, criterion_text,
        expert_label, gpt4_label, expert_explanation, gpt4_explanation
    """
    out: List[Dict[str, Any]] = []
    for pair in pairs:
        for side in sides:
            criteria = pair.get(f"{side}_criteria") or []
            exp_labels = pair.get(f"{side}_expert_eligibility") or []
            gpt4_labels = pair.get(f"{side}_gpt4_eligibility") or []
            exp_expl = pair.get(f"{side}_expert_explanation") or []
            gpt4_expl = pair.get(f"{side}_gpt4_explanation") or []

            n = max(len(criteria), len(exp_labels), len(gpt4_labels))
            for i in range(n):
                out.append({
                    "trial_id": pair["trial_id"],
                    "patient_id": pair["patient_id"],
                    "side": side,
                    "criterion_idx": i,
                    "criterion_text": criteria[i] if i < len(criteria) else None,
                    "expert_label": exp_labels[i] if i < len(exp_labels) else None,
                    "gpt4_label": gpt4_labels[i] if i < len(gpt4_labels) else None,
                    "expert_explanation": exp_expl[i] if i < len(exp_expl) else None,
                    "gpt4_explanation": gpt4_expl[i] if i < len(gpt4_expl) else None,
                    "_source_split": pair.get("_source_split"),
                })
    return out


def summarize(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Quick label-distribution summary."""
    from collections import Counter
    inc = Counter()
    exc = Counter()
    for p in pairs:
        for l in p.get("inclusion_expert_eligibility") or []:
            inc[l] += 1
        for l in p.get("exclusion_expert_eligibility") or []:
            exc[l] += 1
    return {
        "n_pairs": len(pairs),
        "n_trials": len({p["trial_id"] for p in pairs}),
        "n_patients": len({p["patient_id"] for p in pairs}),
        "inclusion_label_counts": dict(inc),
        "exclusion_label_counts": dict(exc),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Load and summarize expert labels.")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--split", default="all")
    ap.add_argument("--flatten", action="store_true", help="Print per-criterion rows.")
    args = ap.parse_args()

    pairs = load_expert_labels(args.data_root, args.split)
    print(json.dumps(summarize(pairs), indent=2))

    if args.flatten:
        rows = flatten_to_criteria(pairs)
        print(f"\n{len(rows)} criterion-level rows")
        for r in rows[:3]:
            print(json.dumps(r, indent=2, ensure_ascii=False))
