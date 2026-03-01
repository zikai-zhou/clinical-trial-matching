#!/usr/bin/env python3
"""Build the per-cell per-system per-outcome mbench drilldown.

Layout:
    mbench_3cell/
      <cell_tag>/
        <system>/
          flipped/<pair>/        — CF valid AND rejudged flipped to eligible
          not_flipped/<pair>/    — CF valid AND rejudged still ineligible
          invalid_cf/<pair>/     — CF generated but validator rejected
          skipped/<pair>/        — first-round eligible OR no blockers
          errored/<pair>/        — pipeline error
            00_summary.md
            01_first_round/{prompt.txt, response.json}
            02_blockers/{rationale.txt, blockers.json or targets.json}
            03_cf_generation/{chart_original.txt, chart_cf.txt}
            04_validator/{response.json}
            05_rejudged/{response.json, prompt_or_command.txt}

For AEGIS uses the random-cohort variant (seed=2) on cell 1; falls back to
first-cohort on cells 2/3 where random was not run.
"""
import json, pathlib, sys, re, shutil

HERE = pathlib.Path(__file__).resolve().parent
OUT_ROOT = HERE.parent / 'mbench_3cell'

# Source directory per (cell, system)
CELLS = ['cell1_4.1m_4.1v_filt', 'cell2_4.1m_v3v_filt', 'cell3_5m_v3v_filt']
# Default source: out/<cell>/ — contains all systems
# Override for v5_cot_gpt5: out/<cell>_v5cot5/
# Override for aegis on cell 1: out/<cell>_randcohort_seed2/
def source_for(cell_tag, system):
    """All systems source from the main cell dir (which uses random-cohort
    seed=2 for AEGIS by virtue of the post-truncation-fix rerun). The
    v5_cot_gpt5 system is in a parallel _v5cot5 directory."""
    if system == 'v5_cot_gpt5':
        return HERE/'out'/(cell_tag + '_v5cot5')
    return HERE/'out'/cell_tag

SYSTEMS = ['aegis', 'v5', 'v5_cot', 'v5_cot_gpt5', 'v5_blockers', 'tg', 'shah']

# Friendly directory names
SYSTEM_DIR_NAME = {
    'aegis': 'aegis_smt',
    'v5':    'llm_v5_gpt4.1',
    'v5_cot': 'llm_v5_cot_gpt4.1',
    'v5_cot_gpt5': 'llm_v5_cot_gpt5',
    'v5_blockers': 'llm_v5_blockers_gpt4.1',
    'tg':    'trialgpt_per_criterion',
    'shah':  'shah_koopman_binary',
}

def is_valid(v):
    if 'valid' in v: return bool(v.get('valid'))
    return bool(v.get('coherent') and v.get('flips_target_atom') and v.get('keeps_other_facts'))

def categorize(sd):
    if not sd: return None
    if sd.get('error'): return 'errored'
    if sd.get('skipped'): return 'skipped'
    v = sd.get('cf_validation') or {}
    if not is_valid(v): return 'invalid_cf'
    return 'flipped' if sd.get('rejudged',{}).get('eligibility')=='eligible' else 'not_flipped'

def load_pair_records(d, system):
    """{pair: sd} from a cell directory, preferring non-error/non-skip entries."""
    by_pair = {}
    for fp in d.glob('records.*of*.jsonl'):
        if 'merged' in fp.name: continue
        for ln in fp.open():
            try: r = json.loads(ln)
            except: continue
            p = r['pair']
            sd = (r.get('systems') or {}).get(system)
            if not sd: continue
            existing = by_pair.get(p)
            if existing and isinstance(existing, dict) and not existing.get('error'):
                continue
            by_pair[p] = sd
    return by_pair

def write_text(p, s):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s if isinstance(s, str) else json.dumps(s, indent=2, default=str))

