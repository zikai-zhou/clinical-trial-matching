"""SatIR — constraint-satisfaction clinical-trial retrieval and compilation.

    import satir

    satir.config()                        # resolved SatIRConfig
    satir.compile_trial("NCT00337116")    # trial text  -> SMT constraints
    satir.compile_patient("sigir-20141")  # patient note -> coded facts
    satir.index()                         # constraints -> clause database
    satir.retrieve()                      # SQL constraint-satisfaction retrieval
    satir.match("sigir-20141", "NCT00337116")   # SMT eligibility check

The compiler and indexer stages are argv-driven underneath: their upstream
`main(argv)` functions own the argument parsing, so these wrappers pass extra
options straight through rather than re-declaring a parallel schema that could
drift. Anything you would type after the subcommand goes in `*args`:

    satir.compile_trial("NCT00337116", "--side", "inclusion", "--stop-after", "ir")

For matching, the underlying module exposes genuinely reusable pieces and they
are re-exported here: `load_patient`, `build_ctx_from_persisted`,
`run_match_for_side`.

Imports are lazy: `import satir` works without the optional heavy dependencies
(torch, elasticsearch, matplotlib...). You only pay for what you call.
"""
from __future__ import annotations

from typing import Any

__all__ = ["config", "compile_trial", "compile_patient", "index", "retrieve",
           "match", "load_patient", "build_ctx_from_persisted",
           "run_match_for_side", "services", "benchmark"]


def config() -> Any:
    """The resolved SatIRConfig (paths, services, retrieval, keys)."""
    from smt_core.config import get_config
    return get_config()


def _run_argv(module_path: str, func: str, argv: list[str]) -> Any:
    """Call an argv-driven main() without leaking our own sys.argv into it."""
    import importlib, sys
    mod = importlib.import_module(module_path)
    fn = getattr(mod, func)
    saved = sys.argv
    sys.argv = [module_path.split(".")[-1]] + argv
    try:
        return fn()
    finally:
        sys.argv = saved


def compile_trial(trial_id: str, *args: str) -> Any:
    """Compile one trial's eligibility criteria into SMT constraints.

    Args:
        trial_id: NCT identifier.
        *args:    passed through, e.g. "--side", "inclusion".
    """
    return _run_argv("trial_compiler.compile_trial", "main", [trial_id, *args])


def compile_patient(patient_id: str, *args: str) -> Any:
    """Compile one patient's note into canonical constraint variables."""
    return _run_argv("patient_compiler.compile_patient", "main", [patient_id, *args])


def index(*args: str) -> Any:
    """Index compiled constraints into the clause database."""
    return _run_argv("db_indexer.cli", "main", list(args))


def retrieve(*args: str, db: str | None = None) -> int:
    """Run SQL constraint-satisfaction retrieval. Returns the exit code.

    Defaults come from `config().retrieval`; `*args` are appended verbatim.
    """
    import os, subprocess, sys, pathlib
    cfg = config()
    root = pathlib.Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "sql_retrieval.ops.constraint_retrieval",
           "--db", db or str(pathlib.Path(cfg.paths.build_dir) / "trial.db"),
           "--scope", cfg.retrieval.scope,
           "--important-mode", cfg.retrieval.important_mode,
           "--alt-mode", cfg.retrieval.alt_mode,
           "--parallel", str(cfg.retrieval.parallel)]
    if cfg.retrieval.enable_prevention:
        cmd.append("--enable-prevention-hits")
    cmd += list(args)
    return subprocess.run(
        cmd, env={**os.environ, "PYTHONPATH": str(root)}).returncode


def match(patient_id: str, trial_id: str, *args: str) -> Any:
    """SMT eligibility check for one patient--trial pair.

    For programmatic control over a single side, prefer `run_match_for_side`.
    """
    return _run_argv("smt_matcher.match_patient_to_trial", "main",
                     [patient_id, trial_id, *args])


def services(*args: str) -> int:
    """Manage Elasticsearch + Snowstorm (start/stop/status/install/import/check)."""
    import pathlib, subprocess
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "services.sh"
    return subprocess.run([str(script), *args]).returncode


def benchmark(*args: str) -> Any:
    """Benchmark retrieval performance."""
    return _run_argv("tests.benchmark_retrieval", "main", list(args))


def __getattr__(name: str) -> Any:
    """Lazily re-export the reusable matcher functions.

    Kept lazy so `import satir` does not pull in z3/azure just to read config.
    """
    if name in ("load_patient", "build_ctx_from_persisted", "run_match_for_side"):
        try:
            from smt_matcher import match_patient_to_trial as m
        except ImportError as e:
            raise ImportError(
                f"satir.{name} needs the optional inference backends: "
                f"pip install -e '.[llm]'   (missing: {e.name})") from e
        return getattr(m, name)
    raise AttributeError(name)
