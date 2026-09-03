"""Common types for matchers."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuditStep:
    """One step in the system's reasoning chain.

    Every variant's `audit_trail` is a list of AuditSteps. The same step types
    are used across variants so a reader can directly compare what each variant
    does/doesn't produce.
    """
    stage: str            # one of: "atom_mining", "smt_solve", "lm_judge",
                          #         "atoms_only_arbiter", "lm_evidence_arbiter",
                          #         "accept_if_either", "trialgpt_score"
    decision: Optional[str] = None    # "eligible" / "ineligible" / None
    rationale: str = ""               # human-readable explanation
    evidence: Dict[str, Any] = field(default_factory=dict)  # structured data


@dataclass
class Decision:
    """The output of any variant. Every variant returns this type so they can
    be compared head-to-head."""
    pair_id: str
    variant: str
    decision: str                      # "eligible" / "ineligible"
    reasoning: str                     # one-sentence explanation
    audit_trail: List[AuditStep] = field(default_factory=list)

    def is_auditable(self) -> bool:
        """True iff every step in the trail traces to chart-grounded atoms.
        False if any step rests solely on free-text LM rationale (e.g.,
        accept-if-either when LM-judge alone forwarded a SMT-rejected case)."""
        for step in self.audit_trail:
            if step.stage == "accept_if_either" and step.evidence.get("authority") == "lm_judge_alone":
                return False
        return True

    def summarize(self) -> str:
        lines = [f"Pair: {self.pair_id}",
                 f"Variant: {self.variant}",
                 f"Decision: {self.decision}",
                 f"Auditable: {self.is_auditable()}",
                 f"Reasoning: {self.reasoning}",
                 "Audit trail:"]
        for i, step in enumerate(self.audit_trail):
            lines.append(f"  [{i+1}] {step.stage}: {step.decision or '(no verdict)'}")
            if step.rationale:
                lines.append(f"      rationale: {step.rationale[:140]}")
            for k, v in step.evidence.items():
                vs = str(v)[:80]
                lines.append(f"      {k}: {vs}")
        return "\n".join(lines)