def dump_pair(cell_dir, system, pair, sd, outcome):
    pair_dir = cell_dir / SYSTEM_DIR_NAME[system] / outcome / pair
    pair_dir.mkdir(parents=True, exist_ok=True)

    # 00_summary.md
    elig_before = 'ineligible (by published verdict)' if system != 'v5_cot_gpt5' else (
        sd.get('first_round',{}).get('eligibility','?'))
    elig_after = (sd.get('rejudged',{}) or {}).get('eligibility', '?')
    v = sd.get('cf_validation') or {}
    if 'valid' in v: valid_str = f"valid={v.get('valid')}, all_flipped={v.get('all_flipped')}, no_overcorrection={v.get('no_overcorrection')}"
    else: valid_str = f"coherent={v.get('coherent')}, flips_target={v.get('flips_target_atom')}, keeps_other={v.get('keeps_other_facts')}"
    extras = ''
    if system == 'aegis':
        extras = (f"\n- deciding cohort variant: `{sd.get('deciding_variant','?')}`\n"
                  f"- cohort mode: `{sd.get('cohort_mode','first')}`"
                  f"\n- joint maxsat: `{sd.get('use_joint_maxsat', False)}`"
                  f"\n- co-present atoms: {len(sd.get('co_present_atoms') or [])}\n")
    summary = (f"# {pair} — {system} — {outcome}\n\n"
               f"- system: **{system}**\n"
               f"- cell: `{cell_dir.name}`\n"
               f"- outcome: **{outcome}**\n"
               f"- first verdict: {elig_before}\n"
               f"- rejudged verdict: **{elig_after}**\n"
               f"- validator: {valid_str}\n"
               f"{extras}")
    write_text(pair_dir/'00_summary.md', summary)

    # 01_first_round
    first = pair_dir / '01_first_round'
    if system == 'aegis':
        # AEGIS first-round: from cmsrc; we don't have it stored in our records.
        # Just record what we know.
        write_text(first/'note.md',
                   "AEGIS first round comes from the cmsrc subprocess. We don't store "
                   "first-round prompts here — see `experiments/53_v2_full/cmsrc_out_REMINE_v9_full/` "
                   f"for `{sd.get('deciding_variant')}` outputs.")
    else:
        fr = sd.get('first_round') or {}
        write_text(first/'prompt.txt', sd.get('first_round',{}).get('prompt', '') or fr.get('sample_prompt', '') or '(no prompt stored)')
        # Strip the prompt out of response to avoid duplication
        clean = {k: v for k, v in fr.items() if k != 'prompt'}
        write_text(first/'response.json', clean)

    # 02_blockers (rationale/targets passed to modifier)
    blk = pair_dir / '02_blockers'
    if system == 'aegis':
        write_text(blk/'targets.json', sd.get('targets', []))
        write_text(blk/'preserve.json', sd.get('preserve', []))
        write_text(blk/'other_atoms_sample.json', sd.get('other_atoms', []))
    else:
        write_text(blk/'rationale.txt', sd.get('cited_rationale', '') or '(no rationale)')
        write_text(blk/'supports.txt', sd.get('supports', '') or '(no supports)')

    # 03_cf_generation
    cfg = pair_dir / '03_cf_generation'
    # original chart isn't stored in our records (only CF) — point to source
    pid = pair.split('__')[0]
    write_text(cfg/'chart_cf.txt', sd.get('cf_chart','') or '(no cf chart)')
    write_text(cfg/'where_to_find_original.md',
               f"Original chart: `dataset/clinical_trial/sigir/queries.jsonl` patient_id=`{pid}`")

    # 04_validator
    write_text(pair_dir/'04_validator'/'response.json', sd.get('cf_validation', {}))

    # 05_rejudged
    rejudged = sd.get('rejudged', {})
    if system == 'aegis':
        # aegis rejudge is a cmsrc subprocess; strip the giant raw_full
        slim = {k: v for k, v in rejudged.items() if k != 'raw_full'}
        write_text(pair_dir/'05_rejudged'/'response.json', slim)
        if 'raw_full' in rejudged:
            write_text(pair_dir/'05_rejudged'/'cmsrc_full.json', rejudged['raw_full'])
    else:
        write_text(pair_dir/'05_rejudged'/'response.json', rejudged)

def build_cell(cell_tag, mbench_root):
    cell_dir = mbench_root / cell_tag
    cell_dir.mkdir(parents=True, exist_ok=True)
    cell_summary_lines = [f'# {cell_tag} — system × outcome counts', '']
    for system in SYSTEMS:
        src = source_for(cell_tag, system)
        if not src.exists():
            print(f'  skip {system}: source {src} missing')
            continue
        records = load_pair_records(src, system)
        if not records:
            print(f'  skip {system}: no records in {src}')
            continue
        counts = {}
        for pair, sd in records.items():
            outcome = categorize(sd)
            if not outcome: continue
            counts[outcome] = counts.get(outcome, 0) + 1
            dump_pair(cell_dir, system, pair, sd, outcome)
        # Compute rate
        flipped = counts.get('flipped',0); not_flipped = counts.get('not_flipped',0)
        valid = flipped + not_flipped
        rate = (100*flipped/valid) if valid else 0
        line = f'- **{SYSTEM_DIR_NAME[system]}**: flipped={flipped} not_flipped={not_flipped} invalid_cf={counts.get("invalid_cf",0)} skipped={counts.get("skipped",0)} errored={counts.get("errored",0)} → **{rate:.1f}%** (flipped/valid)'
        cell_summary_lines.append(line)
        print(f'  {system}: {counts} → {rate:.1f}%')
    (cell_dir/'00_summary.md').write_text('\n'.join(cell_summary_lines))

def main():
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    overview = ['# 3-Cell × 7-System Self-Faithfulness Mbench', '',
                'Each cell uses a different (modifier × validator) combination.',
                'Each system was run on its own published-ineligible pair set.',
                '',
                '| cell | modifier | validator | rationale filter |',
                '|---|---|---|---|',
                '| cell1_4.1m_4.1v_filt | gpt-4.1 | gpt-4.1 itemized | blocker-filtered |',
                '| cell2_4.1m_v3v_filt | gpt-4.1 | gpt-5 v3 simclin | blocker-filtered |',
                '| cell3_5m_v3v_filt | gpt-5 | gpt-5 v3 simclin | blocker-filtered |',
                '',
                'Special variants:',
                '- `aegis_smt` on ALL cells uses **random cohort selection** (seed=2)',
                '  with the deterministic md5-based hash. Removes filesystem-order bias.',
                '- All systems use the **no-truncation** modifier/validator path',
                '  (criterion/chart_fact/rationale/trial passed full-length).',
                '- `llm_v5_cot_gpt5` is a new baseline: V5+CoT prompt run against gpt-5,',
                '  with `decisive_blockers` as the model-declared minimum-flip set.',
                '- `shah_koopman_binary` is the binary-forced Koopman prompt with',
                '  evidence_status field (deterministic_not_met → blocker, chart_silent → deferred).',
                '']
    for cell in CELLS:
        print(f'\n=== {cell} ===')
        build_cell(cell, OUT_ROOT)
        overview.append(f'\n## [{cell}]({cell}/00_summary.md)')
    (OUT_ROOT/'README.md').write_text('\n'.join(overview))
    print(f'\nwrote {OUT_ROOT}')

if __name__ == '__main__':
    main()
