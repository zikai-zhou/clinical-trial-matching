"""Per-system blocker extractors for counterfactual experiments.

Each system has a different rationale format; this module produces a uniform
"blocker list" that the CF generator can use to rewrite the chart.

For AEGIS, blockers are the atom-value pairs in the post-arbiter unsat core.
For V5/TG/Stanford, blockers are NL strings (parsed from rationale) — the CF
generator handles them as text.
"""
from __future__ import annotations
import json, pathlib, re, sys
from typing import List, Dict, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'experiments/accuracy/scripts'))
import z3
from repro_headline import quote_smt, smt_number, thresh_binding_lines


# ---------- AEGIS ----------

def _expand_with_parent_stems(blockers: list, av: dict) -> list:
    """If a blocker is `X@@qualifier` with target=FALSE, also include parent
    stem `X` with target=FALSE (qual=>stem aux). If target=TRUE, include parent
    stem with target=TRUE. This addresses the qualifier-stem auxiliary that
    keeps exclusions firing when only the qualifier is flipped at chart level."""
    seen = {b['atom']: b for b in blockers}
    out = list(blockers)
    for b in blockers:
        atom = b['atom']
        if '@@' not in atom: continue
        stem = atom.split('@@', 1)[0]
        if stem in seen: continue
        m = av.get(stem) or {}
        out.append({
            'atom': stem,
            'current_value': m.get('value'),
            'target_value': b.get('target_value'),  # propagate the same target
            'evidence': m.get('evidence', '') or f'(parent stem of {atom})',
            'assessment': m.get('assessment', '') or f'(propagated from {atom})',
        })
        seen[stem] = b
    return out


def aegis_blockers_joint(pair: str, full_tid: str, full_json: dict, arbiter_cache: dict) -> dict:
    """Joint-MaxSat variant of aegis_blockers.

    Solves the inclusion and exclusion programs JOINTLY so that target values
    chosen for shared atoms (e.g., patient age) satisfy both sides at once.
    This prevents the exclusion-blowback failure mode that drives 64% of
    AEGIS's self-faithfulness errors.

    Same output shape as `aegis_blockers` plus:
      - 'co_present_atoms': atoms in both inc_av and exc_av
      - 'joint_verified': bool — did re-plugging targets verify both sides?
      - 'joint_status': overall maxsat status
    """
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from cf_maxsat import maxsat_blockers_joint
    out = {'inc_blockers': [], 'exc_blockers': [],
           'inc_preserve': [], 'exc_preserve': [],
           'inc_all_atoms': [], 'exc_all_atoms': [],
           'inc_status': 'unknown', 'exc_status': 'unknown',
           'co_present_atoms': [], 'joint_verified': False, 'joint_status': 'unknown'}

    def _get_side(side):
        side_d = full_json.get(side) or {}
        side_raw = side_d.get('raw') or {}
        prog_lines = side_raw.get('smt_program_lines') or []
        av_orig = side_raw.get('patient_var_values_rich') or {}
        cache_entry = arbiter_cache.get((pair, full_tid, side), {})
        overrides = cache_entry.get('overrides') or []
        av = {a: dict(m) if isinstance(m, dict) else m for a, m in av_orig.items()}
        for o in overrides:
            a = o.get('atom'); newv = o.get('new_value')
            if a in av and isinstance(av[a], dict): av[a]['value'] = newv
        return prog_lines, av

    inc_lines, inc_av = _get_side('inclusion')
    exc_lines, exc_av = _get_side('exclusion')

    res = maxsat_blockers_joint(inc_lines, exc_lines, inc_av, exc_av)
    out['joint_status'] = res['status']
    out['co_present_atoms'] = res['co_present_atoms']
    out['joint_verified'] = res['joint_verified']
    out['inc_status'] = ('SAT' if res['status'] == 'sat'
                         else 'UNSAT' if res['status'] in ('sat_after_drops','unsat_irreducible')
                         else 'unknown')
    out['exc_status'] = out['inc_status']

    sat_values = res['sat_values']
    inc_dropped = res['inc_dropped']
    exc_dropped = res['exc_dropped']
    inc_kept    = res['inc_kept']
    exc_kept    = res['exc_kept']
    co_present  = set(res['co_present_atoms'])

    def _build_blockers(atoms, av):
        out_list = []
        for atom in atoms:
            m = av.get(atom) or {}
            v = m.get('value')
            target_source = 'derived'
            if isinstance(v, bool):
                target = not v
            elif atom in sat_values:
                target = sat_values[atom]; target_source = 'z3_model'
            else:
                target = None; target_source = 'silence_fallback'
            out_list.append({'atom': atom, 'current_value': v, 'target_value': target,
                             'target_source': target_source,
                             'co_present': atom in co_present,
                             'evidence': m.get('evidence',''),
                             'assessment': m.get('assessment','')})
        return out_list

    def _build_preserve(atoms, av):
        out_list = []
        for atom in atoms:
            m = av.get(atom) or {}
            v = m.get('value')
            if v is None: continue
            out_list.append({'atom': atom, 'current_value': v,
                             'evidence': m.get('evidence',''),
                             'assessment': m.get('assessment','')})
        return out_list

    def _build_full(av, dropped_set):
        out_list = []
        for atom, m in av.items():
            if not isinstance(m, dict): continue
            if atom in dropped_set: continue
            out_list.append({'atom': atom, 'current_value': m.get('value'),
                              'evidence': m.get('evidence',''),
                              'assessment': m.get('assessment','')})
        return out_list

    out['inc_blockers'] = _expand_with_parent_stems(_build_blockers(inc_dropped, inc_av), inc_av)
    out['exc_blockers'] = _expand_with_parent_stems(_build_blockers(exc_dropped, exc_av), exc_av)
    out['inc_preserve'] = _build_preserve(inc_kept, inc_av)
    out['exc_preserve'] = _build_preserve(exc_kept, exc_av)
    out['inc_all_atoms'] = _build_full(inc_av, set(inc_dropped))
    out['exc_all_atoms'] = _build_full(exc_av, set(exc_dropped))
    return out


