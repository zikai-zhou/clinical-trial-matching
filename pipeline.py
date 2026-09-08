"""End-to-end screening: retrieve candidate trials, then decide each one.

    import pipeline

    for r in pipeline.screen("sigir-20141", db="build/trial.db", limit=10):
        print(r.rank, r.nct_id, r.decision or "(no data)")

This is the only module that touches both systems. SatIR and VERDICT must not
import each other -- coupling them would mean you could not run retrieval
without the matcher, or the matcher without a database -- so the join lives
here, one layer above both.

    parser  ->  SatIR      (which trials are worth a look?)
            ->  VERDICT    (does this patient meet this one, and why?)
                    \\
                     pipeline: the two in sequence

What each stage needs
---------------------
SatIR    a clause database. Pure SQL: no LLM, no services.
VERDICT  per-pair stage-1 artifacts under $VERDICT_PAIR_DATA. Candidates
         without them come back with decision=None rather than a guess --
         "not evaluated" must never be reportable as "not eligible".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["ScreenResult", "screen"]


@dataclass
class ScreenResult:
    """One candidate trial, retrieved and (where possible) decided."""
    nct_id: str
    rank: Optional[int]
    retrieval_label: str
    decision: Optional[str] = None      # None == VERDICT had no data for it
    reasoning: str = ""
    assumptions: Dict[str, Any] = field(default_factory=dict)
    pivotal: List[str] = field(default_factory=list)

    @property
    def eligible(self) -> Optional[bool]:
        """True / False, or None when the pair could not be evaluated."""
        return None if self.decision is None else self.decision == "eligible"

    @property
    def evaluated(self) -> bool:
        return self.decision is not None


def screen(patient_id: str, *, db: Optional[str] = None,
           limit: Optional[int] = None, system: str = "verdict",
           artifacts: bool = True) -> List[ScreenResult]:
    """Retrieve candidates for a patient, then decide each with VERDICT.

    Args:
        patient_id: e.g. "sigir-20141".
        db:         clause database for retrieval.
        limit:      stop after this many candidates (they are rank-ordered).
        system:     matcher variant; see verdict.systems().
        artifacts:  also compute assumptions and pivotal conditions.

    Returns:
        One ScreenResult per candidate, in retrieval rank order. Candidates
        VERDICT cannot evaluate keep decision=None.
    """
    import satir
    import verdict

    candidates = satir.retrieve(patient_id, db=db)
    if limit is not None:
        candidates = candidates[:limit]

    results: List[ScreenResult] = []
    for c in candidates:
        r = ScreenResult(nct_id=c.nct_id, rank=c.rank,
                         retrieval_label=c.label)
        pair = f"{patient_id}__{c.nct_id}"
        try:
            d = verdict.match(pair, system=system)      # strict: raises if absent
        except verdict.MissingPairData:
            results.append(r)                            # decision stays None
            continue
        r.decision, r.reasoning = d.decision, d.reasoning
        if artifacts:
            try:
                from verdict.artifacts import artifacts_for
                a = artifacts_for(pair)
                if a is not None:
                    r.assumptions, r.pivotal = a.assumptions, a.pivotal
            except Exception:
                pass                                     # artifacts are optional
        results.append(r)
    return results
