"""MaxSat-based minimum flip-set extraction for AEGIS.

Given an SMT program + LLM-asserted atom values, find the MINIMUM-cardinality
set of atom assertions to drop/flip such that the program becomes SAT. This
is exactly the v6-paper "minimum flip target" computation.

Implementation: Z3 Optimize with soft constraints (each LLM-asserted atom
contributes a weight-1 soft constraint; the optimizer minimizes the number of
violated soft constraints to satisfy the hard program).
"""
from __future__ import annotations
import re, sys, pathlib
from typing import Dict, List, Tuple
import z3

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'experiments/accuracy/scripts'))
from repro_headline import quote_smt, smt_number, thresh_binding_lines


def maxsat_blockers(prog_lines: list, av: dict) -> Tuple[str, List[str], List[str]]:
    """Returns (status, dropped_atoms, kept_atoms).

    status:
      'sat'              — program with all LLM atoms is SAT (no flips needed).
      'sat_after_drops'  — program initially UNSAT; dropping listed atoms makes SAT.
      'unsat_irreducible'— program-level contradiction (UNSAT even with no LLM atoms).
      'unknown'          — solver error.
    """
    if not prog_lines: return 'sat', [], [], {}
    declared = set(); thresh_bound = set(); extra_decls = []
    soft = []  # list of (atom, smt_expr_string) soft constraints

    def bind_thresh(atom):
        if atom in thresh_bound or '__THRESH__' not in atom: return
        lines = thresh_binding_lines(atom, prog_lines=prog_lines)
        if lines: extra_decls.extend(lines); thresh_bound.add(atom)
    def ensure_declared(atom):
        if '__THRESH__' in atom or atom in declared: return
        esc = re.escape(atom)
        # Match either bare `name` or pipe-quoted `|name|` declarations
        pat = rf'\(declare-(const|fun)\s+\|?{esc}\|?\s+'
        if not any(re.search(pat, ln) for ln in prog_lines):
            extra_decls.append(f'(declare-const {quote_smt(atom)} Bool)')
        declared.add(atom)
    for atom, m in (av or {}).items():
        if not isinstance(m, dict): continue
        v = m.get('value')
        if v is None: continue
        qn = quote_smt(atom); bind_thresh(atom); ensure_declared(atom)
        if isinstance(v, bool): expr = f'(= {qn} {"true" if v else "false"})'
        elif isinstance(v, (int,float)): expr = f'(= {qn} {smt_number(v)})'
        elif isinstance(v, str):
            sl = v.strip().lower()
            if sl in ('true','false'): expr = f'(= {qn} {sl})'
            else: continue
        else: continue
        soft.append((atom, expr))
    # Pre-check 1: program declarations alone (no LLM atoms) — if UNSAT, irreducible.
    s_pre = z3.Solver()
    try:
        s_pre.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls))
        if s_pre.check() != z3.sat: return 'unsat_irreducible', [], [], {}
    except Exception:
        return 'unknown', [], [], {}
    # Pre-check 2: program + ALL LLM atoms hard-asserted — if SAT, no drops needed.
    s_full = z3.Solver()
    try:
        s_full.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls)
                            + '\n' + '\n'.join(f'(assert {expr})' for _, expr in soft))
        if s_full.check() == z3.sat: return 'sat', [], [a for a, _ in soft], {}
    except Exception:
        return 'unknown', [], [], {}
    # Now build Optimize with soft constraints
    parts = list(prog_lines) + extra_decls
    for i, (atom, expr) in enumerate(soft):
        parts.append(f'(assert-soft {expr} :id grp{i} :weight 1)')
    parts.append('(check-sat)')
    parts.append('(get-model)')
    o = z3.Optimize()
    try:
        o.from_string('\n'.join(parts))
        r = o.check()
        if r != z3.sat: return 'unknown', [], [], {}
        model = o.model()
    except Exception:
        return 'unknown', [], [], {}
    # For each soft, evaluate the expression in the model — if False, it's dropped
    dropped = []; kept = []
    for atom, expr in soft:
        # Evaluate via Z3: build the expr as a check
        try:
            # Use a fresh solver to evaluate the expression in the model
            f = z3.Solver()
            f.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls)
                          + f'\n(assert {expr})')
            # Add model as constraints
            for d in model.decls():
                val = model.get_interp(d)
                if val is None: continue
                f.add(d() == val)
            if f.check() == z3.sat: kept.append(atom)
            else: dropped.append(atom)
        except Exception:
            kept.append(atom)
    # Build a satisfying-value table for the dropped atoms by reading the
    # Z3 model. This lets numeric atoms get a CONCRETE target value (e.g.,
    # "82 → 45") instead of falling back to None / silence.
    sat_values: dict = {}
    try:
        decls_by_name = {str(d): d for d in model.decls()}
        for atom in dropped:
            # NB: Z3 FuncDeclRef cannot be cast to bool, so don't use `or`
            d = decls_by_name.get(atom)
            if d is None: d = decls_by_name.get(quote_smt(atom).strip('|'))
            if d is None: continue
            val = model.get_interp(d)
            if val is None: continue
            if z3.is_bool(d()):
                sat_values[atom] = z3.is_true(val)
            elif z3.is_int(d()):
                try: sat_values[atom] = val.as_long()
                except Exception: sat_values[atom] = str(val)
            elif z3.is_real(d()):
                # z3 Real → fraction → float
                try:
                    n, d_ = val.numerator_as_long(), val.denominator_as_long()
                    sat_values[atom] = n / d_ if d_ else float(str(val))
                except Exception:
                    try: sat_values[atom] = float(str(val))
                    except Exception: sat_values[atom] = str(val)
            else:
                sat_values[atom] = str(val)
    except Exception:
        pass
    return 'sat_after_drops', dropped, kept, sat_values


