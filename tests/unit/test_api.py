"""The two public APIs: satir and verdict."""
from __future__ import annotations

import subprocess
import sys

import pytest

import satir
import verdict


# ---------------------------------------------------------------- satir
def test_satir_exposes_the_pipeline():
    for name in ("config", "compile_trial", "compile_patient", "index",
                 "retrieve", "match"):
        assert name in satir.__all__ and callable(getattr(satir, name))


def test_satir_config_resolves():
    assert type(satir.config()).__name__ == "SatIRConfig"


def test_satir_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError):
        satir.definitely_not_a_function


def test_satir_does_not_eagerly_import_heavy_backends():
    """`import satir` must stay cheap; backends load only when used."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, satir;"
         "print([m for m in ('torch','elasticsearch','matplotlib','dspy','azure')"
         " if m in sys.modules])"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"eager imports: {out.stdout}"


# ---------------------------------------------------------------- verdict
def test_verdict_lists_its_variants():
    s = verdict.systems()
    assert "verdict" in s and "trialgpt" in s
    assert all(isinstance(v, str) and v for v in s.values())


def test_verdict_rejects_unknown_system():
    with pytest.raises(KeyError, match="unknown system"):
        verdict.match("a__b", system="not-a-system")


def test_verdict_defaults_to_strict(pair_data_available):
    """A pair that cannot be loaded must raise, not return 'ineligible'.

    This is the harmful direction for a trial matcher: a missing file must
    never read as a decision that the patient does not qualify.
    """
    with pytest.raises(verdict.MissingPairData):
        verdict.match("definitely__NCT0")


def test_verdict_lenient_mode_keeps_the_paper_sentinel(pair_data_available):
    if not pair_data_available:
        pytest.skip("no pair data")
    d = verdict.match("definitely__NCT0", strict=False)
    assert d.decision == "ineligible"
    assert d.is_missing_data and d.reasoning == verdict.NO_DATA


def test_verdict_match_and_explain_agree(pair_data_available):
    if not pair_data_available:
        pytest.skip("no pair data")
    pair = verdict.pairs()[0]
    d = verdict.match(pair)
    assert d.decision in ("eligible", "ineligible")
    assert d.decision.upper() in verdict.explain(pair)
