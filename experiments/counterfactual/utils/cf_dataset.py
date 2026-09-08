"""Counterfactual experiment dataset helpers.

Identifies the diagonal test-set (pairs where target system cohort agrees on
INELIGIBLE) and loads charts + trial criteria + per-system rationales/atoms.

Active 4-system cohort:
  - AEGIS v9 + arbiter
  - V5 (single_shot_llm)
  - TrialGPT (corrected aggregation)
  - Stanford (shahlab)
"""
from __future__ import annotations
import json, pathlib, re
from collections import defaultdict
from typing import Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[3]


def load_charts() -> Dict[str, str]:
    out = {}
    for line in open(ROOT/'dataset/clinical_trial/sigir/queries.jsonl'):
        try: o = json.loads(line); out[o['_id']] = o.get('text','')
        except: pass
    return out


def load_trial_text() -> Dict[str, str]:
    """Trial id (no cohort suffix) → corpus text."""
    out = {}
    for line in open(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl'):
        try: o = json.loads(line); out[o.get('_id') or o.get('id')] = o.get('text','')
        except: pass
    return out


def load_aegis_v9_arbiter() -> Dict[str, dict]:
    """Returns {pair: {eligibility, rationale, deciding_variant, arbiter_applied, ...}}."""
    out = {}
    for line in open(ROOT/'matchers/systems/aegis/rationales_v9_arbiter.jsonl'):
        try: r = json.loads(line); out[r['pair']] = r
        except: pass
    return out


def load_v5() -> Dict[str, dict]:
    out = {}
    for line in open(ROOT/'backup/overnight/lm_only_V5_TWO_STEP.jsonl'):
        try: r = json.loads(line); out[r['pair']] = r
        except: pass
    return out


def load_v5_blockers() -> Dict[str, dict]:
    """V5 variant with structured blocker output."""
    out = {}
    fp = ROOT/'matchers/systems/single_shot_llm/v5_blockers.jsonl'
    if not fp.exists(): return out
    for line in fp.open():
        try: r = json.loads(line); out[r['pair']] = r
        except: pass
    return out


def load_tg() -> Dict[str, dict]:
    """TrialGPT with corrected aggregation."""
    out = {}
    for line in open(ROOT/'matchers/systems/trialgpt/rationales.jsonl'):
        try: r = json.loads(line); out[r['pair']] = r
        except: pass
    return out


def load_shahlab() -> Dict[str, dict]:
    out = {}
    for line in open(ROOT/'matchers/systems/shahlab/rationales.jsonl'):
        try: r = json.loads(line); out[r['pair']] = r
        except: pass
    return out


def load_v9_mine() -> dict:
    """Per-pair {variant_key: (full_tid, full_json)} from the v9+arbiter mine."""
    mine = defaultdict(dict)
    src = ROOT/'experiments/53_v2_full/cmsrc_out_REMINE_v9_full'
    for pdir in src.iterdir():
        if not pdir.is_dir() or pdir.name.startswith('_'): continue
        for fp in pdir.glob('*__full.json'):
            try: o = json.loads(fp.read_text())
            except: continue
            tid = fp.name.replace('__full.json','')
            m = re.match(r'^(NCT\d+)([a-z]?)$', tid)
            if not m: continue
            mine[f'{pdir.name}__{m.group(1)}'][m.group(2) or '_'] = (m.group(0), o)
    return mine


def load_arbiter_cache() -> Dict[tuple, dict]:
    """Returns {(pair, full_tid, side): {overrides, ...}}."""
    out = {}
    for fp in (ROOT/'experiments/53_v2_full/arbiter_cache').glob('*.json'):
        stem = fp.stem; parts = stem.rsplit('__', 2)
        if len(parts) != 3 or parts[2] not in ('inclusion','exclusion'): continue
        try: out[(parts[0], parts[1], parts[2])] = json.loads(fp.read_text())
        except: pass
    return out


def diagonal_ineligible(systems: List[str]) -> List[str]:
    """Return pairs where ALL specified systems voted ineligible.
    systems ⊆ {'aegis','v5','tg','shah'}."""
    loaders = {
        'aegis': load_aegis_v9_arbiter, 'v5': load_v5,
        'v5_blockers': load_v5_blockers,
        'tg': load_tg, 'shah': load_shahlab,
    }
    preds = {s: loaders[s]() for s in systems}
    common = set.intersection(*[set(preds[s]) for s in systems])
    out = []
    for p in sorted(common):
        if all((preds[s][p].get('eligibility') == 'ineligible') for s in systems):
            out.append(p)
    return out


def trial_id_parent(nct_with_suffix: str) -> str:
    return re.sub(r'(?<=NCT\d{8})[a-z]+$', '', nct_with_suffix)
