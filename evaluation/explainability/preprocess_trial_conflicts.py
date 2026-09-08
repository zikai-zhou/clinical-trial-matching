"""Preprocess SMT program: remove exclusion REQs that are infeasible given
the inclusion feasible region. These are inc-exc compilation redundancies
(e.g., inc requires age in [6mo, 5yr]; exc asserts age out of that range
as a positive exclusion condition — the same axis twice, with opposite sign).

Under a preprocessed program, patients have a chance to be eligible on the
axes that are NOT cross-side-conflicted. The original SMT over-constrains.
"""
from __future__ import annotations
import math
import re
from typing import Dict, List, Tuple

from evaluation.explainability.smtlib_parser import extract_named_assertions, Expr


def _numeric_range(expr):
    if not isinstance(expr, list) or len(expr) != 3:
        return None
    op, a, b = expr
    if not isinstance(a, str) or isinstance(b, list):
        return None
    try:
        n = float(b)
    except (TypeError, ValueError):
        return None
    if op == ">=":
        return (a, (n, math.inf))
    if op == ">":
        return (a, (n + 1e-9, math.inf))
    if op == "<=":
        return (a, (-math.inf, n))
    if op == "<":
        return (a, (-math.inf, n - 1e-9))
    if op == "=":
        return (a, (n, n))
    return None


def collect_inc_numeric_region(named_inc: Dict[str, Expr]) -> Dict[str, Tuple[float, float]]:
    """Conjunction of inc REQ bodies, per-variable numeric feasible region."""
    cons: Dict[str, Tuple[float, float]] = {}

    def walk(body):
        if isinstance(body, list) and body and body[0] == "and":
            for s in body[1:]:
                walk(s)
            return
        r = _numeric_range(body)
        if r:
            var, rng = r
            prev = cons.get(var, (-math.inf, math.inf))
            cons[var] = (max(prev[0], rng[0]), min(prev[1], rng[1]))
    for body in named_inc.values():
        walk(body)
    return cons


def _body_conflicts_inc(body: Expr, inc_ranges: Dict[str, Tuple[float, float]]) -> bool:
    """True if `body` asserted True is infeasible under inc ranges.
    Handles (or A B ...) by checking if ALL disjuncts conflict."""
    if isinstance(body, list) and body and body[0] == "or":
        for sub in body[1:]:
            if not _body_conflicts_inc(sub, inc_ranges):
                return False
        return True  # all disjuncts conflict
    # Atomic numeric comparison
    r = _numeric_range(body)
    if r:
        var, (lo, hi) = r
        inc_r = inc_ranges.get(var)
        if inc_r:
            lo_int = max(lo, inc_r[0])
            hi_int = min(hi, inc_r[1])
            return lo_int > hi_int
    # Non-numeric or unknown — conservatively return False (no conflict)
    return False


def identify_conflicting_exc_labels(inc_smt: str, exc_smt: str) -> List[str]:
    """Return list of exc REQ labels whose bodies are infeasible under inc."""
    named_inc = extract_named_assertions(inc_smt)
    named_exc = extract_named_assertions(exc_smt)
    inc_ranges = collect_inc_numeric_region(named_inc)
    conflicting = []
    for lbl, body in named_exc.items():
        if "_AUXILIARY" in lbl:
            continue
        if _body_conflicts_inc(body, inc_ranges):
            conflicting.append(lbl)
    return conflicting


_ASSERT_NAMED_RE = re.compile(
    r"\(assert\s*\(!\s*.*?\s*:named\s+(\S+)\s*\)\s*\)(?:\s*;;[^\n]*)?",
    re.DOTALL,
)


def remove_labeled_assertions(smt_source: str, labels_to_remove: set) -> str:
    """Remove (assert (! body :named LABEL)) blocks whose LABEL is in labels_to_remove.
    Uses paren-balance walking like extract_named_assertions."""
    if not labels_to_remove:
        return smt_source

    from evaluation.explainability.smtlib_parser import _tokenize, _parse_tokens

    # Find each (assert ...) block and remove by rewriting the string.
    out_chunks = []
    cursor = 0
    tokens = _tokenize(smt_source)

    # Rebuild with source offsets — we need position tracking.
    # Simpler approach: iterate over (assert...) top-level blocks by paren balance in the raw source.
    n = len(smt_source)
    i = 0
    while i < n:
        j = smt_source.find("(assert", i)
        if j < 0:
            out_chunks.append(smt_source[i:])
            break
        out_chunks.append(smt_source[i:j])
        # Balance parens starting at j
        depth = 0
        k = j
        while k < n:
            c = smt_source[k]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        block = smt_source[j:k]
        m = re.search(r":named\s+(\S+?)\s*\)", block)
        label = m.group(1) if m else None
        if label in labels_to_remove:
            # Absorb trailing newline/comment if any
            while k < n and smt_source[k] in " \t":
                k += 1
            if k < n and smt_source[k] == ";":
                # Eat the trailing `;; ...` comment line
                nl = smt_source.find("\n", k)
                k = n if nl < 0 else nl + 1
            elif k < n and smt_source[k] == "\n":
                k += 1
        else:
            out_chunks.append(block)
        i = k
    return "".join(out_chunks)


def preprocess_trial_smt(inc_smt: str, exc_smt: str) -> Tuple[str, str, List[str]]:
    """Preprocess trial SMT: identify inc-exc conflicting exc REQs and remove
    them from the exclusion program.

    Returns (pruned_inc_smt, pruned_exc_smt, removed_labels).
    """
    if not exc_smt:
        return inc_smt, exc_smt, []
    conflicting = identify_conflicting_exc_labels(inc_smt, exc_smt)
    if not conflicting:
        return inc_smt, exc_smt, []
    pruned_exc = remove_labeled_assertions(exc_smt, set(conflicting))
    return inc_smt, pruned_exc, conflicting
