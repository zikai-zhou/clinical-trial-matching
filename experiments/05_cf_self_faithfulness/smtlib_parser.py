"""Minimal SMT-LIB s-expression parser for extracting {variable: target_value}
from named assertions in the unsat core.

Key features:
  - Paren-balance walker to extract each (assert (! ... :named LABEL)) accurately
    (avoids regex mis-attribution on nested :named tags).
  - Tree walker on the inner expression:
      * (= var literal)  → var = literal
      * (not var)        → var = False
      * var              → var = True  (bare Boolean)
      * (>= var N), (<= var N), (> var N), (< var N)
                         → var = value satisfying the inequality
      * (and ...)        → union of targets from each sub-expression
      * (or ...)         → pick the FIRST branch that yields any targets
                           (heuristic; sound for prescreen)
  - Returns a dict {variable: target_value} aggregated across all unsat-core
    assertions.

Limitations:
  - Does not reason about variable interactions; if two unsat assertions
    constrain the same variable incompatibly, last-write-wins.
  - For `or`, the leftmost-feasible heuristic doesn't always match what Z3
    would pick, but usually works for prescreen-style criteria.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple, Union


Atom = Union[str, int, float, bool]
Expr = Union[Atom, List["Expr"]]


# ─── s-expression tokenizer/parser ────────────────────────────────────────

def _tokenize(src: str) -> List[str]:
    """Simple SMT-LIB tokenizer; handles comments (;), strings, parens."""
    tokens: List[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
        elif c == ";":  # line comment
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == '\\':
                    j += 2
                else:
                    j += 1
            tokens.append(src[i:j + 1])
            i = j + 1
        elif c == "|":  # SMT-LIB quoted symbol
            j = i + 1
            while j < n and src[j] != "|":
                j += 1
            tokens.append(src[i:j + 1])
            i = j + 1
        else:
            j = i
            while j < n and src[j] not in " \t\r\n();":
                j += 1
            tokens.append(src[i:j])
            i = j
    return tokens


def _parse_tokens(tokens: List[str], pos: int = 0) -> Tuple[Expr, int]:
    """Parse a single s-expression starting at tokens[pos]. Returns (expr, next_pos)."""
    tok = tokens[pos]
    if tok == "(":
        lst: List[Expr] = []
        pos += 1
        while pos < len(tokens) and tokens[pos] != ")":
            sub, pos = _parse_tokens(tokens, pos)
            lst.append(sub)
        if pos >= len(tokens):
            raise ValueError("unbalanced parens")
        return lst, pos + 1
    else:
        return tok, pos + 1


def parse(src: str) -> Expr:
    """Parse a single s-expression; return nested list/atom structure."""
    tokens = _tokenize(src)
    expr, _ = _parse_tokens(tokens, 0)
    return expr


# ─── Walk SMT-LIB top-level to find named assertions ──────────────────────

def extract_named_assertions(smt_source: str) -> Dict[str, Expr]:
    """Given an SMT-LIB source text, return {label: body_expr} for every
    (assert (! body :named LABEL)) pattern, with proper paren-balance.
    Handles nested :named tags correctly."""
    tokens = _tokenize(smt_source)
    i = 0
    out: Dict[str, Expr] = {}
    # Walk top-level forms
    while i < len(tokens):
        if tokens[i] != "(":
            i += 1
            continue
        expr, next_i = _parse_tokens(tokens, i)
        i = next_i
        # Top-level form: (assert <body>)
        if (isinstance(expr, list) and len(expr) == 2
                and expr[0] == "assert"):
            body = expr[1]
            # body might be (! <inner> :named LABEL [..optional attrs..])
            if (isinstance(body, list) and len(body) >= 3 and body[0] == "!"):
                inner = body[1]
                # Walk attributes to find :named
                k = 2
                label = None
                while k < len(body) - 1:
                    if body[k] == ":named":
                        label = body[k + 1] if isinstance(body[k + 1], str) else None
                        break
                    k += 1
                if label:
                    out[label] = inner
    return out


# ─── Target-value extraction ──────────────────────────────────────────────

def _atom_value(x: Atom):
    """Convert an SMT literal atom to a Python value."""
    if isinstance(x, str):
        if x in ("true", "True"):
            return True
        if x in ("false", "False"):
            return False
        try:
            if "." in x:
                return float(x)
            return int(x)
        except ValueError:
            return x  # e.g., a symbol
    return x


def extract_targets(expr: Expr) -> Dict[str, Any]:
    """Given an assertion body expression (s-expression tree), return
    {variable: target_value} required to satisfy the assertion.
    Returns empty dict if nothing clean can be extracted."""
    out: Dict[str, Any] = {}

    if isinstance(expr, str):
        # Bare symbol = that variable must be True
        out[expr] = True
        return out

    if not isinstance(expr, list) or not expr:
        return out

    op = expr[0] if isinstance(expr[0], str) else None

    # (not X) → X = False (if X is an atom) OR recursive negation
    if op == "not":
        if len(expr) == 2:
            inner = expr[1]
            if isinstance(inner, str):
                out[inner] = False
                return out
            # (not (= var val)) → var != val, pick a Python-appropriate flip
            if isinstance(inner, list) and len(inner) == 3 and inner[0] == "=":
                v = inner[1] if isinstance(inner[1], str) else None
                val = inner[2]
                if v:
                    # For bool: flip; for numeric: pick a value not equal to val
                    bv = _atom_value(val) if not isinstance(val, list) else None
                    if isinstance(bv, bool):
                        out[v] = not bv
                    elif isinstance(bv, (int, float)):
                        out[v] = bv + 1 if bv else 1
                    return out
            # (not (and ...)) → at least one conjunct false; too complex, skip
            # (not (or ...)) → all false; apply De Morgan
            if isinstance(inner, list) and len(inner) > 1 and inner[0] == "or":
                # All disjuncts must be false; recursively negate each
                for sub in inner[1:]:
                    sub_targets = extract_targets(["not", sub])
                    out.update(sub_targets)
                return out
            if isinstance(inner, list) and len(inner) > 1 and inner[0] == "and":
                # At least one conjunct must be false; pick first that extracts
                for sub in inner[1:]:
                    sub_targets = extract_targets(["not", sub])
                    if sub_targets:
                        out.update(sub_targets)
                        return out
        return out

    # (= var literal) → var = literal
    if op == "=" and len(expr) == 3:
        v = expr[1] if isinstance(expr[1], str) else None
        lit = expr[2]
        if v and not isinstance(lit, list):
            out[v] = _atom_value(lit)
            return out
        # (= literal var) symmetric
        if isinstance(expr[2], str) and not isinstance(expr[1], list):
            out[expr[2]] = _atom_value(expr[1])
            return out

    # Numeric comparisons: pick a value satisfying the bound.
    if op in (">=", "<=", ">", "<"):
        if len(expr) == 3:
            a, b = expr[1], expr[2]
            # (>= var N) or (<= var N)
            if isinstance(a, str) and not isinstance(b, list):
                nval = _atom_value(b)
                if isinstance(nval, (int, float)):
                    out[a] = _pick_numeric(op, nval, a_is_left=True)
                    return out
            # (>= N var) → symmetric
            if isinstance(b, str) and not isinstance(a, list):
                nval = _atom_value(a)
                if isinstance(nval, (int, float)):
                    # (>= N var) ≡ (<= var N)
                    flipped_op = {">=": "<=", "<=": ">=", ">": "<", "<": ">"}[op]
                    out[b] = _pick_numeric(flipped_op, nval, a_is_left=True)
                    return out

    # (and ...) → union of targets
    if op == "and":
        for sub in expr[1:]:
            sub_targets = extract_targets(sub)
            out.update(sub_targets)
        return out

    # (or ...) → pick leftmost branch that yields non-empty targets
    if op == "or":
        for sub in expr[1:]:
            sub_targets = extract_targets(sub)
            if sub_targets:
                out.update(sub_targets)
                return out

    # (=> ant cons) → if antecedent is True, pick consequent targets
    if op == "=>":
        if len(expr) == 3:
            # Assume antecedent true; extract consequent
            sub_targets = extract_targets(expr[2])
            out.update(sub_targets)
            return out

    # Otherwise: unknown op; return empty
    return out


def _pick_numeric(op: str, n: float, a_is_left: bool = True) -> float:
    """Pick a concrete numeric value satisfying the comparison."""
    # `a OP n` where a is the variable
    if op == ">=":
        return n  # var >= n → pick n
    if op == "<=":
        return n  # var <= n → pick n
    if op == ">":
        return n + 1
    if op == "<":
        return n - 1
    return n


# ─── Convenience helper ───────────────────────────────────────────────────

def extract_targets_from_core(smt_source: str, unsat_labels: List[str]) -> Dict[str, Any]:
    """Top-level: given SMT source and list of unsat-core labels, return the
    minimum {variable: target_value} map that would satisfy the core."""
    named = extract_named_assertions(smt_source)
    targets: Dict[str, Any] = {}
    for lbl in unsat_labels:
        body = named.get(lbl)
        if body is None:
            continue
        these = extract_targets(body)
        targets.update(these)
    return targets