def aegis_blockers(pair: str, full_tid: str, full_json: dict, arbiter_cache: dict,
                   max_iters: int = 8, use_maxsat: bool = True) -> dict:
    """Return AEGIS's minimum-flip blocker set via MaxSat (Z3 Optimize with
    soft constraints). When `use_maxsat=False`, falls back to iterative-core.
    Always applies parent-stem expansion for qualified atoms.

    Output keys (per side):
      <side>_blockers: minimum-cardinality flip set
      <side>_preserve: atoms whose values are SAT-compatible (passed to CF
                       generator as the "do not collateral-damage" hint)"""
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from cf_maxsat import maxsat_blocker_list
    out = {'inc_blockers': [], 'exc_blockers': [],
           'inc_preserve': [], 'exc_preserve': [],
           'inc_all_atoms': [], 'exc_all_atoms': [],
           'inc_status': 'unknown', 'exc_status': 'unknown'}
    for side in ('inclusion', 'exclusion'):
        side_d = full_json.get(side) or {}
        side_raw = side_d.get('raw') or {}
        prog_lines = side_raw.get('smt_program_lines') or []
        av_orig = side_raw.get('patient_var_values_rich') or {}
        cache_entry = arbiter_cache.get((pair, full_tid, side), {})
        overrides = cache_entry.get('overrides') or []
        av = {a: dict(m) if isinstance(m, dict) else m for a, m in av_orig.items()}
        for o in overrides:
            a = o.get('atom'); newv = o.get('new_value')
            if a in av and isinstance(av[a], dict): av[a]['value'] = newv
        side_key = 'inc' if side == 'inclusion' else 'exc'
        if use_maxsat:
            from cf_maxsat import maxsat_blockers as _maxsat_pair
            status, dropped, kept, sat_values = _maxsat_pair(prog_lines, av)
            out[f'{side_key}_status'] = ('SAT' if status == 'sat'
                                          else 'UNSAT' if status in ('sat_after_drops','unsat_irreducible')
                                          else 'unknown')
            if dropped:
                blockers = []
                for atom in dropped:
                    m = av.get(atom) or {}
                    v = m.get('value')
                    target_source = 'derived'
                    if isinstance(v, bool):
                        target = not v
                    elif atom in sat_values:
                        target = sat_values[atom]; target_source = 'z3_model'
                    else:
                        target = None; target_source = 'silence_fallback'
                    blockers.append({'atom': atom, 'current_value': v, 'target_value': target,
                                     'target_source': target_source,
                                     'evidence': m.get('evidence',''), 'assessment': m.get('assessment','')})
                blockers = _expand_with_parent_stems(blockers, av)
                out[f'{side_key}_blockers'] = blockers
            if kept:
                preserve = []
                for atom in kept:
                    m = av.get(atom) or {}
                    v = m.get('value')
                    if v is None: continue
                    preserve.append({'atom': atom, 'current_value': v,
                                     'evidence': m.get('evidence',''), 'assessment': m.get('assessment','')})
                out[f'{side_key}_preserve'] = preserve
            # Always populate full-atom list (including silents) — the rewriter must
            # not introduce chart content that would change any of these on re-mine.
            full = []
            dropped_set = set(dropped)
            for atom, m in av.items():
                if not isinstance(m, dict): continue
                if atom in dropped_set: continue  # exclude target atoms
                full.append({'atom': atom,
                              'current_value': m.get('value'),
                              'evidence': m.get('evidence',''),
                              'assessment': m.get('assessment','')})
            out[f'{side_key}_all_atoms'] = full
        else:
            # Fallback: iterative-core extraction
            flip_set = {}
            cur_av = {a: dict(m) if isinstance(m, dict) else m for a, m in av.items()}
            last_status = None
            for it in range(max_iters):
                sat, core_atoms = _solve_with_core(prog_lines, cur_av)
                last_status = sat
                if sat is True or sat is None: break
                new_atoms = [a for a in core_atoms if a not in flip_set]
                if not new_atoms: break
                for atom in new_atoms:
                    m = av.get(atom) or {}
                    v = m.get('value')
                    target = (not v) if isinstance(v, bool) else None
                    if target is None: continue
                    flip_set[atom] = target
                    if isinstance(cur_av.get(atom), dict):
                        cur_av[atom] = dict(cur_av[atom]); cur_av[atom]['value'] = target
                    else:
                        cur_av[atom] = {'value': target}
            out[f'{side_key}_status'] = 'SAT' if last_status is True else ('UNSAT' if last_status is False else 'unknown')
            if flip_set:
                blockers = []
                for atom, tv in flip_set.items():
                    m = av.get(atom) or {}
                    blockers.append({
                        'atom': atom, 'current_value': m.get('value'), 'target_value': tv,
                        'evidence': m.get('evidence', ''), 'assessment': m.get('assessment', ''),
                    })
                blockers = _expand_with_parent_stems(blockers, av)
                out[f'{side_key}_blockers'] = blockers
    return out


