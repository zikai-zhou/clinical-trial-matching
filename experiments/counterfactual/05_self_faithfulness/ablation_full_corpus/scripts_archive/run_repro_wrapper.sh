#!/bin/bash
cd <local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored
set -a; source .env; set +a
echo "ENDPOINT len=${#OPENAI_ENDPOINT} KEY len=${#OPENAI_API_KEY}"
exec <local-path>/.pyenv/versions/3.11.9/bin/python -u /tmp/rerun_gpt41_subset.py
