#!/bin/bash
cd <local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored
set -a; source .env; set +a
exec <local-path>/.pyenv/versions/3.11.9/bin/python -u /tmp/revalidate_with_v3_simclin.py
