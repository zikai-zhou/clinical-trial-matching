# scripts_archive/

Working scripts used during the 2x2 / full-corpus ablation runs. These were
originally created under `/tmp` and have been preserved here for traceability.

Most are one-off scripts:
- `fix_v3_validity_flag.py` — recomputes `cf_valid_under_v3_validator =
  coherent AND flips_target_atom AND keeps_other_facts`. Apply this to any v3
  validator output before computing flip rates.
- `build_mbench_gpt5mod.py` — builds an mbench drilldown directory from a
  records.jsonl produced by the gpt-5 modifier.
- `aegis_rejudge_311.py` / `aegis_rejudge_v2.py` — wrap the cmsrc matcher
  subprocess to rejudge a list of (pair, cf_chart) cells under python 3.11.9.
- `cf_modifier_gpt5.py` / `cf_modifier_gpt5_rationale.py` — earlier gpt-5
  modifier wrappers used before `counterfactual_modifier/atom_target_modifier.py`
  was generalized.
- `rejudge_*.py` — rejudge variants under various validators / age fixes.
- `fix_smt_numeric_targets.py` / `fullcorpus_age_fix.py` — patch numeric
  blocker targets after the Z3 sat_values feature was added.

The shell scripts (`run_slice_*.sh`, `run_snapshot_*.sh`, `watch_v3.sh`) are
the original launchers for the 2x2 snapshots.
