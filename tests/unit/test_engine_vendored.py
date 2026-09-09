"""The vendored matcher must be runnable from anywhere.

Upstream resolved its roots against the current working directory, so it only
worked when invoked from inside the matcher directory. These tests pin the
fixes that made it importable and locatable as a package.
"""
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_engine_imports():
    import verdict.engine.match_patient_to_trial as m
    assert hasattr(m, "Config") and hasattr(m, "main")


def test_prompts_resolve_from_unrelated_cwd(tmp_path):
    """The bug that would hit a real user: prompts resolved against cwd."""
    code = (
        "from verdict.engine.match_patient_to_trial import Config;"
        "ps = Config().prompt_sources();"
        "print(sum(1 for v in ps.values() if not v.exists()))"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO))
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("0"), "prompts missing: " + out.stdout


def test_config_roots_are_absolute():
    from verdict.engine.match_patient_to_trial import Config
    c = Config()
    for name in ("data_root", "build_root", "project_root", "prompt_root"):
        p = getattr(c, name)
        assert p.is_absolute(), f"{name} is cwd-relative: {p}"


def test_run_without_endpoint_explains_itself():
    """A missing endpoint must produce guidance, not a traceback."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENAI_ENDPOINT", "OPENAI_MODEL")}
    out = subprocess.run([sys.executable, "verdict_cli.py", "run", "NCT1", "P1"],
                         cwd=REPO, capture_output=True, text=True, env=env)
    assert out.returncode != 0
    assert "OPENAI_ENDPOINT" in (out.stdout + out.stderr)
    assert "Traceback" not in out.stderr
