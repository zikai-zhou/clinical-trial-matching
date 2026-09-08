"""LLM-based CF chart validator.

Given (original_chart, cf_chart, cited_facts), asks an LLM whether each cited
fact was actually flipped in the rewrite. Used to filter out CF generations
that didn't faithfully apply the requested edits.
"""
from __future__ import annotations
import json, os, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
ENDPOINT = os.environ.get('OPENAI_ENDPOINT', '')
KEY      = os.environ.get('OPENAI_API_KEY', '')

PROMPT = (ROOT/'experiments/counterfactual/prompts/cf_validator.prompt').read_text()


def _llm(prompt: str, max_tokens: int = 1500) -> str:
    body = json.dumps({
        'messages': [{'role':'user','content':prompt}],
        'max_tokens': max_tokens, 'temperature': 0,
        'response_format': {'type': 'json_object'},
    }).encode()
    req = urllib.request.Request(
        f"{ENDPOINT}/chat/completions?api-version=2024-08-01-preview",
        data=body, headers={'api-key': KEY, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''


def fmt_aegis_targets(targets: list) -> str:
    out = []
    for t in targets:
        readable = t['atom'].replace('_', ' ').replace('@@', ' qualified by ')
        out.append(f'- atom: `{t["atom"]}`\n  meaning: "{readable}"\n  current value: {t.get("current_value")}\n  target value: {t.get("target_value")}')
    return '\n'.join(out)


def fmt_text_blockers(rationale: str, blocker_lines: list = None) -> str:
    """Format NL rationale + extracted blocker lines for the validator."""
    parts = []
    if blocker_lines:
        parts.append('Specific blocker statements cited by the system:')
        for ln in blocker_lines: parts.append(f'  - {ln}')
    parts.append('\nFull system rationale:')
    parts.append(rationale)
    return '\n'.join(parts)


def fmt_preserve_atoms(preserve: list) -> str:
    """Format AEGIS preserve atoms for the validator."""
    if not preserve: return '_(no PRESERVE list — validator should rely on trial-criteria reasoning only)_'
    out = []
    for p in preserve:
        atom = p.get('atom','')
        readable = atom.replace('_',' ').replace('@@', ' qualified by ')
        cv = p.get('current_value')
        ev = (p.get('evidence','') or '')
        line = f"- `{atom}` = `{cv}`  meaning: \"{readable}\""
        if ev: line += f"\n  chart evidence to preserve: \"{ev}\""
        out.append(line)
    return '\n'.join(out)


def validate(original_chart: str, cf_chart: str, cited_facts_text: str,
             preserve_facts_text: str | None = None) -> dict:
    """Returns dict with flip + overcorrection signals + composite `valid`.

    Keys: items, all_flipped, overcorrection_issues, no_overcorrection, valid,
          summary, n_flipped, n_total.
    """
    if not cf_chart or cf_chart == original_chart:
        return {'items': [], 'all_flipped': False, 'overcorrection_issues': [],
                'no_overcorrection': False, 'valid': False,
                'summary': 'CF chart is empty or identical to original',
                'n_flipped': 0, 'n_total': 0}
    prompt = (PROMPT
              .replace('{{ORIGINAL_CHART}}', original_chart)
              .replace('{{CF_CHART}}', cf_chart)
              .replace('{{CITED_FACTS}}', cited_facts_text or '')
              .replace('{{PRESERVE_FACTS}}', preserve_facts_text or '_(none specified)_'))
    try:
        txt = _llm(prompt)
        o = json.loads(txt)
    except Exception as e:
        return {'items': [], 'all_flipped': False, 'overcorrection_issues': [],
                'no_overcorrection': False, 'valid': False,
                'summary': f'validator err: {e}',
                'n_flipped': 0, 'n_total': 0, 'error': str(e)[:200]}
    items = o.get('items') or []
    overcorr = o.get('overcorrection_issues') or []
    n_total = len(items)
    n_flipped = sum(1 for it in items if it.get('flipped'))
    all_flipped = bool(o.get('all_flipped'))
    no_overcorr = bool(o.get('no_overcorrection'))
    # Trust the LLM's `valid` if provided, else derive
    valid = o.get('valid')
    if valid is None: valid = all_flipped and no_overcorr
    return {
        'items': items,
        'all_flipped': all_flipped,
        'overcorrection_issues': overcorr,
        'no_overcorrection': no_overcorr,
        'valid': bool(valid),
        'summary': o.get('summary',''),
        'n_flipped': n_flipped, 'n_total': n_total,
    }
