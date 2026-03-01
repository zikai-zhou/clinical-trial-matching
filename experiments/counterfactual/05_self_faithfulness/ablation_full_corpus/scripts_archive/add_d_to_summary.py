# Patch summarize_2x2.py to add a D column
import re, pathlib
p = pathlib.Path('/tmp/summarize_2x2.py')
src = p.read_text()
if 'snapshot D' in src.lower(): exit(0)
src = src.replace(
    "Cp = summarize(SF/'self_faithfulness_gpt5mod_filtered_subset.jsonl')",
    "Cp = summarize(SF/'self_faithfulness_gpt5mod_filtered_subset.jsonl')\nD  = summarize(SF/'self_faithfulness_gpt5modifier_with_v3_validator.jsonl',\n               valid_key='cf_valid_under_v3_validator', flipped_key='flipped_under_v3_validator')")
# Rewrite the header / row
src = src.replace(
    'print(\'='*120 + ")",
    "print('='*135)")
src = re.sub(
    r"header = .*\n",
    "header = f'{\"system\":<6} | {\"PUB\":>6}  {\"A (4.1m+4.1v)\":>15} {\"B (4.1m+v3v)\":>15} {\"C (5m+4.1v,full)\":>17} {\"C\\u2032 (5m+4.1v,filt)\":>20} {\"D (5m+v3v)\":>15}'\n",
    src, count=1)
src = re.sub(
    r"print\(f'\{s:<6\} \| \{pub:>5\.1f\}%  \{fmt\(A,s\):>15\} \{fmt\(B,s\):>15\} \{fmt\(C,s\):>18\} \{fmt\(Cp,s\):>20\}'\)",
    "print(f'{s:<6} | {pub:>5.1f}%  {fmt(A,s):>15} {fmt(B,s):>15} {fmt(C,s):>17} {fmt(Cp,s):>20} {fmt(D,s):>15}')",
    src, count=1)
src = src.replace(
    "print('C → C′ isolates: did \"full rationale\" or \"gpt-5 modifier\" cause shah’s +34pp jump in C?')",
    "print('C → C′ isolates: did full rationale or gpt-5 modifier cause shah\\'s +34pp jump in C?')\nprint('D : gpt-5 modifier + v3 simclin validator (C\\'s CFs re-validated with v3).')")
p.write_text(src)
print("patched")