def _merge_programs(inc_prog_lines: list, exc_prog_lines: list) -> list:
    """Concatenate two SMT programs, deduplicating declarations AND namespacing
    `:named ID` annotations per side to avoid "named expression already defined"
    errors when the two programs use the same REQ_* labels.

    Why: inc and exc programs typically declare the SAME patient atom variables
    (e.g. patient_age_value_recorded_now_in_days) AND both use `(assert (! ...
    :named REQn_COMPONENTm_OTHER_REQUIREMENTS))` style labels. Both must be
    deduplicated/namespaced or Z3 rejects the joint program.
    """
    decl_re = re.compile(r'\(declare-(?:const|fun)\s+\|?([^|\s)]+)\|?\s+')
    named_re = re.compile(r':named\s+([A-Za-z_][A-Za-z_0-9]*)')
    seen_decls = set()
    out = []
    def _rename_named(line, prefix):
        return named_re.sub(lambda m: f':named {prefix}{m.group(1)}', line)
    for line in inc_prog_lines:
        m = decl_re.match(line.strip())
        if m:
            name = m.group(1)
            if name in seen_decls: continue
            seen_decls.add(name)
        out.append(_rename_named(line, 'I_'))
    for line in exc_prog_lines:
        m = decl_re.match(line.strip())
        if m:
            name = m.group(1)
            if name in seen_decls: continue
            seen_decls.add(name)
        out.append(_rename_named(line, 'E_'))
    return out


