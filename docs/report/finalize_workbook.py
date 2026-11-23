"""Finalise the single evaluation set: no train/test split, plus a one-page test list."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / "docs" / "report" / "Pathfinder_Test_Cases_Review.xlsx"
NAVY = "1F3A5F"
AMBER = "FFF2CC"


def set_header(ws, old: str, new: str) -> None:
    for cell in ws[1]:
        if cell.value == old:
            cell.value = new
            return


def set_values(ws, header: str, value: str) -> None:
    headers = [c.value for c in ws[1]]
    if header not in headers:
        return
    col = headers.index(header) + 1
    for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
        row[0].value = value


wb = load_workbook(OUT)

# Pathfinder suites: one frozen evaluation set, no dev/heldout labels.
for sheet in ("Routing_84", "Planner_17", "Edit_vs_Replan_5"):
    ws = wb[sheet]
    set_header(ws, "Split", "Set")
    set_values(ws, "Set", "v5 evaluation")

# All-cases view: one set label, reference for external cases.
ws = wb["All_Cases"]
set_header(ws, "Split/Group", "Set")
headers = [c.value for c in ws[1]]
set_idx = headers.index("Set") + 1
for row in ws.iter_rows(min_row=2):
    if row[1].value == "TravelPlanner":
        row[set_idx - 1].value = "external reference"
    else:
        row[set_idx - 1].value = "v5 evaluation"

# TravelPlanner: source subsets are provenance, not our train/test split.
ws = wb["TravelPlanner_1225"]
set_header(ws, "Split", "Source")
set_values(ws, "Source", "external reference")
# Keep the original row number; add a clear provenance note in README.

# Add the final one-page test list.
ws = wb.create_sheet("Final_Test_List")
rows = [
    ["Routing_84", "Routing", 84, "Typed intent: plan / modify / ask / clarify", "P0", "Pending retest", "84 frozen utterances; no training split"],
    ["Planner_17", "Planning", 17, "Sourced itinerary satisfying hard constraints", "P0", "Pending retest", "Hong Kong 8 + Japan 6 + TravelPlanner-style 3"],
    ["Verifier_17", "Constraint audit", 17, "pass / fail / unknown per constraint", "P0", "Pending retest", "3 exact duplicates removed from the old 20"],
    ["Editor_8", "Edit", 8, "Typed edit + non-target preservation", "P0", "Pending retest", "Duplicate ed06 replaced"],
    ["Ask_12", "Grounded QA", 12, "Answer, gap, scope or injection refusal", "P0", "Pending retest", "11 in-scope + q11 scope + q12 injection"],
    ["Memory_8", "Preference memory", 8, "Stated preferences only; no invention", "P1", "Pending retest", "No-fact checks retained"],
    ["Efficiency_10", "Cost/latency", 10, "Calls, tokens and latency per route", "P1", "Pending retest", "Overlaps routing intentionally"],
    ["Edit_vs_Replan_5", "Edit cost", 5, "Preserve non-target stops; no replan", "P0", "Pending retest", "Paired replan arm still missing"],
    ["Injection_84", "Security", 84, "No prompt/canary leak; no task hijack", "P0", "Pending retest", "84 probes across channels"],
    ["Cap_Stress", "Robustness", 10, "Safe degradation at low caps", "P1", "Pending retest", "10 aggregate conditions"],
    ["TravelPlanner_1225", "External planning", 1225, "Official TravelPlanner constraints", "P2", "Reference only - not run", "45 + 180 + 1,000 cases; no Pathfinder run claimed"],
    ["TOTAL", "", 1480, "", "", "", "255 v5 evaluation cases + 1,225 external reference cases"],
]
ws.append(["Suite", "Construct", "Cases", "Expected result", "Priority", "Status", "Notes"])
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
for r in range(2, ws.max_row + 1):
    if ws.cell(r, 6).value == "Pending retest":
        ws.cell(r, 6).fill = PatternFill("solid", fgColor=AMBER)
ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions
for i, width in enumerate([22, 18, 8, 45, 9, 24, 62], start=1):
    ws.column_dimensions[get_column_letter(i)].width = width

# README: explain no train/test split.
ws = wb["README"]
r = ws.max_row + 2
for key, value in [
    ("Set design", "There is no train/test split. All 255 Pathfinder cases are one frozen evaluation set; the 1,225 TravelPlanner cases are an external reference set and are not used for training."),
    ("Final list", "See Final_Test_List for the complete suite-level list, counts, constructs, priority and status."),
    ("Priority", "P0 = required for the next report; P1 = needed for robustness; P2 = external/future work."),
]:
    ws.cell(r, 1, key).font = Font(bold=True, size=10)
    ws.cell(r, 2, value).font = Font(size=10)
    ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
    r += 1

wb._sheets = [wb["README"], wb["Final_Test_List"], wb["Index"], wb["Changes"], wb["Model_Settings"], wb["Temperature_Ablation"], wb["Router_Comparison_84"], wb["Pricing_Evidence"], wb["All_Cases"], wb["Routing_84"], wb["Planner_17"], wb["Verifier_17"], wb["Editor_8"], wb["Ask_12"], wb["Memory_8"], wb["Efficiency_10"], wb["Edit_vs_Replan_5"], wb["Injection_84"], wb["Cap_Stress"], wb["TravelPlanner_1225"]]
wb.save(OUT)
print(f"finalised {OUT}")
