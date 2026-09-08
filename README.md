# SatIR + VERDICT — constraint-satisfaction clinical-trial retrieval and auditable matching

This repository contains **two related systems** from two papers:

| | what it does | entry point |
|---|---|---|
| **SatIR** | constraint-satisfaction-based trial *retrieval and compilation* — trial/patient compilers, clause DB indexing, SQL retrieval | `satir` |
| **VERDICT** | auditable patient--trial *eligibility matching* — LLM formalization + SMT/MaxSAT decision, with a per-criterion audit trail | `verdict` |

They run in that order and share `smt_core` (entity canonicalization, attribute
extraction, inference engines). SatIR compiles trials and patients into
constraint programs and retrieves candidates at corpus scale; VERDICT then
decides a given patient--trial pair over those programs and shows its work.

```bash
pip install -e .
satir --help             # SatIR: setup / compile / index / retrieve / match
verdict systems          # VERDICT: matcher variants
```



## Quickstart

```bash
pip install -e .
cp .env.example .env      # fill in your API credentials
source .env
```

### SatIR — retrieval and compilation

```bash
satir setup               # validate environment and prerequisites
satir info
satir compile-trial ...   # trial text  -> constraint program
satir compile-patient ... # patient note -> coded facts
satir index               # build the clause database
satir retrieve ...        # candidate trials at corpus scale
```

Run `satir <subcommand> --help` for arguments. Services (SNOMED Snowstorm,
Elasticsearch) are described in `docs/QUICKSTART.md`; build artifacts in
`docs/BUILD_ARTIFACTS.md`.

### VERDICT — eligibility matching

Decide a patient--trial pair:

```bash
verdict list --limit 5
verdict match   sigir-20141__NCT00337116
verdict explain sigir-20141__NCT00337116     # with the audit trail
verdict systems                              # the matcher variants
```

```
pair    : sigir-20141__NCT00337116
system  : verdict
decision: ELIGIBLE
why     : SMT solver accepted; no arbitration needed.

audit trail (2 steps)
1. atom_mining
     evidence: {'n_atoms': 40}
2. smt_solve
     decision: eligible
```

Or from Python:

```python
from matchers import variants
d = variants.smt_lm_evidence_arbiter("sigir-20141__NCT00337116")
print(d.decision, d.reasoning)
for step in d.audit_trail:
    print(step)
```

## Python API

Both systems are importable, not just command-line tools.

### SatIR

```python
import satir

satir.config()                              # resolved SatIRConfig
satir.compile_trial("NCT00337116")          # trial text   -> SMT constraints
satir.compile_patient("sigir-20141")        # patient note -> coded facts
satir.index()                               # constraints  -> clause database
satir.retrieve()                            # SQL constraint-satisfaction retrieval
satir.match("sigir-20141", "NCT00337116")   # SMT eligibility check
```

The compiler and indexer stages are argv-driven underneath, so extra options
pass straight through rather than being re-declared in a parallel schema that
could drift from the real parser:

```python
satir.compile_trial("NCT00337116", "--side", "inclusion", "--stop-after", "ir")
```

For programmatic matching, the genuinely reusable pieces are re-exported:
`satir.load_patient`, `satir.build_ctx_from_persisted`, `satir.run_match_for_side`.

Imports are lazy — `import satir` pulls in no heavy backend, so reading config
costs nothing. Touching a function that needs one raises with the extra to
install (`pip install -e '.[llm]'`).

### VERDICT

```python
import verdict

verdict.systems()                           # {name: description}
d = verdict.match("sigir-20141__NCT00337116")
print(d.decision, d.reasoning)              # 'eligible', 'SMT solver accepted; ...'
for step in d.audit_trail:
    print(step.stage, step.decision)

print(verdict.explain(pair_id))             # rendered audit trail
verdict.match(pair_id, system="hybrid")     # a different variant
```

**The API defaults to `strict=True`**, so a pair that cannot be loaded raises
`MissingPairData` instead of returning a confident `ineligible`. Pass
`strict=False` for the paper-compatible sentinel:

```python
verdict.match("nope__NCT0")                  # raises MissingPairData
verdict.match("nope__NCT0", strict=False)    # Decision(reasoning='no data')
```

## Accountability artifacts (MaxSMT)

`smt_core.maxsmt` implements Steps 2--6 of the published VERDICT algorithm.

```python
from smt_core.maxsmt import Condition, solve, OBSERVED, UNRESOLVED

phi = ["(declare-const |egfr| Bool)", "(declare-const |crcl| Real)",
       "(assert |egfr|)", "(assert (>= |crcl| 60))"]
a = solve(phi, [Condition("egfr", False, OBSERVED),
                Condition("crcl", None, UNRESOLVED)])

a.decision      # 'ineligible'          d
a.trace         # why                   gamma
a.assumptions   # {'crcl': 60.0}        rho  -- the solver's witness
a.pivotal       # ['egfr']              delta
a.delta_e, a.delta_i
```

