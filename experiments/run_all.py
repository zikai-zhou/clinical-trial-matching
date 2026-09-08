"""Regenerate all experiment tables and numbers from saved data/ JSON files.

Walks experiments/NN_*/analyze.py in order and invokes each.
Outputs land in each experiment's out/ subdirectory.
"""
import importlib.util, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
experiments = sorted(p for p in HERE.iterdir() if p.is_dir() and p.name[:2].isdigit())

for exp_dir in experiments:
    analyze = exp_dir / "analyze.py"
    if not analyze.exists():
        print(f"[skip] {exp_dir.name}: no analyze.py")
        continue
    print(f"\n===== {exp_dir.name} =====")
    spec = importlib.util.spec_from_file_location(exp_dir.name, analyze)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[error] {exp_dir.name}: {type(e).__name__}: {e}")