def _solve_with_core(prog_lines, av):
    """Solve with named asserts to get unsat core. Returns (sat:bool|None, core:list[str])."""
    if not prog_lines: return None, []
    asserts = []
    declared = set(); thresh_bound = set(); extra_decls = []
    name_map = {}
    def bind_thresh(atom):
        if atom in thresh_bound or '__THRESH__' not in atom: return
        lines = thresh_binding_lines(atom, prog_lines=prog_lines)
        if lines: extra_decls.extend(lines); thresh_bound.add(atom)
    def ensure_declared(atom):
        if '__THRESH__' in atom or atom in declared: return
        if not any(re.search(rf'\(declare-(const|fun)\s+{re.escape(atom)}\s+', ln) for ln in prog_lines):
            extra_decls.append(f'(declare-const {quote_smt(atom)} Bool)')
        declared.add(atom)
    for atom, m in (av or {}).items():
        if not isinstance(m, dict): continue
        v = m.get('value')
        if v is None: continue
        qn = quote_smt(atom); bind_thresh(atom); ensure_declared(atom)
        if isinstance(v, bool):
            idx = len(name_map); name_map[f'pa_{idx}'] = atom
            asserts.append(f'(assert (! (= {qn} {"true" if v else "false"}) :named pa_{idx}))')
        elif isinstance(v, (int,float)):
            idx = len(name_map); name_map[f'pa_{idx}'] = atom
            asserts.append(f'(assert (! (= {qn} {smt_number(v)}) :named pa_{idx}))')
        elif isinstance(v, str):
            sl = v.strip().lower()
            if sl in ('true','false'):
                idx = len(name_map); name_map[f'pa_{idx}'] = atom
                asserts.append(f'(assert (! (= {qn} {sl}) :named pa_{idx}))')
    parts = ['(set-option :produce-unsat-cores true)'] + list(prog_lines) + extra_decls + asserts
    try:
        s = z3.Solver()
        s.from_string('\n'.join(parts))
        r = s.check()
        if r == z3.sat: return True, []
        if r != z3.unsat: return None, []
        core_names = [str(c) for c in s.unsat_core()]
        return False, [name_map[c] for c in core_names if c in name_map][:30]
    except Exception:
        return None, []


