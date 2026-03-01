import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path("experiments/counterfactual").resolve()))
sys.path.insert(0, str(pathlib.Path("experiments").resolve()))
import utils.cf_judge as J
from utils.cf_judge import judge_aegis
ROOT = pathlib.Path(".").resolve()
PROMPT_ROOT = ROOT/"experiments/53_v2_full/inputs/prompt_root/prompt_out"
PROMPT_MAP = PROMPT_ROOT/"prompt_map.json"
SELFFAITH = ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"

sf = {r["pair"]: r for r in (json.loads(l) for l in SELFFAITH.open())}
remine = [json.loads(l) for l in open("/tmp/aegis_symbolic_numeric.jsonl")]
valid = [r for r in remine if r.get("status")=="ok" and r.get("cf_valid") and r.get("cf_chart")]
# pick first 2 to pilot
for rec in valid[:2]:
    pair = rec["pair"]
    jr = judge_aegis(pair, rec["deciding_variant"], rec["cf_chart"], PROMPT_ROOT, PROMPT_MAP)
    print(f"pair={pair}")
    print(f"  eligibility: {jr.get('eligibility')!r}")
    print(f"  rationale: {(jr.get('rationale','') or '')[:200]}")
