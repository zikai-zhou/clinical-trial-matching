#!/bin/bash
cd /Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored
set -a; source .env; set +a
export CF_MODIFIER_MODEL=gpt-5
exec /Users/xyrus/.pyenv/versions/3.11.9/bin/python -u /tmp/rerun_snapshot_C_filtered.py
