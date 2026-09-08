# Stanford OVAL Clinical Trial Matcher

Software that finds clinical trials a patient might qualify for, decides
whether they actually meet the criteria, and shows its reasoning so a
clinician can check it.

It comes in two parts, which run in order:

| | what it does | command |
|---|---|---|
| **SatIR** | Searches a large trial corpus and narrows it to the handful worth a closer look. To do that it first turns each trial's eligibility criteria, and each patient note, into structured facts a computer can compare. | `satir` |
| **VERDICT** | Takes one patient and one trial and answers *does this patient qualify?* — along with which criterion decided it, what it had to assume because the chart was silent, and what would change the answer. | `verdict` |

The two share a common core (`smt_core`) that reads clinical language and
normalizes it: mapping "kidney failure" and "renal insufficiency" to the same
concept, pulling out numbers and units, and so on.

Why bother with the second part? A language model asked "is this patient
eligible?" will answer, but you cannot tell whether its explanation is the
real reason for its answer. VERDICT separates *reading* the chart from
*deciding* the verdict: a solver makes the decision from the extracted facts,
so the explanation is generated from the same thing that produced the answer,
not written afterwards.

```bash
pip install -e .
satir --help             # find candidate trials
verdict systems          # decide a patient-trial pair
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

## What the system tells you about a decision

Every verdict comes with three things beyond yes/no. Using the paper's running
example — a trial that needs `egfr` true and creatinine clearance of at least
60, and a chart that records a failing eGFR but says nothing about clearance:

```python
from smt_core.maxsmt import Condition, solve, OBSERVED, UNRESOLVED

phi = ["(declare-const |egfr| Bool)", "(declare-const |crcl| Real)",
       "(assert |egfr|)", "(assert (>= |crcl| 60))"]
a = solve(phi, [Condition("egfr", False, OBSERVED),      # chart says: failing
                Condition("crcl", None, UNRESOLVED)])    # chart is silent
```

| what you get | in the example | what it means |
|---|---|---|
| `a.decision` | `'ineligible'` | the answer |
| `a.trace` | why | how the solver got there |
| `a.assumptions` | `{'crcl': ...}` | **what it had to assume.** The chart never mentions clearance, so the system filled it in to reach an answer. Every one of these is something a clinician should check. |
| `a.pivotal` | `['egfr']` | **what would change the answer.** The eGFR result is the one thing standing between this patient and the trial. |

`a.delta_e` and `a.delta_i` split that last one: what would have to change to
make the patient *eligible*, and what would make them *ineligible*. Whichever
is relevant becomes `pivotal`.

Three properties hold by construction, and the tests check them: a patient is
eligible exactly when nothing needs to change to make them eligible; ineligible
exactly when nothing needs to change to make them ineligible; and there is
always something that would flip the answer.

### An assumed value is not a measurement

When the chart is silent about a number, the solver picks *some* value that
satisfies the trial. For "clearance of at least 60" it might pick 60, 72, or
500 — all equally valid, none a fact about this patient.

So the explanation must state **the requirement, not the number the solver
picked**. Saying "creatinine clearance 72" would invent a lab result:

```python
a.for_verbalizer(phi)["assumptions"]["crcl"]
# {'requirement': '>= 60', 'witness': 60.0}
#   report the requirement; the witness is just the solver's pick
```

`verbalizer/prompts/_freeform_rationale_v13_maxsmt.prompt` carries this rule.
It is a new prompt rather than an edit to the old one, so rationales published
with the earlier version can still be reproduced.

### Two versions of the algorithm

The paper changed between the submitted and updated versions, so both are
implemented. The difference is real, not a rename.

```python
from smt_core.maxsmt import solve, RESIDUAL, MAXSMT

solve(phi, conds, version=RESIDUAL)   # submitted paper
solve(phi, conds, version=MAXSMT)     # updated paper (default)
```

In the submitted version, the unanswered conditions were reported as
*requirements still outstanding* (`crcl >= 60`). In the update they are
reported as *assumptions the system made* (`crcl = 60`) — a requirement and a
value satisfying it are different claims. The update also computes both
directions of "what would change the answer" separately, where the submitted
version computed one.

| | `RESIDUAL` (submitted) | `MAXSMT` (update) |
|---|---|---|
| unanswered conditions | requirements outstanding: `{crcl: '>= 60'}` | assumptions made: `{crcl: 60.0}` |
| what would flip it | one calculation | both directions, separately |
| chart-silent value supplied by policy | called `ASSUMED` | called `IMPUTED` |

`ASSUMED` is still accepted, so records written under either version load
unchanged.

### Tying the output back to the paper

If you are reading alongside the paper, `to_paper()` labels every field with
its symbol and the step that produces it, so the code and the paper cannot
drift apart:

```python
print(a.describe())
# VERDICT artifacts  [maxsmt = update paper]
#   d        (Step 2 ) ineligible
#   rho      (Step 4 ) {'crcl': 60.0}
#   delta    (Step 6 ) ['egfr']
#   delta_E  (Step 3 ) ['egfr']
#   delta_I  (Step 5 ) []
```

Fields that a version does not compute are left out of the export rather than
returned empty, so "we did not calculate this" cannot be misread as "we
calculated it and found nothing".

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

### Missing data must not look like a "no"

If the system cannot find the data for a pair, the underlying matcher returns
`ineligible` with the note `"no data"`. That is how the paper's numbers were
produced, so it stays the default — but it is a trap for anything built on
top: a missing file then looks exactly like a patient who genuinely does not
qualify. For a trial matcher that is the dangerous direction, because the
patient quietly never gets surfaced.

So either check for it, or ask to be told loudly:

```python
from matchers import variants
from matchers.schema import MissingPairData

d = variants.smt_lm_evidence_arbiter(pair_id)
if d.is_missing_data:
    ...                      # this is not a verdict

variants.strict(True)        # or: export VERDICT_STRICT=1
try:
    d = variants.smt_lm_evidence_arbiter(pair_id)
except MissingPairData:
    ...                      # raises instead of guessing
```

The `verdict` command and the `verdict` Python package both do this for you:
they refuse an unknown pair rather than returning an answer.

### What this does not do yet

`verdict` works on pairs that have already been processed — it reads
per-pair files from `$VERDICT_PAIR_DATA` (default `experiments/53_v2_full`).

**You cannot yet hand it a raw chart and a trial and get an answer.** That
first processing step lives in a separate codebase (`cmsrc`; set `$CMSRC_DIR`,
see [docs/DATA.md](docs/DATA.md)). Connecting it so that
`verdict match --chart chart.txt --trial NCT...` works end to end is the main
gap between this repository and something you could point at a new patient.

To reproduce the numbers in the paper, see
[docs/REPRODUCE_TABLES.md](docs/REPRODUCE_TABLES.md). For where the data comes
from and what may be redistributed, see [docs/DATA.md](docs/DATA.md).

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
