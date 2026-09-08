#!/usr/bin/env python3
"""Score clinician CSV responses against LLM gold + compute pairwise win-rate.

Inputs:
  --csv CLINICIAN1.csv [--csv CLINICIAN2.csv ...]
  Output of build_instrument.py: aegis_clinician_responses_*.csv
  Schema: order,pair,competitor_label,a_id,b_id,verdict,pairwise,note,ts

Outputs:
  - Per-clinician F1 vs LLM gold
  - Inter-rater Cohen's kappa on verdict (if 2+ clinicians)
  - Pairwise win-rate per matchup (AEGIS vs each competitor)
  - kappa(clinician majority, LLM gold)
"""
import argparse, csv, json, math, pathlib
from collections import defaultdict, Counter

ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')


def load_csv(p):
    rows = list(csv.DictReader(open(p)))
    return {r['pair']: r for r in rows if r.get('pair')}


def kappa(a_dict, b_dict):
    common = set(a_dict) & set(b_dict)
    if not common: return None, 0
    pa = pe = 0
    a_yes = b_yes = 0
    n = len(common)
    for k in common:
        ai = (a_dict[k].get('verdict') == 'eligible')
        bi = (b_dict[k].get('verdict') == 'eligible')
        if ai == bi: pa += 1
        if ai: a_yes += 1
        if bi: b_yes += 1
    p_obs = pa/n
    p_exp = (a_yes/n)*(b_yes/n) + ((n-a_yes)/n)*((n-b_yes)/n)
    return ((p_obs-p_exp)/(1-p_exp) if p_exp < 1 else 1.0), n


def metrics(pred_d, gold_d):
    """pred_d, gold_d: pair -> bool."""
    common = set(pred_d) & set(gold_d)
    tp=fp=fn=tn=0
    for p in common:
        a, g = pred_d[p], gold_d[p]
        if g and a: tp+=1
        elif g and not a: fn+=1
        elif not g and a: fp+=1
        else: tn+=1
    n=tp+fp+fn+tn
    P=tp/(tp+fp) if tp+fp else 0
    R=tp/(tp+fn) if tp+fn else 0
    F1=2*P*R/(P+R) if P+R else 0
    return n, (tp+tn)/n if n else 0, P, R, F1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', action='append', required=True, help='clinician CSV (repeat per clinician)')
    args = ap.parse_args()

    GOLD = {k: bool(v) for k,v in
            json.loads((ROOT/'experiments/accuracy/data/gold_5sys_freeform.json').read_text())['gold'].items()}

    clinicians = []
    for c in args.csv:
        clinicians.append({'name': pathlib.Path(c).stem, 'responses': load_csv(c)})

    print(f'\n=== {len(clinicians)} clinician(s) loaded ===')
    for c in clinicians:
        print(f'  {c["name"]}: {len(c["responses"])} pairs answered')

    # Per-clinician F1 vs LLM gold
    print('\n=== Per-clinician verdict vs LLM gold ===')
    print(f'{"clinician":<24}{"n":<5}{"acc":<8}{"P":<8}{"R":<8}{"F1":<8}')
    pred_dicts = []
    for c in clinicians:
        pred = {p: r['verdict']=='eligible' for p, r in c['responses'].items() if r.get('verdict')}
        pred_dicts.append(pred)
        n, acc, P, R, F1 = metrics(pred, GOLD)
        print(f'{c["name"]:<24}{n:<5}{acc:.3f}   {P:.3f}   {R:.3f}   {F1:.3f}')

    # Inter-rater κ
    if len(clinicians) >= 2:
        print('\n=== Inter-rater κ on verdict ===')
        for i in range(len(clinicians)):
            for j in range(i+1, len(clinicians)):
                k, n = kappa(clinicians[i]['responses'], clinicians[j]['responses'])
                if k is not None:
                    print(f'  {clinicians[i]["name"]} vs {clinicians[j]["name"]}: κ={k:.3f} (n={n})')

    # Clinician majority
    print('\n=== κ(clinician majority, LLM gold) ===')
    all_pairs = set()
    for c in clinicians: all_pairs |= set(c['responses'])
    majority = {}
    for p in all_pairs:
        votes = [c['responses'][p].get('verdict') for c in clinicians if p in c['responses'] and c['responses'][p].get('verdict')]
        if not votes: continue
        n_e = sum(1 for v in votes if v=='eligible')
        majority[p] = (n_e > len(votes)/2)
    n, acc, P, R, F1 = metrics(majority, GOLD)
    print(f'  clinician majority: n={n}  acc={acc:.3f}  F1 vs LLM gold={F1:.3f}')
    # κ
    k_dict_a = {p: ('eligible' if v else 'ineligible') for p, v in majority.items()}
    k_dict_b = {p: ('eligible' if GOLD.get(p) else 'ineligible') for p in majority if p in GOLD}
    k_obj_a = {p: {'verdict': k_dict_a[p]} for p in k_dict_a}
    k_obj_b = {p: {'verdict': k_dict_b[p]} for p in k_dict_b}
    k, n = kappa(k_obj_a, k_obj_b)
    if k is not None: print(f'  κ(clinician maj, LLM gold) = {k:.3f}  (n={n})')

    # Pairwise win-rate per matchup
    print('\n=== Pairwise rationale preference (clinician) ===')
    print('A/B order was randomized per case; we map back to AEGIS-vs-competitor.')
    matchup_outcomes = defaultdict(Counter)  # competitor_label -> Counter(AEGIS/competitor/tie)
    for c in clinicians:
        for pair, r in c['responses'].items():
            comp = r.get('competitor_label','?')
            a_id = r.get('a_id'); b_id = r.get('b_id')
            choice = r.get('pairwise')
            if not choice: continue
            if choice == 'tie':
                matchup_outcomes[comp]['tie'] += 1
            elif choice == 'A':
                if a_id == 'aegis': matchup_outcomes[comp]['AEGIS'] += 1
                else:               matchup_outcomes[comp]['other'] += 1
            elif choice == 'B':
                if b_id == 'aegis': matchup_outcomes[comp]['AEGIS'] += 1
                else:               matchup_outcomes[comp]['other'] += 1
    print(f'{"matchup":<24}{"AEGIS":<8}{"Other":<8}{"Tie":<6}{"AEGIS-win%":<12}')
    for comp, c in sorted(matchup_outcomes.items()):
        total = c['AEGIS']+c['other']+c['tie']
        if not total: continue
        rate = c['AEGIS']/total*100
        print(f'AEGIS vs {comp:<14}{c["AEGIS"]:<8}{c["other"]:<8}{c["tie"]:<6}{rate:.1f}%')

    # Notes
    print('\n=== Clinician notes (non-empty) ===')
    for c in clinicians:
        for pair, r in c['responses'].items():
            if r.get('note','').strip():
                print(f'  [{c["name"]}] {pair}: {r["note"][:200]}')


if __name__ == '__main__':
    main()
