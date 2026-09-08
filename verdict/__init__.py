"""VERDICT — auditable patient--trial eligibility matching.

Decide a pair and see exactly why:

    import verdict

    d = verdict.match("sigir-20141__NCT00337116")
    print(d.decision, d.reasoning)
    for step in d.audit_trail:
        print(step.stage, step.decision)

Pick a different matcher variant:

    verdict.match(pair_id, system="hybrid")
    verdict.systems()                     # {name: description}

Missing pair data is NOT a verdict. By default the underlying variants return
decision="ineligible", reasoning="no data" (the behaviour the paper's numbers
were produced under), which downstream is indistinguishable from a real
INELIGIBLE. This API defaults to strict=True and raises instead:

    verdict.match("nope__NCT0")           # raises MissingPairData
    verdict.match(pair_id, strict=False)  # paper-compatible sentinel

Pair data is read from $VERDICT_PAIR_DATA (default <repo>/experiments/53_v2_full).
"""
from __future__ import annotations

from matchers.schema import Decision, AuditStep, MissingPairData, NO_DATA

__all__ = ["match", "explain", "systems", "pairs",
           "Decision", "AuditStep", "MissingPairData", "NO_DATA"]

# public name -> matchers.variants function
SYSTEMS = {
    "verdict":  ("smt_lm_evidence_arbiter",
                 "SMT + LLM auditor with LM-judge evidence (recommended, auditable)"),
    "smt-only": ("smt_raw", "atom miner + Z3 only, no review"),
    "atoms":    ("smt_atoms_arbiter", "SMT + LLM auditor on rejects (atoms only)"),
    "lm-only":  ("lm_only", "single LLM call over chart + criteria"),
    "hybrid":   ("hybrid_strict", "accept-if-either + LM-evidence arbiter (max F1)"),
    "trialgpt": ("trialgpt", "TrialGPT baseline (Yang et al. 2023)"),
}


def systems() -> dict[str, str]:
    """Available matcher variants, as {name: description}."""
    return {k: desc for k, (_, desc) in SYSTEMS.items()}


def pairs() -> list[str]:
    """Pair ids available in the configured pair-data directory."""
    from verdict_cli import iter_pairs
    return list(iter_pairs())


def match(pair_id: str, system: str = "verdict", *, strict: bool = True) -> Decision:
    """Decide one patient--trial pair.

    Args:
        pair_id: "<patient>__<NCT>", e.g. "sigir-20141__NCT00337116".
        system:  a key of SYSTEMS (default "verdict").
        strict:  raise MissingPairData when the pair cannot be loaded.
                 Set False for the paper's sentinel behaviour.

    Returns:
        Decision(pair_id, variant, decision, reasoning, audit_trail).

    Raises:
        KeyError:          unknown `system`.
        MissingPairData:   pair not loadable and strict=True.
    """
    if system not in SYSTEMS:
        raise KeyError(f"unknown system {system!r}; choose from {sorted(SYSTEMS)}")
    from matchers import variants
    fn = getattr(variants, SYSTEMS[system][0])
    was = variants.is_strict()
    variants.strict(strict)
    try:
        return fn(pair_id)
    finally:
        variants.strict(was)


def explain(pair_id: str, system: str = "verdict", *, strict: bool = True) -> str:
    """Same decision as match(), rendered as a readable audit trail."""
    d = match(pair_id, system, strict=strict)
    out = [f"pair    : {pair_id}",
           f"system  : {system}",
           f"decision: {d.decision.upper()}",
           f"why     : {d.reasoning}",
           "",
           f"audit trail ({len(d.audit_trail)} steps)"]
    for i, step in enumerate(d.audit_trail, 1):
        fields = step if isinstance(step, dict) else getattr(step, "__dict__", {})
        name = fields.get("stage") or fields.get("step") or f"step {i}"
        out.append(f"{i}. {name}")
        for k, v in fields.items():
            if k in ("stage", "step") or v in (None, "", {}, []):
                continue
            out.append(f"     {k}: {str(v)[:200]}")
    return "\n".join(out)
