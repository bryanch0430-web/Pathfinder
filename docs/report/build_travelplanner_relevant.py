"""Build the TravelPlanner relevance-screening workbook.

Population: 1,225 official TravelPlanner queries.
Sample: 200 stratified by official source subset and difficulty.
Filter: single-city, 3/5 days, 1-4 travellers, no local hard constraints.
"""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / "docs" / "report" / "Pathfinder_TravelPlanner_Relevant.xlsx"
NAVY = "1F3A5F"
AMBER = "FFF2CC"
GREEN = "E2EFDA"
RED = "FCE4D6"
REVIEW = ["Review status", "Reviewer", "Review notes"]


def add_sheet(wb, title, headers, rows, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color="FFFFFF", bold=True, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = Font(size=9)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.row_dimensions[1].height = 30
    for i, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(header, 18)
    if "Review status" in headers:
        col = headers.index("Review status") + 1
        dv = DataValidation(type="list", formula1='"pending,keep,exclude,needs discussion"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(col)}2:{get_column_letter(col)}2000")


def has_local_constraint(value: str) -> bool:
    try:
        data = ast.literal_eval(value)
        return any(v not in (None, "", [], "None") for v in data.values())
    except Exception:
        return str(value).strip() not in ("", "{}", "None")


def local_text(value: str) -> str:
    try:
        data = ast.literal_eval(value)
        parts = [f"{k}: {v}" for k, v in data.items() if v not in (None, "", [], "None")]
        return "; ".join(parts) if parts else ""
    except Exception:
        return str(value)


def screening(row):
    reasons = []
    if has_local_constraint(row["local_constraint"]):
        reasons.append("local hard constraint unsupported")
    relevant = not reasons
    single = int(row["visiting_city_number"]) == 1
    small = int(row["people_number"]) <= 4
    corpus_ready = relevant and str(row["dest"]).strip() in {"New York", "San Francisco"}
    if not relevant:
        tier = ""
    elif single and small:
        tier = "A: core single-city"
    else:
        tier = "B: supported but secondary (multi-city and/or >4)"
    return relevant, "; ".join(reasons) if reasons else "relevant", corpus_ready, tier


def main() -> None:
    frames = []
    for source_split in ("train", "validation", "test"):
        frame = pd.read_csv(PROJECT / ".archify" / "travelplanner" / f"{source_split}.csv")
        frame["source_split"] = source_split
        frame["source_row"] = range(1, len(frame) + 1)
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    assert len(df) == 1225

    records = []
    for _, row in df.iterrows():
        relevant, reason, corpus_ready, tier = screening(row)
        records.append((relevant, reason, corpus_ready, tier))
    df["relevant"] = [x[0] for x in records]
    df["reason"] = [x[1] for x in records]
    df["corpus_ready"] = [x[2] for x in records]
    df["tier"] = [x[3] for x in records]

    # Stratified sample: 200, proportional rounded, seeded.
    rng = random.Random(20261123)
    groups = list(df.groupby(["source_split", "level"]))
    allocation = {key: max(1, round(len(group) / len(df) * 200)) for key, group in groups}
    by_key = {key: group for key, group in groups}
    while sum(allocation.values()) > 200:
        key = max(allocation, key=allocation.get)
        allocation[key] -= 1
    while sum(allocation.values()) < 200:
        key = max(allocation, key=lambda k: len(by_key[k]) - allocation[k])
        allocation[key] += 1
    sampled = []
    for key, group in groups:
        sampled.extend(rng.sample(list(group.index), min(allocation[key], len(group))))
    df["in_sample"] = df.index.isin(sampled)
    sample = df[df["in_sample"]].copy()

    relevant = df[df.relevant].copy()
    n = len(sample)
    p = sample.relevant.mean()
    z = 1.96
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    low, high = max(0, centre - margin), min(1, centre + margin)

    wb = Workbook()
    wb.remove(wb.active)

    # ---------------------------------------------------------------- README
    ws = wb.create_sheet("README")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "TravelPlanner relevance screen for Pathfinder v5"
    ws["A1"].font = Font(size=17, bold=True, color=NAVY)
    ws["A2"] = "One new workbook: population screen, 200-case stratified sample, and the full list of relevant cases."
    ws["A2"].font = Font(size=10, italic=True, color="7F6000")
    lines = [
        ("Population", "1,225 official TravelPlanner queries: 45 train + 180 validation + 1,000 test."),
        ("Why sample", "Full manual review is unnecessary for a rule that is computed from structured fields; a stratified sample checks that the filter behaves as intended."),
        ("Sample", "200 cases (16.3%), stratified by official source subset and difficulty, random seed 20261123."),
        ("Sample estimate", f"{int(sample.relevant.sum())}/{n} relevant ({p:.1%}); Wilson 95% CI {low:.1%}-{high:.1%}."),
        ("Interpretation", "This is a structured-field audit, not independent human annotation. It checks whether the operational filter behaves as intended; it is not a substitute for human gold labels."),
        ("Full filter", "No unsupported local hard constraint. This yields 423 structurally relevant cases."),
        ("Why these rules", "v5 can plan multi-city first slices and parties above four (the pipeline caps at 40; the UI lists up to 20). It does not guarantee restaurant cuisine, house rules, room type or no-flight constraints, so those remain excluded."),
        ("Tier A", "Core single-city, 1-4 travellers, no local constraint: 147 cases; 7 are corpus-ready (New York or San Francisco). Tier B adds 12 corpus-ready cases."),
        ("Tier B", "Supported but secondary focus: multi-city and/or more than four travellers; 276 cases. They carry more transport/coverage risk and should not be treated as equally mature Tier A cases."),
        ("Recommended order", "Run Tier A first; then Tier B only after the multi-city/transport path and provider coverage are confirmed. Do not report all 423 as one equally valid benchmark."),
        ("Date caveat", "TravelPlanner dates are 2022. These are static-planning cases, not live booking or forecast tests; dates are retained as official provenance."),
        ("Results", "No Pathfinder results are present. Every row is unrun reference material."),
        ("Review", "Use Review status: pending / keep / exclude / needs discussion."),
    ]
    r = 4
    for key, value in lines:
        ws.cell(r, 1, key).font = Font(bold=True, size=10)
        ws.cell(r, 2, value).font = Font(size=10)
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120

    # ---------------------------------------------------------------- summary
    add_sheet(wb, "Summary", ["Item", "Value"], [
        ["Population", 1225],
        ["Sample size", n],
        ["Sample relevant", int(sample.relevant.sum())],
        ["Sample relevance rate", f"{p:.1%}"],
        ["Sample Wilson 95% CI", f"{low:.1%}-{high:.1%}"],
        ["Full relevant", len(relevant)],
        ["Full relevance rate", f"{len(relevant)/len(df):.1%}"],
        ["Tier A: core single-city", int(df.tier.str.startswith("A").sum())],
        ["Tier B: supported but secondary", int(df.tier.str.startswith("B").sum())],
        ["Tier A corpus-ready", int((df.tier.str.startswith("A") & df.corpus_ready).sum())],
        ["Excluded: local hard constraint", int((~df.relevant).sum())],
    ], widths={"Item": 40, "Value": 20})

    # ---------------------------------------------------------------- sample
    sample_rows = []
    for _, row in sample.iterrows():
        sample_rows.append([
            f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}",
            row["source_split"], row["level"], row["org"], row["dest"], int(row["days"]),
            int(row["people_number"]), row["budget"], local_text(row["local_constraint"]),
            "yes" if row["relevant"] else "no", row["reason"], row["tier"],
            row["query"], "", "", "",
        ])
    add_sheet(wb, "Sample_Audit_200", ["Case UID", "Official subset", "Level", "Origin", "Destination", "Days", "People", "Budget", "Local constraints", "Relevant", "Reason", "Tier", "Query", *REVIEW], sample_rows,
              widths={"Case UID": 20, "Official subset": 12, "Level": 8, "Origin": 16, "Destination": 16, "Days": 6, "People": 6, "Budget": 8, "Local constraints": 38, "Relevant": 9, "Reason": 32, "Tier": 22, "Query": 95, "Review notes": 35})

    # ---------------------------------------------------------------- all screened
    all_rows = []
    for _, row in df.iterrows():
        all_rows.append([
            f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}",
            row["source_split"], row["level"], row["org"], row["dest"], int(row["days"]),
            int(row["people_number"]), row["budget"], local_text(row["local_constraint"]),
            "yes" if row["relevant"] else "no", row["reason"], row["tier"],
            "yes" if row["in_sample"] else "no", row["query"], "", "", "",
        ])
    add_sheet(wb, "All_1225_Screened", ["Case UID", "Official subset", "Level", "Origin", "Destination", "Days", "People", "Budget", "Local constraints", "Relevant", "Reason", "Tier", "In 200 sample", "Query", *REVIEW], all_rows,
              widths={"Case UID": 20, "Official subset": 12, "Level": 8, "Origin": 16, "Destination": 16, "Days": 6, "People": 6, "Budget": 8, "Local constraints": 38, "Relevant": 9, "Reason": 32, "Tier": 22, "In 200 sample": 10, "Query": 85, "Review notes": 35})

    # ---------------------------------------------------------------- relevant
    relevant_rows = []
    for _, row in relevant.iterrows():
        relevant_rows.append([
            f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}",
            row["source_split"], row["level"], row["org"], row["dest"], int(row["days"]),
            row["date"], int(row["people_number"]), row["budget"], row["query"],
            "yes" if row["corpus_ready"] else "no", row["tier"], "Not run - reference only",
            "", "", "",
        ])
    add_sheet(wb, "Relevant_Cases_423", ["Case UID", "Official subset", "Level", "Origin", "Destination", "Days", "Dates", "People", "Budget", "Query", "Corpus-ready", "Tier", "Run status", *REVIEW], relevant_rows,
              widths={"Case UID": 20, "Official subset": 12, "Level": 8, "Origin": 16, "Destination": 16, "Days": 6, "Dates": 30, "People": 6, "Budget": 8, "Query": 95, "Corpus-ready": 10, "Tier": 22, "Run status": 26, "Review notes": 35})

    wb._sheets = [wb["README"], wb["Summary"], wb["Sample_Audit_200"], wb["Relevant_Cases_423"], wb["All_1225_Screened"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"{OUT} full={len(df)} sample={len(sample)} relevant={len(relevant)} tierA={int(df.tier.str.startswith('A').sum())} tierB={int(df.tier.str.startswith('B').sum())}")


if __name__ == "__main__":
    main()
