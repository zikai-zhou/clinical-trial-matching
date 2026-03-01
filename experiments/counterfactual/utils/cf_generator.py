"""LLM-driven CF chart generators.

Two flavors:
  generate_cf_from_targets(chart, targets):  for AEGIS — uses atom-value targets
  generate_cf_from_rationale(chart, trial, rationale_text):  for V5/TG/Stanford
"""
from __future__ import annotations
import json, os, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
KEY  = os.environ.get('OPENAI_API_KEY', '')
# CF_MODIFIER_MODEL env var:
#   "gpt-4.1" (default — original published path)
#   "gpt-5"   (uses OPENAI_ENDPOINT_GPT5 — for ablation C in the 2x2 study)
CF_MOD_MODEL = os.environ.get('CF_MODIFIER_MODEL', 'gpt-4.1')
if CF_MOD_MODEL == 'gpt-5':
    EP_GPT5 = os.environ.get('OPENAI_ENDPOINT_GPT5') or os.environ.get('OPENAI_ENDPOINT', '')
    _ENDPOINT_BASE = EP_GPT5.split('/openai/')[0] if EP_GPT5 else ''
else:
    _ENDPOINT_BASE = os.environ.get('OPENAI_ENDPOINT', '')
ENDPOINT = _ENDPOINT_BASE  # back-compat for any callers reading the module-level var

PROMPT_AEGIS = (ROOT/'experiments/counterfactual/prompts/cf_aegis_targets.prompt').read_text()
PROMPT_TEXT  = (ROOT/'experiments/counterfactual/prompts/cf_text_blockers.prompt').read_text()


def _llm(prompt: str, max_tokens: int = 2500) -> str:
    if CF_MOD_MODEL == 'gpt-5':
        body = json.dumps({
            'model': 'gpt-5',
            'messages': [{'role':'user','content':prompt}],
            'max_completion_tokens': max_tokens + 4000,  # gpt-5 reasoning overhead
        }).encode()
        url = f"{_ENDPOINT_BASE}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview"
    else:
        body = json.dumps({
            'messages': [{'role':'user','content':prompt}],
            'max_tokens': max_tokens, 'temperature': 0,
        }).encode()
        url = f"{_ENDPOINT_BASE}/chat/completions?api-version=2024-08-01-preview"
    req = urllib.request.Request(url, data=body,
        headers={'api-key': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''


def generate_cf_from_targets(chart: str, targets: list, preserve: list = None,
                              other_atoms: list = None) -> str:
    """targets: list of {atom, target_value, evidence?, assessment?}
    preserve: list of {atom, current_value, evidence, assessment} — explicit
              must-stay-supported list (MaxSat kept). Validator-checked.
    other_atoms: list of {atom, current_value, evidence, assessment} for ALL
              non-target atoms (incl. NULL/silent). Rewriter is asked to keep
              these invariant on re-mine — important for silent-qualifier
              cascading-blocker prevention."""
    if not targets: return chart
    target_lines = []
    for t in targets:
        atom = t['atom']
        tv = t.get('target_value')
        readable_atom = atom.replace('_', ' ').replace('@@', ' qualified by ')
        line = f"- atom `{atom}`: target value = {tv}\n  meaning: \"{readable_atom}\""
        if t.get('assessment'):
            line += f"\n  current evidence/assessment: {t['assessment']}"
        target_lines.append(line)
    preserve_lines = []
    for p in (preserve or []):
        atom = p['atom']; readable = atom.replace('_', ' ').replace('@@', ' qualified by ')
        cv = p.get('current_value')
        line = f"- atom `{atom}` = `{cv}` (KEEP THIS)\n  meaning: \"{readable}\""
        if p.get('evidence'): line += f"\n  chart evidence to preserve: \"{(p['evidence'] or '')}\""
        preserve_lines.append(line)
    preserve_block = ('\n'.join(preserve_lines)
                      if preserve_lines else '_(no atoms to preserve specified)_')
    other_lines = []
    for a in (other_atoms or []):
        atom = a['atom']; readable = atom.replace('_', ' ').replace('@@', ' qualified by ')
        cv = a.get('current_value')
        if cv is None: cv_label = 'NULL (silent in original chart — must remain silent)'
        else: cv_label = f'`{cv}`'
        line = f"- `{atom}` = {cv_label}  ({readable})"
        other_lines.append(line)
    other_block = ('\n'.join(other_lines)
                   if other_lines else '_(no other atoms specified)_')
    prompt = (PROMPT_AEGIS
              .replace('{{CHART}}', chart)
              .replace('{{TARGETS}}', '\n'.join(target_lines))
              .replace('{{PRESERVE}}', preserve_block)
              .replace('{{OTHER_ATOMS}}', other_block))
    return _llm(prompt, max_tokens=3000).strip()


def generate_cf_from_rationale(chart: str, trial: str, rationale: str,
                                supports: str | None = None) -> str:
    """Used for systems with NL rationales (V5, TG, Shahlab).
    supports: optional NL block describing inclusion-supporting facts to preserve."""
    if not rationale: return chart
    sup = supports if supports else '_(no explicit support list — preserve any chart content that supports an inclusion criterion)_'
    prompt = (PROMPT_TEXT
              .replace('{{CHART}}', chart)
              .replace('{{TRIAL}}', trial or '')
              .replace('{{RATIONALE}}', rationale)
              .replace('{{SUPPORTS}}', sup))
    return _llm(prompt).strip()
