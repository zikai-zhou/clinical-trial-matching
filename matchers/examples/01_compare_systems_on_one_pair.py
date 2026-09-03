"""Example: run all 9 variants on one (patient, trial) pair and compare audit trails."""
from matchers import variants, VARIANTS
from matchers.data import load_judge_verdict

PAIR = "sigir-20141__NCT02001545"  # STEMI/NSTEMI case — gold says ineligible

gold = load_judge_verdict(PAIR)
print(f"Pair: {PAIR}  |  gold (clinician_v2 judge): {gold}\n")

for name, fn in VARIANTS.items():
    d = fn(PAIR)
    correct = "✓" if d.decision == gold else "✗"
    audit = "AUDITABLE" if d.is_auditable() else "PARTIAL-AUDIT"
    print(f"  [{correct}] {name:<28s} {d.decision:<12s} {audit}")
    print(f"      {d.reasoning[:120]}")
