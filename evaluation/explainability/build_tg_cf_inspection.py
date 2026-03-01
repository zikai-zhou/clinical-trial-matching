"""Build inspection folder + Excel workbook for TG counterfactual probe.

Outputs:
  evaluation/results/TG_CF_INSPECTION/
    INSPECTION.xlsx  — workbook: summary + one sheet per pair
    per_pair/
      <pair>/
        00_README.md
        01_original_chart.txt
        02_cf_chart.txt
        03_tg_original_criteria.csv
        04_tg_cf_criteria.csv
        05_blockers_flipped.csv
        06_new_blockers.csv

Usage:
    python -m evaluation.explainability.build_tg_cf_inspection
"""
from __future__ import annotations
import json
import pathlib
import sys
import csv

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation" / "results"
CF_ROOT = RESULTS / "counterfactual_tg_blockers_20"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT_ROOT = RESULTS / "TG_CF_INSPECTION"
OUT_ROOT.mkdir(parents=True, exist_ok=True)
(OUT_ROOT / "per_pair").mkdir(exist_ok=True)


def find_orig_tg(pair):
    for shard in V3.glob("shard_*"):
        p = shard / pair / "trialgpt_decision.json"
        if p.exists():
            return json.load(open(p))
    return None


def find_cf_tg(pair):
    work = CF_ROOT / pair / "_work" / "_prompt_cache"
    try:
        inc_files = sorted((work / "trialgpt_inclusion").glob("*.json"))
        exc_files = sorted((work / "trialgpt_exclusion").glob("*.json"))
        if not inc_files or not exc_files:
            return None
        inc = json.load(open(inc_files[-1]))["payload"]
        exc = json.load(open(exc_files[-1]))["payload"]
        return {"inclusion": inc, "exclusion": exc}
    except Exception:
        return None


def rows_to_map(rows, criteria_list):
    """Map criterion_id → (criterion_text, label, reasoning)."""
    out = {}
    for r in rows:
        cid = r.get("criterion_id")
        label = (r.get("label") or "").lower()
        reasoning = r.get("reasoning", "")
        ct = criteria_list[cid] if (cid is not None and cid < len(criteria_list)) else "?"
        out[cid] = (ct, label, reasoning)
    return out