def maxsat_blockers_joint(inc_prog_lines: list, exc_prog_lines: list,
                          inc_av: dict, exc_av: dict) -> dict:
    """Joint MaxSat: solve inc AND exc simultaneously so that the flipped atom
    values satisfy both sides at once. This prevents the exclusion-blowback
    failure mode where fixing inclusion blockers accidentally triggers
    exclusion criteria.

    Returns dict with keys:
      status: 'sat' | 'sat_after_drops' | 'unsat_irreducible' | 'unknown'
      inc_dropped, exc_dropped: lists of atoms dropped per side
      inc_kept, exc_kept: lists of atoms kept (preserve set) per side
      sat_values: {atom: target_value} from the joint model
      co_present_atoms: list of atoms that appeared in both inc_av and exc_av
      joint_verified: bool — whether plugging targets back into each side
                       (independently) yields SAT for both
    """
    out = {'status': 'unknown',
           'inc_dropped': [], 'exc_dropped': [],
           'inc_kept': [], 'exc_kept': [],
           'sat_values': {}, 'co_present_atoms': [],
           'joint_verified': False}
    if not inc_prog_lines and not exc_prog_lines:
        out['status'] = 'sat'; return out

    # Merge programs (dedup declarations)
    prog_lines = _merge_programs(inc_prog_lines or [], exc_prog_lines or [])

    # Detect overlap
    inc_atoms = set((inc_av or {}).keys())
    exc_atoms = set((exc_av or {}).keys())
    co_present = sorted(inc_atoms & exc_atoms)
    out['co_present_atoms'] = co_present

    # Merge av tables (prefer non-None values; both sides should agree on values
    # for co-present atoms, but if they disagree we pick inc's). Track origin.
    av = {}
    origin = {}  # atom -> set({'inc','exc'})
    for a, m in (inc_av or {}).items():
        if not isinstance(m, dict): continue
        av[a] = dict(m); origin[a] = {'inc'}
    for a, m in (exc_av or {}).items():
        if not isinstance(m, dict): continue
        if a in av:
            origin[a].add('exc')
            if av[a].get('value') is None and m.get('value') is not None:
                av[a]['value'] = m['value']
        else:
            av[a] = dict(m); origin[a] = {'exc'}

    # Build soft constraints (one per atom, even if co-present)
    declared = set(); thresh_bound = set(); extra_decls = []
    soft = []
    def bind_thresh(atom):
        if atom in thresh_bound or '__THRESH__' not in atom: return
        lines = thresh_binding_lines(atom, prog_lines=prog_lines)
        if lines: extra_decls.extend(lines); thresh_bound.add(atom)
    def ensure_declared(atom):
        if '__THRESH__' in atom or atom in declared: return
        esc = re.escape(atom)
        pat = rf'\(declare-(const|fun)\s+\|?{esc}\|?\s+'
        if not any(re.search(pat, ln) for ln in prog_lines):
            extra_decls.append(f'(declare-const {quote_smt(atom)} Bool)')
        declared.add(atom)
    for atom, m in av.items():
        v = m.get('value')
        if v is None: continue
        qn = quote_smt(atom); bind_thresh(atom); ensure_declared(atom)
        if isinstance(v, bool):       expr = f'(= {qn} {"true" if v else "false"})'
        elif isinstance(v,(int,float)): expr = f'(= {qn} {smt_number(v)})'
        elif isinstance(v, str):
            sl = v.strip().lower()
            if sl in ('true','false'): expr = f'(= {qn} {sl})'
            else: continue
        else: continue
        soft.append((atom, expr))

    # Pre-check: joint program (no LLM atoms) SAT?
    s_pre = z3.Solver()
    try:
        s_pre.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls))
        if s_pre.check() != z3.sat:
            out['status'] = 'unsat_irreducible'; return out
    except Exception as e:
        out['error'] = f'pre-check parse error: {str(e)[:200]}'
        return out  # status='unknown'

    # Pre-check: full joint + all atoms hard-asserted SAT?
    s_full = z3.Solver()
    try:
        s_full.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls)
                            + '\n' + '\n'.join(f'(assert {expr})' for _, expr in soft))
        if s_full.check() == z3.sat:
            out['status'] = 'sat'
            out['inc_kept'] = [a for a in inc_atoms if a in {x for x,_ in soft}]
            out['exc_kept'] = [a for a in exc_atoms if a in {x for x,_ in soft}]
            return out
    except Exception as e:
        out['error'] = f'full-check parse error: {str(e)[:200]}'
        return out

    # Optimize: minimize number of dropped softs
    parts = list(prog_lines) + extra_decls
    for i, (atom, expr) in enumerate(soft):
        parts.append(f'(assert-soft {expr} :id grp{i} :weight 1)')
    parts.append('(check-sat)'); parts.append('(get-model)')
    o = z3.Optimize()
    try:
        o.from_string('\n'.join(parts))
        if o.check() != z3.sat: return out
        model = o.model()
    except Exception:
        return out

    # Per-atom drop/keep decision
    dropped = []; kept = []
    for atom, expr in soft:
        try:
            f = z3.Solver()
            f.from_string('\n'.join(prog_lines) + '\n' + '\n'.join(extra_decls)
                          + f'\n(assert {expr})')
            for d in model.decls():
                val = model.get_interp(d)
                if val is None: continue
                f.add(d() == val)
            if f.check() == z3.sat: kept.append(atom)
            else: dropped.append(atom)
        except Exception:
            kept.append(atom)

    # Build sat_values from the joint model (one value per atom, consistent
    # across inc and exc).
    sat_values = {}
    decls_by_name = {str(decl): decl for decl in model.decls()}
    for atom in dropped:
        decl = decls_by_name.get(atom)
        if decl is None:
            decl = decls_by_name.get(quote_smt(atom).strip('|'))
        if decl is None:
            continue
        try:
            val = model.get_interp(decl)
            if val is None: continue
            expr = decl()
            if z3.is_bool(expr):
                sat_values[atom] = z3.is_true(val)
            elif z3.is_int(expr):
                try: sat_values[atom] = val.as_long()
                except Exception: sat_values[atom] = str(val)
            elif z3.is_real(expr):
                try:
                    n = val.numerator_as_long(); d_ = val.denominator_as_long()
                    sat_values[atom] = n / d_ if d_ else float(str(val))
                except Exception:
                    try: sat_values[atom] = float(str(val))
                    except Exception: sat_values[atom] = str(val)
            else:
                sat_values[atom] = str(val)
        except Exception as e:
            out['sat_value_errors'] = out.get('sat_value_errors', []) + [f'{atom}: {str(e)[:80]}']

    # Per-side decomposition: a dropped atom belongs to a side if it appears in
    # that side's av. (Co-present dropped atoms appear in BOTH sides' dropped.)
    out['inc_dropped'] = [a for a in dropped if a in inc_atoms]
    out['exc_dropped'] = [a for a in dropped if a in exc_atoms]
    out['inc_kept']    = [a for a in kept    if a in inc_atoms]
    out['exc_kept']    = [a for a in kept    if a in exc_atoms]
    out['sat_values']  = sat_values
    out['status']      = 'sat_after_drops'

    # Verification: plug sat_values back into each side independently
    def _verify_side(side_lines, side_av):
        """Returns True iff side is SAT with kept LLM atoms hard-asserted at
        their current values AND dropped atoms hard-asserted at target_value."""
        if not side_lines: return True
        side_decl = set(); side_extra = []
        def _dec(atom):
            if '__THRESH__' in atom or atom in side_decl: return
            esc = re.escape(atom)
            if not any(re.search(rf'\(declare-(const|fun)\s+\|?{esc}\|?\s+', ln) for ln in side_lines):
                side_extra.append(f'(declare-const {quote_smt(atom)} Bool)')
            side_decl.add(atom)
        asserts = []
        for atom, m in (side_av or {}).items():
            if not isinstance(m, dict): continue
            qn = quote_smt(atom); _dec(atom)
            tv = sat_values.get(atom) if atom in dropped else m.get('value')
            if tv is None: continue
            if isinstance(tv, bool):       asserts.append(f'(assert (= {qn} {"true" if tv else "false"}))')
            elif isinstance(tv,(int,float)): asserts.append(f'(assert (= {qn} {smt_number(tv)}))')
            elif isinstance(tv, str):
                sl = tv.strip().lower()
                if sl in ('true','false'): asserts.append(f'(assert (= {qn} {sl}))')
        try:
            s = z3.Solver()
            s.from_string('\n'.join(side_lines) + '\n' + '\n'.join(side_extra) + '\n' + '\n'.join(asserts))
            return s.check() == z3.sat
        except Exception:
            return False
    out['joint_verified'] = (_verify_side(inc_prog_lines, inc_av) and
                              _verify_side(exc_prog_lines, exc_av))
    return out


