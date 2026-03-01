#!/bin/bash
# Launch all 3 cells × 8 slices = 24 background processes
# Usage:  bash launch_all.sh [CELL]   # optional: only one cell
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a

PY="${CMSRC_PY:-<local-path>/.pyenv/versions/3.11.9/bin/python}"
DRIVER="$HERE/run_full_ablation.py"
LOG_DIR="$HERE/logs"
mkdir -p "$LOG_DIR"

CELLS=${1:-"1 2 3"}
SLICES=${2:-16}

for CELL in $CELLS; do
  for i in $(seq 0 $((SLICES-1))); do
    log="$LOG_DIR/cell${CELL}.slice${i}of${SLICES}.log"
    echo "launch CELL=$CELL SLICE=$i/$SLICES -> $log"
    CELL=$CELL SLICE="$i/$SLICES" \
      OPENAI_ENDPOINT="$OPENAI_ENDPOINT" OPENAI_ENDPOINT_GPT5="$OPENAI_ENDPOINT_GPT5" OPENAI_API_KEY="$OPENAI_API_KEY" \
      AEGIS_JOINT="${AEGIS_JOINT:-0}" AEGIS_COHORT="${AEGIS_COHORT:-first}" AEGIS_COHORT_SEED="${AEGIS_COHORT_SEED:-0}" \
      SYSTEMS="${SYSTEMS:-aegis,v5,v5_cot,v5_cot_gpt5,v5_blockers,tg,shah}" \
      OUT_SUFFIX="${OUT_SUFFIX:-}" \
      CMSRC_DIR="${CMSRC_DIR:-}" CMSRC_PY="${CMSRC_PY:-}" \
      nohup "$PY" -u "$DRIVER" >"$log" 2>&1 &
    sleep 1
  done
done

echo
echo "launched. tail logs with:  tail -f $LOG_DIR/cell*.log"
echo "monitor with:  ps -ef | grep run_full_ablation"
wait
echo "all done"