def main() -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    all_data = json.load(open(CF_ROOT / "all_results.json"))

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append([
        "Pair", "TG original", "TG after CF", "Flipped?",
        "# blockers TG flagged", "# blockers resolved on CF",
        "# new blockers on CF", "Note",
    ])
    for cell in summary[1]:
        cell.font = Font(bold=True)

    BOLD = Font(bold=True)
    GREEN = PatternFill(start_color="C7E8C7", end_color="C7E8C7", fill_type="solid")
    RED = PatternFill(start_color="F5C7C7", end_color="F5C7C7", fill_type="solid")
    YEL = PatternFill(start_color="FFF4C7", end_color="FFF4C7", fill_type="solid")

    for r in all_data:
        if r.get("error"):
            continue
        pair = r["pair"]
        pair_dir = OUT_ROOT / "per_pair" / pair
        pair_dir.mkdir(exist_ok=True)

        orig_chart = r.get("original_chart", "")
        cf_chart = r.get("tg_blocker_cf_chart", "")
        orig = r.get("original_labels", {}).get("tg", "?")
        cf = r.get("cf_labels", {}).get("tg", "?")
        flipped = r.get("cf_flips", {}).get("tg", False)

        (pair_dir / "01_original_chart.txt").write_text(orig_chart)
        (pair_dir / "02_cf_chart.txt").write_text(cf_chart)

        # Per-criterion comparison
        orig_tg = find_orig_tg(pair)
        cf_tg = find_cf_tg(pair)

        n_resolved = 0
        n_new_blockers = 0
        notes = []

        if orig_tg and cf_tg:
            for side in ("inclusion", "exclusion"):
                o_rows = orig_tg[side].get("rows", [])
                o_crit = orig_tg[side].get("criteria", [])
                cf_rows = cf_tg[side].get("rows", [])
                cf_crit = cf_tg[side].get("criteria", [])
                o_map = rows_to_map(o_rows, o_crit)
                c_map = rows_to_map(cf_rows, cf_crit)

                # Write per-side CSVs
                with open(pair_dir / f"03_tg_original_{side}.csv", "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["criterion_id", "criterion", "label", "reasoning"])
                    for cid, (ct, lbl, rs) in sorted(o_map.items(), key=lambda x: x[0] if x[0] is not None else -1):
                        w.writerow([cid, ct, lbl, rs])
                with open(pair_dir / f"04_tg_cf_{side}.csv", "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["criterion_id", "criterion", "label", "reasoning"])
                    for cid, (ct, lbl, rs) in sorted(c_map.items(), key=lambda x: x[0] if x[0] is not None else -1):
                        w.writerow([cid, ct, lbl, rs])

                # Count blockers resolved + new blockers
                for cid, (ct, o_lbl, _) in o_map.items():
                    blocker_orig = (side == "inclusion" and o_lbl == "not included") or \
                                   (side == "exclusion" and o_lbl == "excluded")
                    if cid not in c_map: continue
                    _, c_lbl, _ = c_map[cid]
                    blocker_cf = (side == "inclusion" and c_lbl == "not included") or \
                                 (side == "exclusion" and c_lbl == "excluded")
                    if blocker_orig and not blocker_cf:
                        n_resolved += 1
                    if not blocker_orig and blocker_cf:
                        n_new_blockers += 1

            if n_new_blockers > 0 and flipped is False:
                notes.append(f"{n_new_blockers} NEW blockers appeared on CF")
            if n_resolved > 0:
                notes.append(f"{n_resolved} original blockers resolved")

        # Write Excel per-pair sheet
        ws = wb.create_sheet(pair[:30])
        ws.append([f"Pair: {pair}"])
        ws.append([f"Original TG: {orig}  →  CF TG: {cf}  (flipped: {flipped})"])
        ws.append([])

        # For each side, dump before/after
        if orig_tg and cf_tg:
            for side in ("inclusion", "exclusion"):
                ws.append([f"=== {side.upper()} ==="])
                for cell in ws[ws.max_row]:
                    cell.font = BOLD
                ws.append(["cid", "criterion", "original label", "CF label", "change"])
                for cell in ws[ws.max_row]:
                    cell.font = BOLD

                o_rows = orig_tg[side].get("rows", [])
                o_crit = orig_tg[side].get("criteria", [])
                cf_rows = cf_tg[side].get("rows", [])
                cf_crit = cf_tg[side].get("criteria", [])
                o_map = rows_to_map(o_rows, o_crit)
                c_map = rows_to_map(cf_rows, cf_crit)
                all_cids = sorted(set(o_map) | set(c_map), key=lambda x: x if x is not None else -1)
                for cid in all_cids:
                    ot, ol, _ = o_map.get(cid, ("?", "?", ""))
                    ct, cl, _ = c_map.get(cid, ("?", "?", ""))
                    change = ""
                    fill = None
                    if ol != cl:
                        # Determine direction
                        blocker_o = (side == "inclusion" and ol == "not included") or (side == "exclusion" and ol == "excluded")
                        blocker_c = (side == "inclusion" and cl == "not included") or (side == "exclusion" and cl == "excluded")
                        if blocker_o and not blocker_c:
                            change = "RESOLVED"
                            fill = GREEN
                        elif not blocker_o and blocker_c:
                            change = "NEW BLOCKER"
                            fill = RED
                        else:
                            change = "CHANGED"
                            fill = YEL
                    row_idx = ws.max_row + 1
                    ws.append([cid, ot[:80], ol, cl, change])
                    if fill:
                        for cell in ws[row_idx]:
                            cell.fill = fill
                ws.append([])

        # Write README for pair
        readme = f"""# TG Counterfactual Inspection: {pair}

## Summary
- Original TG decision: {orig}
- After TG-blockers CF: {cf}
- Flipped? {flipped}
- Original blockers resolved: {n_resolved}
- New blockers appeared: {n_new_blockers}
- Notes: {'; '.join(notes) or 'none'}

## Files
- `01_original_chart.txt` — the patient chart before CF
- `02_cf_chart.txt` — the chart after GPT-4.1 rewrote to flip TG's flagged blockers
- `03_tg_original_{{side}}.csv` — TG's per-criterion output on the ORIGINAL chart
- `04_tg_cf_{{side}}.csv` — TG's per-criterion output on the CF chart

## Interpretation
"""
        if n_new_blockers > 0 and not flipped:
            readme += (
                "TG re-evaluated the CF chart and found NEW blockers that weren't "
                "blocking before. This is evidence that TG's decision is not "
                "stably driven by its originally-cited blockers."
            )
        elif flipped:
            readme += "TG's decision flipped — the CF successfully resolved its cited blockers."
        (pair_dir / "00_README.md").write_text(readme)

        # Summary row
        note_str = "; ".join(notes) if notes else ""
        summary.append([pair, orig, cf, "yes" if flipped else "no",
                        r.get("n_tg_blockers", ""), n_resolved, n_new_blockers, note_str])
        if not flipped:
            for cell in summary[summary.max_row]:
                cell.fill = RED
        else:
            for cell in summary[summary.max_row]:
                cell.fill = GREEN

    # Auto width
    for ws in wb.worksheets:
        for col in ws.columns:
            max_len = max((len(str(c.value)) for c in col if c.value is not None), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 80)

    wb.save(OUT_ROOT / "INSPECTION.xlsx")
    print(f"Wrote {OUT_ROOT}/INSPECTION.xlsx")
    print(f"Wrote {OUT_ROOT}/per_pair/ with {len(list((OUT_ROOT / 'per_pair').iterdir()))} pair folders")


if __name__ == "__main__":
    main()