# Convenience: produce blockers in the same shape as cf_blockers.aegis_blockers
def maxsat_blocker_list(prog_lines, av) -> dict:
    """Returns {'status', 'blockers': [{atom, current_value, target_value, evidence, assessment, target_source}, ...], 'kept_count'}.

    target_value semantics:
      - bool atom: flipped polarity (always derivable)
      - numeric / string atom: Z3-satisfying value from the MaxSat model when
        available (target_source = 'z3_model'). Falls back to None only if
        Z3 left the atom unconstrained in the satisfying assignment, in which
        case the rewriter should make the chart silent on this atom under
        the silence-handling policy.
    """
    status, dropped, kept, sat_values = maxsat_blockers(prog_lines, av)
    blockers = []
    for atom in dropped:
        m = (av or {}).get(atom) or {}
        v = m.get('value')
        target_source = 'derived'
        if isinstance(v, bool):
            target = not v
        elif atom in sat_values:
            target = sat_values[atom]
            target_source = 'z3_model'
        else:
            target = None
            target_source = 'silence_fallback'
        blockers.append({
            'atom': atom, 'current_value': v, 'target_value': target,
            'target_source': target_source,
            'evidence': m.get('evidence',''), 'assessment': m.get('assessment',''),
        })
    return {'status': status, 'blockers': blockers, 'kept_count': len(kept)}
