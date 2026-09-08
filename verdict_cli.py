#!/usr/bin/env python3
"""verdict — command-line interface to the VERDICT clinical-trial matcher.

    verdict list                      show available patient--trial pairs
    verdict match  PAIR_ID            decide one pair
    verdict explain PAIR_ID           decide, with the full audit trail
    verdict systems                   list the matcher variants

PAIR_ID is "<patient>__<NCT>", e.g. sigir-20141__NCT00337116.

Pair data is read from $VERDICT_PAIR_DATA (default:
<repo>/experiments/53_v2_full). These are the per-pair artifacts produced by
the stage-1 atom miner; see docs/DATA.md for how to obtain or regenerate them.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys

SYSTEMS = {
    'verdict':  ('smt_lm_evidence_arbiter', 'SMT + LLM auditor with LM-judge evidence (recommended, auditable)'),
    'smt-only': ('smt_raw',                 'atom miner + Z3 only, no review'),
    'atoms':    ('smt_atoms_arbiter',       'SMT + LLM auditor on rejects (atoms only)'),
    'lm-only':  ('lm_only',                 'single LLM call over chart + criteria'),
    'hybrid':   ('hybrid_strict',           'accept-if-either + LM-evidence arbiter (max F1)'),
    'trialgpt': ('trialgpt',                'TrialGPT baseline (Yang et al. 2023)'),
}


def pair_root() -> pathlib.Path:
    root = pathlib.Path(os.environ.get(
        'VERDICT_ROOT', pathlib.Path(__file__).resolve().parent))
    return pathlib.Path(os.environ.get('VERDICT_PAIR_DATA',
                                       root / 'experiments' / '53_v2_full'))


def iter_pairs():
    base = pair_root() / 'cmsrc_out'
    if not base.exists():
        return
    for pdir in sorted(base.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith('_'):
            continue
        for f in sorted(pdir.glob('*__full.json')):
            yield f'{pdir.name}__{f.name.split("__")[0]}'


def require_pairs():
    pairs = list(iter_pairs())
    if not pairs:
        sys.exit(f'no pair data under {pair_root()/"cmsrc_out"}.\n'
                 f'Set $VERDICT_PAIR_DATA to a directory of stage-1 miner '
                 f'outputs, or see docs/DATA.md.')
    return pairs


def run(system: str, pair_id: str):
    from matchers import variants
    fn_name, _ = SYSTEMS[system]
    fn = getattr(variants, fn_name)
    return fn(pair_id)


def cmd_list(a):
    pairs = require_pairs()
    if a.patient:
        pairs = [p for p in pairs if p.startswith(a.patient)]
    for p in pairs[:a.limit]:
        print(p)
    print(f'\n{len(pairs)} pair(s)'
          + (f'; showing {min(a.limit, len(pairs))}' if len(pairs) > a.limit else ''))


def cmd_systems(a):
    print(f'{"name":<10s}{"function":<26s}description')
    print('-' * 92)
    for k, (fn, desc) in SYSTEMS.items():
        print(f'{k:<10s}{fn:<26s}{desc}')


def cmd_match(a):
    d = run(a.system, a.pair_id)
    if a.json:
        print(json.dumps({'pair': a.pair_id, 'system': a.system,
                          'decision': d.decision, 'reasoning': d.reasoning},
                         indent=2))
    else:
        print(f'pair    : {a.pair_id}')
        print(f'system  : {a.system}')
        print(f'decision: {d.decision.upper()}')
        print(f'why     : {d.reasoning}')


def cmd_explain(a):
    d = run(a.system, a.pair_id)
    print(f'pair    : {a.pair_id}')
    print(f'system  : {a.system}')
    print(f'decision: {d.decision.upper()}')
    print(f'why     : {d.reasoning}\n')
    print(f'audit trail ({len(d.audit_trail)} steps)')
    print('-' * 60)
    for i, step in enumerate(d.audit_trail, 1):
        fields = (step if isinstance(step, dict)
                  else getattr(step, '__dict__', None) or {'value': step})
        name = fields.get('stage') or fields.get('step') or f'step {i}'
        print(f'{i}. {name}')
        for k, v in fields.items():
            if k in ('stage', 'step') or v in (None, '', {}, []):
                continue
            s = str(v)
            print(f'     {k}: {s[:200]}{"..." if len(s) > 200 else ""}')


def main():
    ap = argparse.ArgumentParser(
        prog='verdict', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('list', help='list available pairs')
    p.add_argument('--patient', help='filter by patient id prefix')
    p.add_argument('--limit', type=int, default=40)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('systems', help='list matcher variants')
    p.set_defaults(func=cmd_systems)

    for name, fn in (('match', cmd_match), ('explain', cmd_explain)):
        p = sub.add_parser(name, help=f'{name} a patient--trial pair')
        p.add_argument('pair_id')
        p.add_argument('--system', choices=list(SYSTEMS), default='verdict')
        if name == 'match':
            p.add_argument('--json', action='store_true')
        p.set_defaults(func=fn)

    a = ap.parse_args()
    if a.cmd in ('match', 'explain'):
        if a.pair_id not in set(require_pairs()):
            sys.exit(f'unknown pair: {a.pair_id}\ntry: verdict list')
    a.func(a)


if __name__ == '__main__':
    main()
