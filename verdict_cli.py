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

# The registry and the pair index live in the package, so the command and the
# API can never disagree about which systems or pairs exist. Re-exported here
# because callers (and the test suite) import them from this module.
from verdict.data import iter_pairs, pair_root          # noqa: E402,F401


def _systems() -> dict:
    import verdict
    return verdict.systems()


def require_pairs():
    pairs = list(iter_pairs())
    if not pairs:
        sys.exit(f'no pair data under {pair_root()/"cmsrc_out"}.\n'
                 f'Set $VERDICT_PAIR_DATA to a directory of stage-1 miner '
                 f'outputs, or see docs/DATA.md.')
    return pairs


def run(system: str, pair_id: str):
    import verdict
    return verdict.match(pair_id, system=system, strict=False)


def cmd_list(a):
    pairs = require_pairs()
    if a.patient:
        pairs = [p for p in pairs if p.startswith(a.patient)]
    for p in pairs[:a.limit]:
        print(p)
    print(f'\n{len(pairs)} pair(s)'
          + (f'; showing {min(a.limit, len(pairs))}' if len(pairs) > a.limit else ''))


def cmd_screen(a):
    """Retrieve candidate trials for a patient, then decide each one."""
    import pipeline
    try:
        rs = pipeline.screen(a.patient, db=a.db, limit=a.limit,
                             system=a.system)
    except RuntimeError as e:
        sys.exit(str(e))
    ev = [r for r in rs if r.evaluated]
    print(f'patient  : {a.patient}')
    print(f'retrieved: {len(rs)} candidate trial(s)')
    print(f'decided  : {len(ev)}   eligible: {sum(1 for r in ev if r.eligible)}\n')
    print(f'{"rank":>5s}  {"trial":<16s}{"verdict":<16s}why')
    print('-' * 78)
    for r in rs:
        v = r.decision.upper() if r.decision else 'not evaluated'
        why = r.reasoning[:34] if r.decision else 'no stage-1 data for this pair'
        print(f'{str(r.rank):>5s}  {r.nct_id:<16s}{v:<16s}{why}')
    if len(ev) < len(rs):
        print(f'\n{len(rs) - len(ev)} candidate(s) could not be evaluated: VERDICT needs '
              f'per-pair\nstage-1 artifacts under $VERDICT_PAIR_DATA. They are NOT '
              f'ineligible --\nthey are undecided. See docs/DATA.md.')


def cmd_headline(a):
    """Reproduce the paper's headline table from the shipped artifacts."""
    import runpy
    import sys as _s
    root = pathlib.Path(__file__).resolve().parent
    _s.argv = ['headline']
    runpy.run_path(str(root / 'verdict' / 'headline.py'), run_name='__main__')


def cmd_systems(a):
    import verdict
    verdict.systems()                       # ensure built-ins are registered
    print(f'{"name":<10s}{"function":<26s}description')
    print('-' * 92)
    from verdict.registry import _REGISTRY
    for k, (fn, desc) in _REGISTRY.items():
        print(f'{k:<10s}{getattr(fn, "__name__", "?"):<26s}{desc}')


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

    p = sub.add_parser('headline',
                       help="reproduce the paper's headline table")
    p.set_defaults(func=cmd_headline)

    p = sub.add_parser('screen', help='retrieve candidate trials, then decide each')
    p.add_argument('patient', help="patient id, e.g. sigir-20141")
    p.add_argument('--db', help='clause database for retrieval')
    p.add_argument('--limit', type=int, help='stop after N candidates')
    p.add_argument('--system', choices=list(_systems()), default='verdict')
    p.set_defaults(func=cmd_screen)

    for name, fn in (('match', cmd_match), ('explain', cmd_explain)):
        p = sub.add_parser(name, help=f'{name} a patient--trial pair')
        p.add_argument('pair_id')
        p.add_argument('--system', choices=list(_systems()), default='verdict')
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
