#!/usr/bin/env python3
"""Invariant checks — run after EVERY change.

Asserts the things that must not break: the CLI works, the Python API works,
the reproduction scripts still emit the paper's numbers, and no secret or
machine-specific path has leaked back into shippable code.

    python scripts/check_invariants.py          # all
    python scripts/check_invariants.py -k table # only matching checks
    python scripts/check_invariants.py -v       # show output of failures

Exit code 0 = all green.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
CHECKS = []
VERBOSE = False


class Skip(Exception):
    """Raised when a check's local-only data is absent (fresh clone)."""


def check(name, needs=None):
    """needs: repo-relative path that must exist, else the check SKIPs."""
    def deco(fn):
        def wrapped():
            if needs and not (ROOT / needs).exists():
                raise Skip(needs)
            return fn()
        CHECKS.append((name, wrapped))
        return wrapped
    return deco


PAIR_DATA = 'experiments/53_v2_full/cmsrc_out'


def sh(*args, timeout=600):
    r = subprocess.run([PY, *args], cwd=ROOT, capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def want(hay: str, *needles):
    missing = [n for n in needles if n not in hay]
    if missing:
        raise AssertionError(f'missing from output: {missing}')


# ---------------------------------------------------------------- CLI
@check('cli:systems')
def _():
    rc, out = sh('verdict_cli.py', 'systems')
    assert rc == 0, out
    want(out, 'smt_lm_evidence_arbiter', 'trialgpt', 'hybrid_strict')


@check('cli:list', needs=PAIR_DATA)
def _():
    rc, out = sh('verdict_cli.py', 'list', '--limit', '3')
    assert rc == 0, out
    want(out, 'sigir-', 'pair(s)')
    n = int(re.search(r'(\d+) pair\(s\)', out).group(1))
    assert n > 800, f'expected >800 pairs, got {n}'


@check('cli:match', needs=PAIR_DATA)
def _():
    rc, out = sh('verdict_cli.py', 'match', 'sigir-20141__NCT00337116')
    assert rc == 0, out
    want(out, 'decision: ELIGIBLE')


@check('cli:explain-audit-trail', needs=PAIR_DATA)
def _():
    rc, out = sh('verdict_cli.py', 'explain', 'sigir-20141__NCT00337116')
    assert rc == 0, out
    want(out, 'audit trail', 'atom_mining', 'smt_solve')


@check('cli:bad-pair-errors-cleanly', needs=PAIR_DATA)
def _():
    rc, out = sh('verdict_cli.py', 'match', 'nope__NCT0')
    assert rc != 0, 'expected nonzero exit for unknown pair'
    want(out, 'unknown pair')


@check('cli:ineligible-path', needs=PAIR_DATA)
def _():
    rc, out = sh('verdict_cli.py', 'match', 'sigir-20141__NCT00990262')
    assert rc == 0, out
    want(out, 'decision: INELIGIBLE')


# ---------------------------------------------------------------- API
@check('api:python-import', needs=PAIR_DATA)
def _():
    rc, out = sh('-c', 'from matchers import variants;'
                       'd=variants.smt_lm_evidence_arbiter("sigir-20141__NCT00337116");'
                       'print(d.decision, len(d.audit_trail), d.reasoning)')
    assert rc == 0, out
    decision = out.split()[0]
    assert decision == 'eligible', f'expected exactly "eligible", got {decision!r}'
    assert 'no data' not in out, ('API silently returned a verdict with no pair '
                                  'data loaded -- see api:no-data-is-not-a-verdict')


@check('api:no-data-is-not-a-verdict')
def _():
    """A missing pair must be distinguishable from a real INELIGIBLE.

    matchers.variants returns decision='ineligible', reasoning='no data' when
    the pair cannot be loaded. That is the dangerous error direction for a
    trial matcher, so callers MUST check reasoning. verdict_cli guards this by
    validating the pair id up front; this check pins both halves.
    """
    rc, out = sh('-c', 'from matchers import variants;'
                       'd=variants.smt_lm_evidence_arbiter("TOTALLY__FAKE999");'
                       'print(repr(d.decision), repr(d.reasoning))')
    assert rc == 0, out
    assert 'no data' in out, ('sentinel changed: a missing pair no longer '
                              'reports "no data" -- update verdict_cli and DATA.md')
    rc, out = sh('verdict_cli.py', 'match', 'TOTALLY__FAKE999')
    assert rc != 0, 'CLI must refuse an unknown pair rather than emit a verdict'
    assert ('unknown pair' in out) or ('no pair data' in out), \
        f'CLI refused but with an unhelpful message: {out[:200]}'
    assert 'decision' not in out.lower(), \
        'CLI emitted a verdict for a nonexistent pair'


@check('api:strict-mode-raises', needs=PAIR_DATA)
def _():
    """Strict mode must raise on a missing pair, and not change real verdicts."""
    rc, out = sh('-c',
        'from matchers import variants;'
        'from matchers.schema import MissingPairData;'
        'variants.strict(True);'
        'ok=0\n'
        'try:\n'
        '    variants.smt_lm_evidence_arbiter("FAKE__NCT0")\n'
        'except MissingPairData: ok=1\n'
        'print("raised", ok);'
        'print("real", variants.smt_lm_evidence_arbiter("sigir-20141__NCT00337116").decision)')
    assert rc == 0, out
    want(out, 'raised 1', 'real eligible')


@check('api:default-mode-preserved', needs=PAIR_DATA)
def _():
    """Default (non-strict) behaviour must stay exactly as the paper ran it."""
    rc, out = sh('-c',
        'from matchers import variants;'
        'd=variants.smt_lm_evidence_arbiter("FAKE__NCT0");'
        'print(d.decision, "|", d.reasoning, "|", d.is_missing_data)')
    assert rc == 0, out
    want(out, 'ineligible | no data | True')


# ------------------------------------------------- reproduction numbers
@check('table2:trec-f1-matches-paper')
def _():
    svpo = pathlib.Path(os.environ.get('SVPO_RL', ROOT.parent / 'svpo-rl'))
    if not (svpo / 'data/test_clean_tagged.jsonl').exists():
        raise Skip('svpo-rl checkout (set $SVPO_RL)')
    rc, out = sh('scripts/tables/table2_trec2021_f1.py')
    assert rc == 0, out
    want(out, '363 pairs', '190 eligible / 173 ineligible')
    for label, f1 in (('GPT-5-mini  VERDICT', '0.828'),
                      ('Qwen2.5-7B  VERDICT', '0.738'),
                      ('Qwen2.5-7B  CoT', '0.663')):
        row = next((l for l in out.splitlines() if l.startswith(label)), None)
        assert row, f'row missing: {label}'
        assert f1 in row, f'{label}: expected F1 {f1}, got: {row.strip()}'


@check('table9:sigir-552-pairs')
def _():
    rc, out = sh('scripts/tables/table9_dataset_stats.py')
    assert rc == 0, out
    want(out, '552')


@check('table10:verdict-balance-matches-paper')
def _():
    rc, out = sh('scripts/tables/table10_verdict_balance.py')
    assert rc == 0, out
    want(out, '552-pair comparison set')
    # the three systems whose canonical source is confirmed exact
    for label, elig, med in (('VERDICT', '55.4', '1021'),
                             ('ourLLM',  '35.0', '1195'),
                             ('CoT LLM', '36.1', '592')):
        row = next((l for l in out.splitlines() if l.startswith(label)), None)
        assert row, f'row missing: {label}'
        assert row.count(elig) >= 2, f'{label}: elig% != paper: {row.strip()}'
        assert row.count(med) >= 2, f'{label}: median != paper: {row.strip()}'
        assert row.rstrip().endswith('ok'), f'{label} no longer exact: {row.strip()}'
    # TrialGPT is known-unreproducible; the script must keep saying so
    want(out, 'DOES NOT MATCH', 'Do not cite this row')


@check('zspm:92pct-silence',
       needs='experiments/counterfactual/05_self_faithfulness/mbench_3cell')
def _():
    rc, out = sh('scripts/tables/zspm_silence_breakdown.py')
    assert rc == 0, out
    want(out, '92% of grounds are chart-silence')


@check('gold:278-eligible-274-ineligible')
def _():
    fp = ROOT / 'experiments/accuracy/data/gold_5sys_freeform_balanced.json'
    g = json.loads(fp.read_text())['gold']
    e = sum(1 for v in g.values() if v is True)
    assert (len(g), e, len(g) - e) == (552, 278, 274), (len(g), e)


@check('deps:declared-match-imports')
def _():
    """Every third-party import in shipped code must be a declared dependency.

    Guards against the usual open-source failure: code grows an import, the
    install spec never learns about it, and a fresh clone dies on ImportError.
    """
    import ast as _ast
    try:
        import tomllib
    except ModuleNotFoundError:                       # pragma: no cover
        raise Skip('tomllib (py<3.11)')

    meta = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    declared = set(meta.get('dependencies', []))
    for extra in meta.get('optional-dependencies', {}).values():
        declared |= set(extra)
    # "z3-solver>=4.12" -> "z3-solver"
    dist = {re.split(r'[<>=!\[]', d)[0].strip().lower() for d in declared}

    # import name -> distribution name, where they differ
    ALIAS = {'z3': 'z3-solver', 'dspy': 'dspy-ai', 'sentence_transformers':
             'sentence-transformers', 'azure': 'azure-identity',
             'yaml': 'pyyaml', 'sklearn': 'scikit-learn'}

    stdlib = set(sys.stdlib_module_names)
    local = {'matchers', 'smt_core', 'verbalizer', 'rationale_generators',
             'counterfactual_modifier', 'scripts', 'verdict_cli'}
    undeclared = {}
    for d in ('matchers', 'smt_core', 'verbalizer', 'counterfactual_modifier'):
        base = ROOT / d
        if not base.exists():
            continue
        for f in base.rglob('*.py'):
            if '.bak' in f.name:
                continue
            src = f.read_text(errors='ignore')
            try:
                tree = _ast.parse(src)
            except SyntaxError:
                continue
            # imports inside try/except ImportError are optional by construction
            guarded = set()
            for n in _ast.walk(tree):
                if isinstance(n, _ast.Try) and any(
                        isinstance(h.type, _ast.Name) and
                        h.type.id in ('ImportError', 'ModuleNotFoundError')
                        for h in n.handlers):
                    for sub in _ast.walk(n):
                        if isinstance(sub, _ast.Import):
                            guarded |= {a.name.split('.')[0] for a in sub.names}
                        elif isinstance(sub, _ast.ImportFrom) and sub.module:
                            guarded.add(sub.module.split('.')[0])
            # modules made importable by an explicit sys.path.insert in this file
            path_local = 'sys.path.insert' in src or 'sys.path.append' in src
            for n in _ast.walk(tree):
                mods = []
                if isinstance(n, _ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, _ast.ImportFrom) and n.level == 0 and n.module:
                    mods = [n.module]
                for m in mods:
                    top = m.split('.')[0]
                    if not top or top in stdlib or top in local:
                        continue
                    if top in guarded or path_local:
                        continue   # optional, or resolved via sys.path (see
                                   # imports:shipped-modules-are-importable)
                    # sibling modules imported by filename, not packages
                    if (f.parent / f'{top}.py').exists():
                        continue
                    if ALIAS.get(top, top).lower() not in dist:
                        undeclared.setdefault(top, str(f.relative_to(ROOT)))
    assert not undeclared, ('imported but not declared in pyproject: '
                            + ', '.join(f'{k} ({v})' for k, v in
                                        sorted(undeclared.items())[:8]))


@check('imports:shipped-modules-are-importable')
def _():
    """Every module in the release payload must import without ImportError.

    Catches sys.path hacks reaching into directories excluded from the release,
    and imports of modules that no longer exist anywhere.
    """
    import ast as _ast
    SHIP = ('matchers', 'verbalizer', 'counterfactual_modifier', 'smt_core')
    # Module names that are known not to resolve, with the reason. These are
    # tracked debt, not silent ignores -- shrink this set, never grow it.
    # Verified 2026-09-03 by a full scan; file lists are exact.
    KNOWN_BROKEN = {
        'overnight':
            'no such module anywhere -- breaks aegis/apply_soft_aux_solve.py, '
            'aegis/apply_strict_inc_to_4o.py',
        'run_better_nl_full':
            'no such module anywhere -- breaks aegis/run_verbalize.py, '
            'single_shot_llm/run.py, trialgpt/run.py, '
            'verbalizer/run_aegis_verbalize.py',
        'repro_headline':
            'lives in experiments/accuracy/scripts (excluded from the release) '
            '-- breaks aegis/aegis_arbiter.py, aegis/aegis_arbiter_eval.py, '
            'verbalizer/run_aegis_v9_arbiter_verbalize.py',
        'cf_maxsat':          'lives in experiments/counterfactual/utils, excluded',
        'cf_dataset':         'lives in experiments/, excluded from the release',
        'cf_blockers':        'lives in experiments/, excluded from the release',
        'build_judge_input':  'sibling script, not importable as a module',
    }
    # 9 shipped files total. The `verdict` CLI and matchers.variants do NOT
    # depend on any of these, which is why the CLI works; the affected files
    # are batch entry points. Shrink this set, never grow it.
    # A declared dependency that merely is not installed here is NOT breakage.
    try:
        import tomllib
        meta = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        declared = set(meta.get('dependencies', []))
        for extra in meta.get('optional-dependencies', {}).values():
            declared |= set(extra)
        dist = {re.split(r'[<>=!\[]', d)[0].strip().lower() for d in declared}
    except Exception:
        dist = set()
    ALIAS = {'z3': 'z3-solver', 'dspy': 'dspy-ai',
             'sentence_transformers': 'sentence-transformers',
             'azure': 'azure-identity', 'yaml': 'pyyaml', 'sklearn': 'scikit-learn'}

    # Precompute local module names ONCE (scanning per-import walks the whole
    # tree and takes minutes).
    localmods = set(SHIP)
    for d in SHIP:
        base = ROOT / d
        if not base.exists():
            continue
        for f in base.rglob('*.py'):
            if '__pycache__' in str(f):
                continue
            localmods.add(f.stem)
            localmods.add(f.parent.name)

    resolvable, unresolvable = set(), {}
    for d in SHIP:
        base = ROOT / d
        if not base.exists():
            continue
        for f in sorted(base.rglob('*.py')):
            if '.bak' in f.name or '__pycache__' in str(f):
                continue
            rel = str(f.relative_to(ROOT))
            try:
                tree = _ast.parse(f.read_text(errors='ignore'))
            except SyntaxError:
                continue
            for n in tree.body:          # module level only; nested = lazy
                mods = []
                if isinstance(n, _ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, _ast.ImportFrom) and n.level == 0 and n.module:
                    mods = [n.module]
                for m in mods:
                    top = m.split('.')[0]
                    if (top in sys.stdlib_module_names or top in localmods
                            or top in resolvable or top in KNOWN_BROKEN
                            or ALIAS.get(top, top).lower() in dist):
                        continue
                    if top in unresolvable.values():
                        unresolvable.setdefault(rel, top)
                        continue
                    try:
                        __import__(top)
                        resolvable.add(top)
                    except Exception:
                        unresolvable.setdefault(rel, top)

    assert not unresolvable, (
        'modules that will not import (not declared, not local, not known debt):'
        '\n  ' + '\n  '.join(f'{k}: {v}' for k, v in unresolvable.items()))


# ---------------------------------------------------------------- hygiene
SHIPPED = ['scripts', 'smt_core', 'verbalizer', 'rationale_generators',
           'counterfactual_modifier', 'verdict_cli.py']


@check('hygiene:no-secrets-in-env-example')
def _():
    t = (ROOT / '.env.example').read_text()
    assert not re.search(r'sk-[A-Za-z0-9]{20,}', t), 'API key in .env.example'
    for host in re.findall(r'https?://([^/"\s]+)', t):
        assert host.startswith(('YOUR-', 'localhost', '127.0.0.1')), \
            f'real hostname in .env.example: {host}'


@check('hygiene:env-is-gitignored')
def _():
    gi = (ROOT / '.gitignore').read_text().splitlines()
    assert '.env' in gi and '!.env.example' in gi


@check('hygiene:no-hardcoded-paths-on-ship-path')
def _():
    bad = []
    for rel in SHIPPED:
        p = ROOT / rel
        files = [p] if p.is_file() else [f for f in p.rglob('*')
                                         if f.suffix in ('.py', '.sh')]
        for f in files:
            if '.bak' in f.name:
                continue
            try:
                txt = f.read_text(errors='ignore')
            except Exception:
                continue
            for m in re.findall(r'/Users/[A-Za-z0-9_.-]+', txt):
                bad.append(f'{f.relative_to(ROOT)}: {m}')
    assert not bad, 'hardcoded home paths:\n  ' + '\n  '.join(bad[:10])


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument('-k', help='only checks whose name contains this')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    VERBOSE = a.verbose

    checks = [(n, f) for n, f in CHECKS if not a.k or a.k in n]
    print(f'running {len(checks)} invariant checks\n')
    failed, skipped = [], []
    for name, fn in checks:
        try:
            fn()
            print(f'  PASS  {name}')
        except Skip as s:
            print(f'  SKIP  {name}  (needs {s})')
            skipped.append(name)
        except Exception as e:
            print(f'  FAIL  {name}')
            failed.append((name, e))
            if VERBOSE:
                print(f'        {e}')
    print()
    if skipped:
        print(f'{len(skipped)} skipped -- local-only data absent '
              f'(expected in a fresh clone; see DATA.md)')
    if failed:
        print(f'{len(failed)}/{len(checks)} FAILED')
        for n, e in failed:
            print(f'\n--- {n} ---\n{str(e)[:900]}')
        return 1
    print(f'all {len(checks) - len(skipped)} runnable invariants hold')
    return 0


if __name__ == '__main__':
    sys.exit(main())
