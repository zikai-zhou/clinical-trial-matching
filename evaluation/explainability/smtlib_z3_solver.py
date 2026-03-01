"""Principled target extraction via Z3 itself.

Instead of heuristically walking unsat-core assertions to guess target values
(which is brittle for `or`-branches, `=>`-implications, and cross-variable
interactions), we let Z3 solve the problem directly:

  1. Parse the trial's base program (declarations + auxiliaries + REQ constraints).
  2. For each mined variable whose value is *not* involved in the unsat core,
     HARD-ASSERT `(= var val)` — these stay fixed.
  3. For each mined variable whose value *is* involved in the unsat core,
     SOFT-ASSERT `(= var val)` — Z3 prefers no flip but can flip if needed.
  4. Run `Optimize` → maximum-weight SAT assignment consistent with the trial.
  5. Diff model against current mined values → minimum-flip target map.

This is principled:
  - Z3 handles disjunctions by choosing the consistent branch.
  - Z3 handles implications by back-propagating antecedents/consequents.
  - Z3 handles cross-variable interactions by construction.
  - The returned targets are guaranteed SAT in the full program.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Set

import z3


# ─── Helpers ────────────────────────────────────────────────────────────

_DECL_RE = re.compile(r"\(declare-const\s+(\S+)\s+(\S+?)\s*\)")


def parse_declarations(src: str) -> Dict[str, str]:
    """Return {var_name: sort_name} from (declare-const ...) lines."""
    out: Dict[str, str] = {}
    for m in _DECL_RE.finditer(src):
        out[m.group(1)] = m.group(2)
    return out


def _mk_const(name: str, sort: str, ctx):
    if sort == "Bool":
        return z3.Bool(name, ctx=ctx)
    if sort == "Int":
        return z3.Int(name, ctx=ctx)
    if sort == "Real":
        return z3.Real(name, ctx=ctx)
    return None


def _to_z3_lit(val: Any, sort: str, ctx):
    """Coerce a Python value to a Z3 literal of the given sort."""
    if sort == "Bool":
        if isinstance(val, bool):
            return z3.BoolVal(val, ctx=ctx)
        if isinstance(val, (int, float)):
            return z3.BoolVal(bool(val), ctx=ctx)
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "1", "yes"):
                return z3.BoolVal(True, ctx=ctx)
            if s in ("false", "0", "no"):
                return z3.BoolVal(False, ctx=ctx)
        return None
    if sort in ("Int", "Real"):
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        if sort == "Int":
            return z3.IntVal(int(num), ctx=ctx)
        return z3.RealVal(num, ctx=ctx)
    return None


def _from_z3(ref, sort: str):
    if ref is None:
        return None
    s = str(ref)
    if sort == "Bool":
        if s == "true":
            return True
        if s == "false":
            return False
        return None
    if sort == "Int":
        try:
            return int(s)
        except ValueError:
            return None
    if sort == "Real":
        # Z3 prints rationals like "1/2"
        if "/" in s:
            try:
                a, b = s.split("/")
                return float(a) / float(b)
            except Exception:
                return None
        try:
            return float(s)
        except ValueError:
            return None
    return s


# ─── Main entry point ────────────────────────────────────────────────────

def extract_targets_via_z3(
    base_prog: str,
    current_mined: Dict[str, Any],
    unsat_core_vars: Set[str],
) -> Dict[str, Any]:
    """Compute minimum-flip target map using Z3 Optimize.

    Args:
        base_prog: full trial SMT-LIB source (declarations + auxiliaries + REQ constraints).
                   MUST NOT contain mined-value equality assertions.
        current_mined: {var: value} currently mined from patient chart.
                       Values can be bool, int, float, or string literals.
        unsat_core_vars: set of variable names appearing in the unsat-core REQ bodies.
                         These are allowed to flip (soft constraint).

    Returns:
        {var: target_value} for vars whose optimized value differs from the mined value,
        plus any core-involved vars that were previously None but now have a model value.
        Empty dict if base_prog is unsat on its own (trial internally inconsistent)
        or parsing fails.
    """
    ctx = z3.Context()
    opt = z3.Optimize(ctx=ctx)

    try:
        opt.from_string(base_prog)
    except z3.Z3Exception:
        return {}

    decls = parse_declarations(base_prog)
    if not decls:
        return {}

    # Apply mined values: hard for non-core, soft for core-involved.
    for var, val in current_mined.items():
        sort = decls.get(var)
        if sort is None:
            continue
        c = _mk_const(var, sort, ctx)
        lit = _to_z3_lit(val, sort, ctx)
        if c is None or lit is None:
            continue
        eq = (c == lit)
        if var in unsat_core_vars:
            opt.add_soft(eq)
        else:
            opt.add(eq)

    if opt.check() != z3.sat:
        return {}

    mdl = opt.model()

    targets: Dict[str, Any] = {}
    # Report new values for vars whose optimal model differs from the mined value.
    # Cover both core-involved vars (may flip) and any vars touched by the model.
    for var in unsat_core_vars | set(current_mined):
        sort = decls.get(var)
        if sort is None:
            continue
        c = _mk_const(var, sort, ctx)
        new_ref = mdl.eval(c, model_completion=True)
        new_val = _from_z3(new_ref, sort)
        if new_val is None:
            continue
        old = current_mined.get(var)
        # Normalize comparison
        if old is None:
            # Only include if the var is core-involved (we have reason to set it)
            if var in unsat_core_vars:
                targets[var] = new_val
            continue
        if isinstance(old, str):
            try:
                old_num = float(old)
                if sort in ("Int", "Real") and isinstance(new_val, (int, float)):
                    if abs(old_num - float(new_val)) > 1e-9:
                        targets[var] = new_val
                    continue
            except ValueError:
                pass
        if str(old).lower() != str(new_val).lower() and old != new_val:
            targets[var] = new_val
    return targets