Three invariants from the paper are asserted in the test suite:
`delta_E = {}` iff ELIGIBLE, `delta_I = {}` iff INELIGIBLE, and `delta` is
never empty.

### Two published formulations

Both are implemented and selectable; neither is a rename of the other.

```python
from smt_core.maxsmt import solve, RESIDUAL, MAXSMT

solve(phi, conds, version=RESIDUAL)   # submitted paper
solve(phi, conds, version=MAXSMT)     # updated paper (default)
```

| | `RESIDUAL` (submitted) | `MAXSMT` (update) |
|---|---|---|
| `rho` | **residual constraints** — the UNRESOLVED conditions, as requirements: `{crcl: '>= 60'}` | **assumptions** — the value the solver assigns: `{crcl: 60.0}` |
| `delta` | one MaxSAT call, formula depends on `d` (Step 4) | `delta_I` if ELIGIBLE else `delta_E` (Step 6) |
| `delta_E`/`delta_I` | not computed | Steps 3 and 5 |
| status vocabulary | OBSERVED / **ASSUMED** / UNRESOLVED | OBSERVED / **IMPUTED** / UNRESOLVED |
| solver | MaxSAT | MaxSMT |

`ASSUMED` is accepted as an alias for `IMPUTED` so records written under
either paper load unchanged.

### Mapping artifacts to the paper

`to_paper()` keys the artifacts by their symbol, with the meaning and step
number attached, so code and paper cannot drift:

```python
a = solve(phi, conds, version=MAXSMT)
a.to_paper()["rho"]
# {'value': {'crcl': 60.0}, 'field': 'assumptions', 'step': 'Step 4',
#  'meaning': 'assumptions: the value MAXSMT assigns to each UNRESOLVED
#              condition (a witness, arbitrary within the satisfying region)'}

print(a.describe())
# VERDICT artifacts  [maxsmt = update paper]
#   d        (Step 2 ) ineligible
#   rho      (Step 4 ) {'crcl': 60.0}
#   delta    (Step 6 ) ['egfr']
#   delta_E  (Step 3 ) ['egfr']
#   delta_I  (Step 5 ) []
```

Symbols absent from a version (`delta_E`/`delta_I` under `RESIDUAL`) are
**omitted** rather than exported empty, so a consumer cannot mistake
"not computed" for "computed and found empty".

### A witness is not a finding

The witness for an unresolved numeric condition is arbitrary within the
satisfying region: for `crcl >= 60` the solver may return 60, 72, or 500, and
none is a fact about the patient. So a rationale must report **the requirement,
never the witness**:

```python
a.for_verbalizer(phi)["assumptions"]["crcl"]
# {'requirement': '>= 60', 'witness': 60.0}
```

`verbalizer/prompts/_freeform_rationale_v13_maxsmt.prompt` carries this rule.
It is a new version rather than an edit to v12, so previously published
rationales stay reproducible.

### Tests and checks

```bash
pytest                                    # fast unit suite (~0.3s, no services)
python scripts/check_invariants.py        # ~1s, 26 checks
```

Run this after every change. It covers the CLI, the Python API, the
reproduction numbers (asserted against the values printed in the paper, not
merely "exits 0"), dependency/import health, and secret and machine-path
hygiene. Checks whose local-only data is absent SKIP with the reason rather
than failing, so it is also meaningful in a fresh clone. CI runs it on 3.12
and 3.13.

### Missing data is not a verdict

By default a variant that cannot load its pair returns
`decision="ineligible", reasoning="no data"` — the behaviour the paper's
numbers were produced under. Downstream that is a hazard: an absent file looks
exactly like a real INELIGIBLE, and for a trial matcher that is the harmful
direction, since the patient is silently not surfaced. Either check the flag or
turn on strict mode:

```python
from matchers import variants
from matchers.schema import MissingPairData

d = variants.smt_lm_evidence_arbiter(pair_id)
if d.is_missing_data:
    ...                      # not a verdict

variants.strict(True)        # or: export VERDICT_STRICT=1
try:
    d = variants.smt_lm_evidence_arbiter(pair_id)
except MissingPairData:
    ...                      # raises instead of guessing
```

The `verdict` CLI validates the pair id up front and refuses unknown pairs, so
it never emits a verdict for missing data.

### Scope — read this before you install

`verdict` decides **pre-mined** patient--trial pairs. It reads the per-pair
artifacts produced by the stage-1 atom miner from `$VERDICT_PAIR_DATA`
(default `experiments/53_v2_full`). It does **not** yet accept a free-text
chart and trial and run the pipeline end to end: stage-1 mining lives in the
separate `cmsrc` codebase (set `$CMSRC_DIR`; see `docs/DATA.md`). Wiring that into a
single `verdict match --chart c.txt --trial NCT...` call is the main piece of
work between this repository and a general-purpose tool.

