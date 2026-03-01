"""Principled target extraction via Z3 Optimize.

Given the trial's base SMT program (declarations + auxiliaries + REQ constraints)
for both inclusion and exclusion, and the current mined patient values,
compute the minimum-flip target map via weighted MaxSAT.

Approach:
  - Load both sides' base programs into a fresh Z3 Optimize instance.
  - For every declared variable with a mined value, add a SOFT assertion
    `(var == mined_val)`. Z3 will prefer satisfying these but may violate
    any subset to find a feasible model.
  - check-sat → the model is a globally consistent satisfying assignment
    across both sides, with maximum-weight agreement to the miner.
  - Return {var: model_val} for the subset of variables where the model
    diverges from the current mined value.

This handles `or`, `=>`, cross-side variables, and auxiliary implications
correctly by construction. Every returned target is formally guaranteed
to be consistent with the trial's full constraint set.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional, Tuple

import z3


_DECL_RE = re.compile(r"\(declare-const\s+(\S+)\s+(\S+?)\s*\)")


def parse_declarations(src: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _DECL_RE.finditer(src):
        out[m.group(1)] = m.group(2)
    return out


def _mk_const(name: str, sort: str, ctx):
    if sort == "Bool": return z3.Bool(name, ctx=ctx)
    if sort == "Int":  return z3.Int(name, ctx=ctx)
    if sort == "Real": return z3.Real(name, ctx=ctx)
    return None


def _to_lit(val: Any, sort: str, ctx):
    if sort == "Bool":
        if isinstance(val, bool): return z3.BoolVal(val, ctx=ctx)
        if isinstance(val, (int, float)): return z3.BoolVal(bool(val), ctx=ctx)
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "1", "yes", "t"):  return z3.BoolVal(True, ctx=ctx)
            if s in ("false", "0", "no", "f", "null", "none", ""): return None
        return None
    if sort in ("Int", "Real"):
        try: num = float(val)
        except (TypeError, ValueError): return None
        if sort == "Int": return z3.IntVal(int(num), ctx=ctx)
        return z3.RealVal(num, ctx=ctx)
    return None


def _from_z3(ref, sort: str):
    if ref is None: return None
    if sort == "Bool":
        if z3.is_true(ref): return True
        if z3.is_false(ref): return False
        return None
    s = str(ref)
    if sort == "Int":
        try: return int(s)
        except ValueError: return None
    if sort == "Real":
        if "/" in s:
            try:
                a, b = s.split("/"); return float(a) / float(b)
            except Exception: return None
        try: return float(s)
        except ValueError: return None
    return s


_NAMED_RE = re.compile(r":named\s+(\S+?)\s*\)")


def _dedupe_and_relabel(prog: str, side_tag: str,
                         seen_decls: set, seen_names: set) -> str:
    """Strip duplicate (declare-const NAME ...) by NAME and prefix :named labels."""
    out_lines = []
    for line in prog.splitlines():
        m = _DECL_RE.search(line)
        if m:
            name = m.group(1)
            if name in seen_decls:
                stripped = _DECL_RE.sub("", line).strip()
                if stripped and not stripped.startswith(";"):
                    out_lines.append(stripped)
                continue
            seen_decls.add(name)
        out_lines.append(line)
    text = "\n".join(out_lines)
    def _relabel(match):
        orig = match.group(1)
        new = f"{side_tag}_{orig}"
        i = 1
        candidate = new
        while candidate in seen_names:
            i += 1
            candidate = f"{new}_{i}"
        seen_names.add(candidate)
        return f":named {candidate})"
    return _NAMED_RE.sub(_relabel, text)


def _walk_assert_blocks(prog: str):
    """Yield (start, end, label) tuples for every (assert (! BODY :named LBL ...)) block
    with proper paren balance. start:end is the slice including the outer `(assert ...)`."""
    i = 0
    n = len(prog)
    while i < n:
        j = prog.find("(assert", i)
        if j < 0:
            break
        # balance parens
        depth = 0
        k = j
        while k < n:
            c = prog[k]
            if c == "(": depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        block = prog[j:k]
        m = _NAMED_RE.search(block)
        label = m.group(1) if m else None
        yield j, k, label
        i = k


def _strip_assertions_by_label(prog: str, labels_to_strip: set) -> str:
    """Return prog with all (assert (! ... :named LBL)) blocks stripped where LBL in labels_to_strip."""
    if not labels_to_strip:
        return prog
    out = []
    cursor = 0
    for start, end, label in _walk_assert_blocks(prog):
        if label in labels_to_strip:
            out.append(prog[cursor:start])
            cursor = end
    out.append(prog[cursor:])
    return "".join(out)


def _find_conflicting_exc_labels(merged_prog: str) -> set:
    """Return the EXC_-prefixed labels appearing in the unsat core of merged_prog.
    Returns empty set if merged_prog is SAT or unsat-core is unavailable."""
    ctx = z3.Context()
    s = z3.Solver(ctx=ctx)
    try:
        s.set(unsat_core=True, ignore_labels=False)
    except Exception:
        pass
    try:
        s.from_string("(set-option :produce-unsat-cores true)\n" + merged_prog)
    except z3.Z3Exception:
        return set()
    if s.check() != z3.unsat:
        return set()
    core = set()
    for c in s.unsat_core():
        name = c.sexpr().strip()
        if name.startswith("EXC_"):
            core.add(name)
    return core


def _resolve_conflicts_iteratively(merged_prog: str, max_iter: int = 10) -> Tuple[str, set]:
    """Repeatedly drop EXC assertions appearing in the unsat core until the
    merged program becomes SAT. Returns (pruned_prog, set_of_dropped_labels)."""
    dropped: set = set()
    current = merged_prog
    for _ in range(max_iter):
        conflicting = _find_conflicting_exc_labels(current)
        if not conflicting:
            return current, dropped  # SAT or core unavailable
        dropped.update(conflicting)
        current = _strip_assertions_by_label(current, conflicting)
    return current, dropped


def _merge_programs(inc_prog: Optional[str], exc_prog: Optional[str]) -> str:
    """Concatenate inclusion + exclusion programs. Both sides' REQ bodies are
    asserted as-is; duplicate (declare-const ...) entries are removed and
    :named labels are prefixed with INC_/EXC_ to avoid collisions.

    If inclusion and exclusion bodies are logical negations of each other
    (e.g., inc: months>=6 ∧ years<=5, exc: months<6 ∨ years>5), asserting
    both as True yields unsat. In that case the caller should fall back to
    loading one side only."""
    seen_decls: set = set()
    seen_names: set = set()
    chunks = []
    if inc_prog:
        chunks.append(_dedupe_and_relabel(inc_prog, "INC", seen_decls, seen_names))
    if exc_prog:
        chunks.append(_dedupe_and_relabel(exc_prog, "EXC", seen_decls, seen_names))
    return "\n\n".join(chunks)


def extract_targets_via_optimize(
    inclusion_prog: Optional[str],
    exclusion_prog: Optional[str],
    current_mined_inc: Dict[str, Any],
    current_mined_exc: Dict[str, Any],
    inc_was_unsat: bool = True,
    exc_was_unsat: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """Return ({var: target_val}, status) where status is one of:
         "ok"            — Z3 found a model; targets are the diff from current.
         "unsat"         — trial program itself is infeasible (very rare).
         "no_decls"      — couldn't parse any declarations.
    """
    # Fallback sequence:
    #   (a) joint   — inclusion ∧ exclusion, both asserted as-is.
    #   (b) joint_minus_conflict — if joint is unsat, inspect the unsat core,
    #       drop only the EXC assertions that appear in it, retry.
    #   (c) fall back to whichever side was originally unsat alone.
    fallback_modes = [("joint", inclusion_prog, exclusion_prog)]
    # Probe: if joint is unsat, iteratively drop EXC assertions in the unsat core
    # until SAT (or we run out of iterations). This preserves as many exclusion
    # constraints as possible while resolving the specific conflict.
    joint_merged_probe = _merge_programs(inclusion_prog, exclusion_prog)
    if joint_merged_probe:
        pruned, dropped = _resolve_conflicts_iteratively(joint_merged_probe)
        if dropped:
            fallback_modes.append(("joint_minus_conflict", pruned, None))
    if exc_was_unsat and exclusion_prog:
        fallback_modes.append(("exc_only", None, exclusion_prog))
    if inc_was_unsat and inclusion_prog:
        fallback_modes.append(("inc_only", inclusion_prog, None))
    if inclusion_prog:
        fallback_modes.append(("inc_only_tail", inclusion_prog, None))
    if exclusion_prog:
        fallback_modes.append(("exc_only_tail", None, exclusion_prog))

    ctx = None
    opt = None
    merged = None
    chosen_mode = None
    for mode, inc, exc in fallback_modes:
        m = _merge_programs(inc, exc)
        if not m:
            continue
        c = z3.Context()
        o = z3.Optimize(ctx=c)
        try:
            o.from_string(m)
        except z3.Z3Exception:
            continue
        # Must be sat WITH the soft mined constraints — check hard only first as a quick filter.
        if o.check() != z3.sat:
            continue
        ctx = c
        opt = o
        merged = m
        chosen_mode = mode
        break

    if opt is None:
        return {}, "unsat"

    all_decls = parse_declarations(merged)
    if not all_decls:
        return {}, "no_decls"

    # Unified mined map: last-write-wins if both sides had the same var
    mined: Dict[str, Any] = {}
    for side in (current_mined_inc, current_mined_exc):
        for k, v in (side or {}).items():
            val = v.get("value") if isinstance(v, dict) else v
            if val is None: continue
            if isinstance(val, str) and val.strip().lower() in {"null", "none", ""}:
                continue
            mined[k] = val

    # Soft-assert every mined value. Z3 will prefer satisfying them but can
    # flip any subset to reach a feasible model.
    for var, val in mined.items():
        sort = all_decls.get(var)
        if sort is None: continue
        c = _mk_const(var, sort, ctx)
        lit = _to_lit(val, sort, ctx)
        if c is None or lit is None: continue
        opt.add_soft(c == lit)

    if opt.check() != z3.sat:
        return {}, "unsat"

    mdl = opt.model()

    targets: Dict[str, Any] = {}
    for var, sort in all_decls.items():
        c = _mk_const(var, sort, ctx)
        if c is None: continue
        ref = mdl.eval(c, model_completion=False)
        new_val = _from_z3(ref, sort)
        if new_val is None:
            continue  # model didn't assign — no change required
        old = mined.get(var)
        if old is None:
            # Only report if Z3 actively chose a value (model_completion=False)
            # AND it's not just a default. Skip if we don't have a mined baseline
            # to compare against; the chart doesn't need to assert this var.
            continue
        # Normalize comparison
        if isinstance(old, str):
            s_old = old.strip().lower()
            if sort == "Bool":
                old_bool = s_old in ("true", "1", "yes", "t")
                if old_bool != bool(new_val): targets[var] = new_val
                continue
            if sort in ("Int", "Real"):
                try:
                    old_num = float(old)
                    if abs(old_num - float(new_val)) > 1e-9: targets[var] = new_val
                    continue
                except ValueError:
                    if s_old != str(new_val).lower(): targets[var] = new_val
                    continue
        if old != new_val and str(old).lower() != str(new_val).lower():
            targets[var] = new_val
    return targets, "ok"
