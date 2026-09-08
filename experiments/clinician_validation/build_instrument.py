#!/usr/bin/env python3
"""Build a self-contained HTML clinician validation instrument.

For 24 sampled pairs, embed: chart + criteria + 2 blinded rationales (AEGIS vs
one other system, A/B randomized). Clinician answers: verdict + pairwise winner.
Saves to localStorage; CSV export at end. No server required.
"""
import json, pathlib, random, re
ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')


def load_charts():
    return {json.loads(l)['_id']: json.loads(l).get('text','') or ''
            for l in (ROOT/'dataset/clinical_trial/sigir/queries.jsonl').open()}

def split_inc_exc(t):
    if not t: return '',''
    lo = t.lower()
    i = lo.find('inclusion criteria'); e = lo.find('exclusion criteria')
    inc = t[i:e] if i>=0 and e>i else (t[i:] if i>=0 else '')
    exc = t[e:] if e>=0 else ''
    return inc, exc

def load_trials():
    out={}
    for l in (ROOT/'dataset/clinical_trial/sigir/corpus.jsonl').open():
        try: o=json.loads(l)
        except: continue
        nct = o.get('_id') or o.get('id')
        text = o.get('text','') or ''
        out[nct] = {'text': text, 'title': (o.get('title') or '').strip()}
    return out

def load_freeform(p):
    out={}
    for l in pathlib.Path(p).open():
        try: o=json.loads(l)
        except: continue
        if o.get('pair'): out[o['pair']] = o.get('rationale','')
    return out

def main():
    sample = json.loads((ROOT/'experiments/clinician_validation/sample_24pairs.json').read_text())['pairs']
    rng = random.Random(20260509)

    charts = load_charts(); trials = load_trials()
    aegis_rat = load_freeform(ROOT/'matchers/systems/aegis/aegis_freeform.jsonl')
    v5_rat = load_freeform(ROOT/'matchers/systems/single_shot_llm/v5_freeform.jsonl')
    sbs_rat = load_freeform(ROOT/'matchers/systems/single_shot_llm/v5_verbose_v2_freeform.jsonl')
    tg_rat = load_freeform(ROOT/'backup/overnight/trialgpt_freeform.jsonl')
    shah_rat = load_freeform(ROOT/'backup/overnight/shahlab_freeform.jsonl')

    competitor_systems = ['v5','sbs','tg','shah']
    competitor_rats = {'v5': v5_rat, 'sbs': sbs_rat, 'tg': tg_rat, 'shah': shah_rat}
    competitor_labels = {'v5':'Single-Shot LLM','sbs':'Step-by-Step LLM','tg':'TrialGPT','shah':'Shah lab'}

    # Build 24 cases with rotated competitor assignment
    cases = []
    for i, pair in enumerate(sample):
        qid, nct = pair.split('__', 1)
        chart = charts.get(qid, '(missing)')
        parent = re.sub(r'(?<=NCT\d{8})[a-z]+$', '', nct)
        title = trials.get(parent, {}).get('title','')
        inc, exc = split_inc_exc(trials.get(parent, {}).get('text',''))

        # Rotate competitor: 6 of each
        comp = competitor_systems[i % 4]
        comp_rat = competitor_rats[comp].get(pair, '(missing)')
        aegis_r = aegis_rat.get(pair, '(missing)')

        # Random A/B
        if rng.random() < 0.5:
            a_id, b_id = 'aegis', comp
            a_rat, b_rat = aegis_r, comp_rat
        else:
            a_id, b_id = comp, 'aegis'
            a_rat, b_rat = comp_rat, aegis_r

        cases.append({
            'idx': i+1, 'pair': pair,
            'title': title[:300],
            'chart': chart,
            'inclusion': inc, 'exclusion': exc,
            'a_id': a_id, 'b_id': b_id,
            'a_rationale': a_rat, 'b_rationale': b_rat,
            'competitor_label': competitor_labels[comp],
        })

    rng.shuffle(cases)
    for i, c in enumerate(cases): c['order'] = i + 1

    html = build_html(cases)
    out = ROOT/'experiments/clinician_validation/clinician_instrument.html'
    out.write_text(html)
    print(f'wrote {out} ({len(cases)} cases)')


