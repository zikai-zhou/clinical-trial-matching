#!/bin/bash
cd /Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored
while true; do
  clear
  echo "═══ CF v3 run — $(date +%H:%M:%S) ═══"
  for f in experiments/counterfactual/05_self_faithfulness/out/v3/*.jsonl; do
    n=$(wc -l < "$f" 2>/dev/null || echo 0)
    last=$(tail -1 "$f" 2>/dev/null | python3 -c "
import sys,json
try:
    r=json.loads(sys.stdin.read())
    v=r.get('validator',{})
    valid=v.get('coherent') is True and v.get('keeps_other_facts') is True
    print(f'  last={r[\"pair\"][:35]:35}  verdict={r.get(\"rejudge_verdict\",\"?\"):11}  valid_cf={valid}')
except: print('  (empty)')" 2>/dev/null)
    printf "  %-22s %3d  %s\n" "$(basename $f)" "$n" "$last"
  done
  echo
  echo "process: $(ps aux | grep run_v3_clean | grep -v grep | wc -l | tr -d ' ') running"
  sleep 4
done