Reproduction of the paper's tables is documented in `docs/REPRODUCE_TABLES.md`;
data provenance and redistribution status in `docs/DATA.md`.

## License

**Apache License 2.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Free for any use, academic or commercial, including modification and
redistribution. Apache-2.0 also grants an explicit patent licence for the
method, and terminates that grant for anyone who brings a patent suit over
it — protection that MIT and BSD do not provide.

The code licence is separate from the data. The evaluation corpora are
third-party and carry their own terms; see [docs/DATA.md](docs/DATA.md).

## Layout

Two systems share one solver core. Nothing else is shared.

| | | |
|---|---|---|
| **SatIR** | `trial_compiler/` | trial text → SMT constraint programs |
| | `patient_compiler/` | patient notes → coded facts |
| | `db_indexer/` | constraints → clause database |
| | `sql_retrieval/` | constraint-satisfaction retrieval at corpus scale |
| | `smt_matcher/` | SMT eligibility check |
| | `matching_batch/`, `evaluation/`, `audit/` | batch runs, metrics, inspection |
| | `satir/`, `satir_cli.py` | public API and CLI |
| **shared** | `smt_core/` | entity canonicalization, attribute extraction, inference engines |
| | `smt_core/maxsmt.py` | the accountability artifacts (both paper versions) |
| **VERDICT** | `matchers/` | the matcher variants compared in the paper |
| | `verbalizer/` | rationale generation, prompts |
| | `rationale_generators/`, `counterfactual_modifier/` | flip sets, counterfactual edits |
| | `verdict/`, `verdict_cli.py` | public API and CLI |
| **support** | `scripts/` | reproduction + `check_invariants.py` |
| | `tests/unit/` | fast suite; `tests/*.py` are service-level scripts |
| | `experiments/`, `paper/`, `assets/` | run artifacts and figures |
| | `docs/` | reproduction, data provenance, architecture |

## The four matchers

Each `matchers/systems/<name>/` is a self-contained bundle:

```
<system>/
├── prompts/         — *.prompt files (edit to tune behavior)
├── run.py           — generator (chart+criteria → verdicts.jsonl)
├── verdicts.jsonl   — per-pair eligibility predictions
├── rationales.jsonl — per-pair NL reasoning (used by judges)
└── README.md        — system-specific notes
```

Tune any system by editing its `prompts/<name>.prompt` and re-running `run.py`.

| System | Model | F1 (5-system gold) |
|---|---|---|
| **AEGIS** | gpt-4.1 atom mining + Z3 solve | 0.873 |
| **single_shot_llm** (V5) | gpt-4.1 two-step | **0.904** |
| **trialgpt** | gpt-4.1 per-criterion | 0.804 |
| **shahlab** | gpt-4.1 Koopman prompt | 0.836 |

| Hybrid | F1 |
|---|---|
| **AEGIS+V5+TG MAJ** | **0.906** |
| AEGIS+V5 OR | 0.896 (R=0.975) |
| AEGIS+V5 AND | 0.879 (P=0.943) |
| Meta-fusion balanced | 0.878 (P=0.958) |

## The two experiments

**[`experiments/accuracy/`](experiments/accuracy/REPRODUCE.md)** — accuracy + F1 + inspection
- 5-system gold (n=532), bootstrap CIs, Pareto controllability, sharpness ratings, fliprate
- `inspection/mbench/` — 539-pair drillable mbench with per-atom mining outcomes
- `inspection/v5_beats_aegis/` — 52 cases where V5 beats AEGIS on the gold

**`experiments/typed_policy/`** — policy alignment
- 3-policy benchmark: AEGIS 100% compliance vs LLM 52-72%
- 4-axis stacked policies for fine-grained control
- 22 typed-atom dispatch (lab_chemistry, functional_score, etc.)

## Running

```bash
# Set up env
export OPENAI_ENDPOINT=...
export OPENAI_API_KEY=...
export OPENAI_ENDPOINT_GPT5=...   # for gold derivation only

# Re-mine + re-solve AEGIS
python matchers/systems/aegis/run.py
python matchers/systems/aegis/run_verbalize.py

# Run other systems
python matchers/systems/trialgpt/run.py
python matchers/systems/single_shot_llm/run.py --variant V5_TWO_STEP
python matchers/systems/shahlab/run.py

# Build gold + accuracy F1
python experiments/accuracy/scripts/run_5judges_5sys.py
python experiments/accuracy/scripts/build_gold_from_5sys.py
python experiments/accuracy/scripts/bootstrap_ci.py

# Build inspection mbench
python experiments/accuracy/scripts/build_aegis_v5_mbench.py
```

## Backup

`backup/` contains all historical artifacts — earlier experiment runs, exploratory scripts, deprecated tools, and pre-fix versions of data files. Preserved for reproducibility but not part of the active codebase.
