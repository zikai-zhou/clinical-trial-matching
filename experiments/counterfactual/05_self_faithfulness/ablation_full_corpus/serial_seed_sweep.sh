#!/bin/bash
# Sequential seed-sweep launcher: 16 parallel slices per config, one config at a time.
# Configs: (seed, cell) ∈ {1, 3} × {1, 2, 3}.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a

PY=<local-path>/.pyenv/versions/3.11.9/bin/python
DRIVER="$HERE/run_full_ablation.py"

SLICES=16
for SEED in 3; do
  for CELL in 1 2 3; do
    echo
    echo "=== launching SEED=$SEED CELL=$CELL ($SLICES slices) at $(date +%H:%M:%S) ==="
    pids=()
    for i in $(seq 0 $((SLICES-1))); do
      log="$HERE/logs/seed${SEED}_cell${CELL}.slice${i}of${SLICES}.log"
      AEGIS_COHORT=random AEGIS_COHORT_SEED=$SEED OUT_SUFFIX=_randcohort_seed${SEED} \
        CELL=$CELL SLICE="$i/$SLICES" SYSTEMS=aegis \
        OPENAI_ENDPOINT="$OPENAI_ENDPOINT" OPENAI_ENDPOINT_GPT5="$OPENAI_ENDPOINT_GPT5" OPENAI_API_KEY="$OPENAI_API_KEY" \
        nohup "$PY" -u "$DRIVER" >>"$log" 2>&1 &
      pids+=($!)
      sleep 0.1
    done
    # Wait for all 16 slices of this config to finish before moving on
    for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
    echo "=== done SEED=$SEED CELL=$CELL at $(date +%H:%M:%S) ==="
  done
done
echo
echo "ALL SEED SWEEP DONE at $(date +%H:%M:%S)"