def html_escape(s):
    return (s or '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')


def build_html(cases):
    cases_json = json.dumps(cases)
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AEGIS Clinician Validation</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 0 20px; color: #222; line-height: 1.5; }
  h1 { color: #003c5b; }
  h2 { color: #003c5b; border-bottom: 2px solid #ccc; padding-bottom: 4px; }
  .progress { background: #eee; border-radius: 4px; height: 8px; margin: 10px 0; }
  .progress-bar { background: #4a90d9; height: 100%; border-radius: 4px; transition: width 0.3s; }
  .panel { border: 1px solid #ccc; border-radius: 6px; padding: 14px 18px; margin: 10px 0; background: #f8f9fa; }
  .chart { white-space: pre-wrap; font-size: 13px; max-height: 300px; overflow-y: auto; }
  .criteria { white-space: pre-wrap; font-size: 13px; max-height: 220px; overflow-y: auto; }
  .rationale { white-space: pre-wrap; font-size: 14px; padding: 14px; background: #fff; border: 1px solid #ddd; border-radius: 6px; min-height: 100px; }
  .rationale-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .rationale-pair > div { display: flex; flex-direction: column; }
  .rationale-pair label { font-weight: bold; margin-bottom: 6px; }
  .question { background: #fff8dc; padding: 12px 16px; border-radius: 6px; margin: 10px 0; border-left: 4px solid #d4a017; }
  .question p { margin: 6px 0; }
  .btn { padding: 8px 16px; border: 1px solid #ccc; border-radius: 4px; background: #fff; margin: 4px 4px 4px 0; cursor: pointer; font-size: 14px; }
  .btn:hover { background: #e0e8f0; }
  .btn.selected { background: #4a90d9; color: #fff; border-color: #4a90d9; }
  .navbar { display: flex; justify-content: space-between; align-items: center; margin-top: 20px; }
  .doctrine { background: #fff3cd; padding: 14px 18px; border-radius: 6px; margin: 10px 0; }
  textarea { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; font-family: inherit; font-size: 13px; min-height: 60px; }
  .summary { background: #d4edda; border: 1px solid #28a745; padding: 16px; border-radius: 6px; }
  #export-btn { padding: 12px 24px; background: #28a745; color: #fff; border: none; border-radius: 4px; font-size: 16px; cursor: pointer; }
</style>
</head>
<body>
<h1>AEGIS Clinician Validation</h1>
<p>This study compares clinical-trial eligibility-matching systems against your judgment.
<strong>~45 minutes, 24 pairs.</strong> All data is de-identified SIGIR-public.</p>

<div class="doctrine">
<strong>PRESCREEN-DOCTRINE policy</strong> (apply consistently to every case):
This is a prescreen task, not enrollment. Default to <strong>FORWARD</strong> on chart silence
(missing information will be obtained at the in-person visit). Reject only on
explicit chart contradiction OR defensible clinical inference. Treating chart silence
on a routinely-undocumented inclusion criterion as a blocker is a policy violation.
</div>

<div class="progress"><div class="progress-bar" id="progress" style="width:0%"></div></div>
<p id="progress-text">Pair 0 of 0</p>

<div id="case-area"></div>

<div id="export-area" style="display:none">
  <h2>Done — export your responses</h2>
  <p>Click below to download a CSV with your responses. Email the file back.</p>
  <button id="export-btn" onclick="exportCSV()">Download CSV</button>
</div>

<script>
const CASES = """ + cases_json + r""";
let currentIdx = 0;
let responses = JSON.parse(localStorage.getItem('aegis_clin_resp') || '{}');

function render() {
  if (currentIdx >= CASES.length) {
    document.getElementById('case-area').innerHTML = '';
    document.getElementById('export-area').style.display = 'block';
    document.getElementById('progress').style.width = '100%';
    document.getElementById('progress-text').textContent = 'Done — ' + CASES.length + ' of ' + CASES.length;
    return;
  }
  const c = CASES[currentIdx];
  const r = responses[c.pair] || {};
  document.getElementById('progress').style.width = ((currentIdx)/CASES.length*100) + '%';
  document.getElementById('progress-text').textContent = 'Pair ' + (currentIdx+1) + ' of ' + CASES.length;

  const area = document.getElementById('case-area');
  area.innerHTML = `
    <h2>Pair ${currentIdx+1} of ${CASES.length}: ${escapeHTML(c.title)}</h2>
    <div class="panel"><strong>Patient chart</strong><div class="chart">${escapeHTML(c.chart)}</div></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="panel"><strong>Inclusion criteria</strong><div class="criteria">${escapeHTML(c.inclusion)}</div></div>
      <div class="panel"><strong>Exclusion criteria</strong><div class="criteria">${escapeHTML(c.exclusion)}</div></div>
    </div>

    <div class="question">
      <p><strong>Q1:</strong> Under prescreen-doctrine (default forward on silence), is this patient
      eligible for the trial — should they be FORWARDED to in-person screening?</p>
      <button class="btn ${r.verdict==='eligible'?'selected':''}" onclick="setResp('${c.pair}','verdict','eligible')">Eligible (forward)</button>
      <button class="btn ${r.verdict==='ineligible'?'selected':''}" onclick="setResp('${c.pair}','verdict','ineligible')">Ineligible (reject)</button>
    </div>

    <h3>Two system rationales (blinded)</h3>
    <p>Each rationale was produced by a different matching system. Read both, then pick which
    you find more clinically defensible under prescreen-doctrine.</p>
    <div class="rationale-pair">
      <div><label>Rationale A</label><div class="rationale">${escapeHTML(c.a_rationale)}</div></div>
      <div><label>Rationale B</label><div class="rationale">${escapeHTML(c.b_rationale)}</div></div>
    </div>

    <div class="question">
      <p><strong>Q2:</strong> Which rationale is more clinically defensible under prescreen-doctrine?</p>
      <button class="btn ${r.pairwise==='A'?'selected':''}" onclick="setResp('${c.pair}','pairwise','A')">Rationale A</button>
      <button class="btn ${r.pairwise==='B'?'selected':''}" onclick="setResp('${c.pair}','pairwise','B')">Rationale B</button>
      <button class="btn ${r.pairwise==='tie'?'selected':''}" onclick="setResp('${c.pair}','pairwise','tie')">Tie</button>
    </div>

    <div class="question">
      <p><strong>Q3 (optional):</strong> One-line note if you disagree with the system or have a comment:</p>
      <textarea oninput="setResp('${c.pair}','note',this.value)">${escapeHTML(r.note||'')}</textarea>
    </div>

    <div class="navbar">
      <button class="btn" onclick="prev()" ${currentIdx===0?'disabled':''}>&larr; Previous</button>
      <button class="btn" onclick="next()">Next &rarr;</button>
    </div>
  `;
}

function escapeHTML(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function setResp(pair, key, value) {
  responses[pair] = responses[pair] || {};
  responses[pair][key] = value;
  responses[pair]['ts'] = new Date().toISOString();
  localStorage.setItem('aegis_clin_resp', JSON.stringify(responses));
  render();
}

function prev() { if (currentIdx>0) currentIdx--; render(); }
function next() { currentIdx++; render(); }

function exportCSV() {
  let csv = 'order,pair,competitor_label,a_id,b_id,verdict,pairwise,note,ts\n';
  for (const c of CASES) {
    const r = responses[c.pair] || {};
    csv += `${c.order},"${c.pair}","${c.competitor_label}","${c.a_id}","${c.b_id}","${r.verdict||''}","${r.pairwise||''}","${(r.note||'').replace(/"/g,'""')}","${r.ts||''}"\n`;
  }
  const blob = new Blob([csv], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'aegis_clinician_responses_' + new Date().toISOString().slice(0,16).replace(/[:T]/g,'-') + '.csv';
  a.click();
}

render();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    main()
