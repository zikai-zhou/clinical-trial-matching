"""MaxSMT accountability artifacts — Steps 2-6 of the VERDICT algorithm.

Implements the published formulation (VERDICT, EMNLP update):

    S      the patient constraints: /\\_i {c_i = v_i} for every OBSERVED or
           IMPUTED condition.
    Step 2 (z, gamma) = SMT(phi_t /\\ S).  d = ELIGIBLE iff z = SAT.
    Step 3 delta_E = {s in S | MAXSMT(phi_t /\\ S, W).s = FALSE}
           i.e. which asserted patient facts the solver had to give up to reach
           ELIGIBLE.  delta_E = {} iff d = ELIGIBLE.
    Step 4 rho = /\\_i {c_i = MAXSMT(phi_t /\\ S, W).c_i | q_i = UNRESOLVED}
           the *assumptions*: the values the solver assigns to conditions the
           chart never settled.
    Step 5 delta_I = {x in S /\\ rho | MAXSMT(-phi_t /\\ S /\\ rho, W').x = FALSE}
           delta_I = {} iff d = INELIGIBLE.
    Step 6 delta = delta_I if d = ELIGIBLE else delta_E.

Terminology note. Up to the submitted version rho was called *residual
constraints* and held unresolved conditions as constraints ({crcl >= 60}).
It is now *assumptions* and holds the witness the solver assigns
({crcl = 72}). The two are not interchangeable: one is a requirement, the
other one satisfying value of it.

Verbalizer rule (Step 7). A witness is arbitrary within the satisfying
region, so a rationale must report the *requirement* phi_t imposes on a
numeric condition, not the witness. `requirement_for()` recovers that bound
from the program text; `Artifacts.for_verbalizer()` applies it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

OBSERVED, IMPUTED, UNRESOLVED = "OBSERVED", "IMPUTED", "UNRESOLVED"
ELIGIBLE, INELIGIBLE = "eligible", "ineligible"


@dataclass
class Condition:
    """One condition c_i with its resolved value v_i and status q_i."""
    name: str
    value: Any = None                    # v_i; None when UNRESOLVED
    status: str = UNRESOLVED             # q_i
    policy: Optional[str] = None         # a_i, the policy realization if IMPUTED

    @property
    def resolved(self) -> bool:
        return self.status in (OBSERVED, IMPUTED)


@dataclass
class Artifacts:
    """The accountability artifacts of one decision."""
    decision: str                                   # d
    trace: str                                      # gamma
    assumptions: Dict[str, Any] = field(default_factory=dict)   # rho
    pivotal: List[str] = field(default_factory=list)            # delta
    delta_e: List[str] = field(default_factory=list)
    delta_i: List[str] = field(default_factory=list)
    status: str = "ok"

    def for_verbalizer(self, phi_lines: Sequence[str]) -> Dict[str, Any]:
        """Step 7 view: numeric conditions reported as requirements, not witnesses.

        The solver's witness for an unresolved numeric condition is arbitrary
        within the satisfying region, so surfacing it ("creatinine clearance
        72") would invent a fact. We surface the requirement instead
        ("creatinine clearance >= 60").
        """
        out_rho = {}
        for name, witness in self.assumptions.items():
            req = requirement_for(name, phi_lines)
            out_rho[name] = {"requirement": req, "witness": witness} if req \
                else {"value": witness}
        return {
            "decision": self.decision,
            "trace": self.trace,
            "assumptions": out_rho,
            "pivotal": [{"condition": c, "requirement": requirement_for(c, phi_lines)}
                        for c in self.pivotal],
        }


_CMP = r"(>=|<=|>|<|=)"


def requirement_for(name: str, phi_lines: Sequence[str]) -> Optional[str]:
    """Recover the numeric bound phi_t imposes on `name`, e.g. '>= 60'.

    Returns None for non-numeric conditions, where the witness IS the fact.
    """
    esc = re.escape(name)
    for ln in phi_lines:
        m = re.search(rf"\(\s*{_CMP}\s+\|?{esc}\|?\s+([-\d.]+)\s*\)", ln)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        m = re.search(rf"\(\s*{_CMP}\s+([-\d.]+)\s+\|?{esc}\|?\s*\)", ln)
        if m:                                  # reversed operand order
            flip = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "=": "="}
            return f"{flip[m.group(1)]} {m.group(2)}"
    return None


def _z3():
    """Import z3, distinguishing 'absent' from 'shadowed by something else'.

    A bare `import z3` can succeed against an unrelated namespace package (no
    __file__, no attributes), which then fails much later with a confusing
    AttributeError. Probe for the API we actually need.
    """
    try:
        import z3
    except ImportError as e:                    # pragma: no cover
        raise ImportError(
            "MaxSMT needs the solver: pip install 'z3-solver>=4.12'") from e
    missing = [a for a in ("parse_smt2_string", "Optimize", "Solver")
               if not hasattr(z3, a)]
    if missing:                                 # pragma: no cover
        raise ImportError(
            f"the importable 'z3' is not z3-solver (missing {missing}; "
            f"resolved to {getattr(z3, '__file__', None)!r}). Something else "
            f"named z3 is shadowing it -- pip install 'z3-solver>=4.12' into "
            f"this interpreter.")
    return z3


def _decl_lines(conds: Sequence[Condition], phi_lines: Sequence[str]) -> List[str]:
    """Declare any condition the program text does not already declare."""
    out = []
    for c in conds:
        if any(re.search(rf"\(declare-(const|fun)\s+\|?{re.escape(c.name)}\|?\s+", ln)
               for ln in phi_lines):
            continue
        sort = "Real" if isinstance(c.value, (int, float)) and not isinstance(
            c.value, bool) else "Bool"
        out.append(f"(declare-const |{c.name}| {sort})")
    return out


def _lit(c: Condition) -> str:
    """The SMT literal asserting c_i = v_i."""
    if isinstance(c.value, bool):
        return f"|{c.name}|" if c.value else f"(not |{c.name}|)"
    return f"(= |{c.name}| {c.value})"


def solve(phi_lines: Sequence[str], conditions: Sequence[Condition]) -> Artifacts:
    """Run Steps 2-6 and return the artifacts.

    Args:
        phi_lines:  the trial program phi_t, as SMT-LIB lines (hard constraints).
        conditions: every c_i with its status; resolved ones form S.
    """
    z3 = _z3()
    phi_lines = list(phi_lines)
    resolved = [c for c in conditions if c.resolved]
    unresolved = [c for c in conditions if not c.resolved]
    prelude = "\n".join(list(phi_lines) + _decl_lines(conditions, phi_lines))

    def parse(extra: str = "", negate_phi: bool = False):
        """Build a z3 goal from the program text (+ extra assertions)."""
        src = prelude + "\n" + extra
        try:
            asserts = z3.parse_smt2_string(src)
        except z3.Z3Exception as e:
            raise ValueError(f"cannot parse SMT program: {e}") from e
        return list(asserts)

    # ---- Step 2: the decision itself -------------------------------------
    hard = parse()
    S_exprs = parse("\n".join(f"(assert {_lit(c)})" for c in resolved))[len(hard):]
    s = z3.Solver()
    s.add(*hard, *S_exprs)
    z = s.check()
    if z == z3.unknown:
        return Artifacts(decision=INELIGIBLE, trace="solver returned unknown",
                         status="unknown")
    decision = ELIGIBLE if z == z3.sat else INELIGIBLE
    trace = "SAT under the asserted patient constraints" if z == z3.sat \
        else "UNSAT under the asserted patient constraints"

    # ---- Steps 3-4: MAXSMT(phi /\ S, W) ----------------------------------
    opt = z3.Optimize()
    opt.add(*hard)
    for c, e in zip(resolved, S_exprs):
        opt.add_soft(e, 1, "S")
    delta_e: List[str] = []
    assumptions: Dict[str, Any] = {}
    if opt.check() == z3.sat:
        m = opt.model()
        for c, e in zip(resolved, S_exprs):            # Step 3
            if not z3.is_true(m.eval(e, model_completion=True)):
                delta_e.append(c.name)
        for c in unresolved:                            # Step 4
            for d in m.decls():
                if d.name() == c.name:
                    assumptions[c.name] = _py(m[d])
                    break
    else:
        trace += "; trial constraints unsatisfiable in isolation"

    # ---- Step 5: MAXSMT(-phi /\ S /\ rho, W') ----------------------------
    delta_i: List[str] = []
    rho_lines = "\n".join(
        f"(assert (= |{k}| {_smt_val(v)}))" for k, v in assumptions.items())
    try:
        all_hard = parse(rho_lines)
        rho_exprs = all_hard[len(hard):]
        opt2 = z3.Optimize()
        opt2.add(z3.Not(z3.And(*hard)) if hard else z3.BoolVal(False))
        soft2 = list(S_exprs) + list(rho_exprs)
        names2 = [c.name for c in resolved] + list(assumptions)
        for e in soft2:
            opt2.add_soft(e, 1, "Sp")
        if opt2.check() == z3.sat:
            m2 = opt2.model()
            for nm, e in zip(names2, soft2):
                if not z3.is_true(m2.eval(e, model_completion=True)):
                    delta_i.append(nm)
    except Exception:                                   # pragma: no cover
        trace += "; delta_I unavailable"

    # ---- Step 6 ----------------------------------------------------------
    pivotal = delta_i if decision == ELIGIBLE else delta_e
    return Artifacts(decision=decision, trace=trace, assumptions=assumptions,
                     pivotal=pivotal, delta_e=delta_e, delta_i=delta_i)


def _py(v) -> Any:
    """z3 model value -> plain Python."""
    z3 = _z3()
    if z3.is_true(v):
        return True
    if z3.is_false(v):
        return False
    try:
        if v.is_int():
            return v.as_long()
        return float(v.as_decimal(6).rstrip("?"))
    except Exception:
        return str(v)


def _smt_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