# ---------- V5 / Stanford ----------

def v5_blockers(rec: dict) -> dict:
    """Return {'rationale_text': ..., 'eligibility': ...} — V5 has free-text only."""
    return {
        'rationale_text': rec.get('explanation', '') or rec.get('rationale','') or '',
        'eligibility': rec.get('eligibility','unknown'),
    }


def v5_blockers_structured(rec: dict) -> dict:
    """V5_TWO_STEP_BLOCKERS variant — explicit blockers AND supports lists."""
    blockers = rec.get('blockers') or []
    supports = rec.get('supports') or []
    blocker_lines = [f"[{b.get('side','?')}] {b.get('fact','')}" + (f" — {b.get('reasoning','')}" if b.get('reasoning') else '') for b in blockers]
    support_lines = [f"[{s.get('side','?')}] {s.get('fact','')}" + (f" — {s.get('reasoning','')}" if s.get('reasoning') else '') for s in supports]
    return {
        'rationale_text': rec.get('explanation','') or '',
        'blocker_lines': blocker_lines,
        'support_lines': support_lines,
        'structured_blockers': blockers,
        'structured_supports': supports,
        'eligibility': rec.get('eligibility','unknown'),
    }


def shahlab_blockers(rec: dict) -> dict:
    """Stanford has per-criterion assessments. Extract the 'not met' / 'excluded' rows."""
    # The shahlab rationales.jsonl has a 'rationale' free-text + structured 'global_decision'.
    # Per-criterion data is in verdicts.jsonl 'assessments' field; we use rationale text here.
    rationale = rec.get('rationale','') or ''
    blockers = []
    # Parse "[not met]" or "[excluded]" lines
    for line in rationale.split('\n'):
        ll = line.strip().lower()
        if ll.startswith('- [not met]') or ll.startswith('- [excluded]') or ll.startswith('- [not_met]'):
            blockers.append(line.strip())
    return {
        'rationale_text': rationale, 'blocker_lines': blockers,
        'eligibility': rec.get('eligibility','unknown'),
    }


# ---------- TrialGPT ----------

def tg_blockers(rec: dict) -> dict:
    """TG has per-criterion '[not included]' / '[excluded]' labels in rationale."""
    rationale = rec.get('rationale','') or ''
    blockers = []
    for line in rationale.split('\n'):
        if '[not included]' in line.lower() or '[excluded]' in line.lower():
            if 'not excluded' in line.lower(): continue
            blockers.append(line.strip())
    return {
        'rationale_text': rationale, 'blocker_lines': blockers,
        'eligibility': rec.get('eligibility','unknown'),
    }
