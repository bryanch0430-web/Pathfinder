"""Add the experiment-design sheets that were missing from the case-only workbook."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / "docs" / "report" / "Pathfinder_Test_Cases_Review.xlsx"
ROUTING = [json.loads(x) for x in (PROJECT / "backend" / "eval" / "fixtures" / "routing.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

NAVY = "1F3A5F"
AMBER = "FFF2CC"
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
    for idx, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = (widths or {}).get(header, 18)
    if "Review status" in headers:
        col = headers.index("Review status") + 1
        dv = DataValidation(type="list", formula1='"pending,pass,fail,needs discussion"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(col)}2:{get_column_letter(col)}1000")
    if "Test status" in headers:
        col = headers.index("Test status") + 1
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            row[0].fill = PatternFill("solid", fgColor=AMBER)
    return ws


wb = load_workbook(OUT)

# ---------------------------------------------------------------- model settings
model_rows = [
    ["Writer", "Claude Sonnet 5.5", 0.2, 4096, "none", "Composes the traveller-facing reply and holds the long typed contract.",
     "Best structured-output reliability in the current profile; not proven optimal by a temperature sweep.", "Gemini Flash-Lite; DeepSeek Flash", "Current default - retest"],
    ["Analyst / router", "Claude Sonnet 5.5", 0.2, 4096, "none", "Returns typed intent, slots, constraints and clarification.",
     "Highest routing accuracy in the frozen study; 0.0 and 0.2 tied on the current prompt.", "Flash-Lite; Jev", "Current default - retest"],
    ["Planner / composer", "Claude Sonnet 5.5", 0.2, 12000, "none", "Selects sourced candidates and writes the day plan.",
     "Planner output is long and structured; 0.2 is a production default, not a swept optimum.", "Flash-Lite", "Current default - retest"],
    ["Editor", "Claude Sonnet 5.5", 0.2, 2000, "none", "Returns typed edit operations against an existing plan.",
     "Preservation matters more than novelty; 0.0 is the safer retest arm.", "DeepSeek Flash; Flash-Lite", "Current default - retest"],
    ["Ask", "Claude Sonnet 5.5", 0.2, 1400, "none", "Answers from the plan and sources with optional bounded search.",
     "0.2 is current; no valid temperature sweep yet.", "DeepSeek Flash", "Current default - retest"],
    ["Memory", "Claude Sonnet 5.5", 0.2, 700, "none", "Extracts stated preferences only.",
     "0.2 is current; the memory suite is small and not a temperature study.", "DeepSeek Flash", "Current default - retest"],
    ["Constraint verifier", "DeepSeek Flash", 0.0, 1500, "none requested", "Audits each hard constraint as pass/fail/unknown.",
     "Independent model family; 0 is chosen for deterministic audit output. The endpoint may still emit reasoning_content.", "Claude Sonnet; disable audit", "Current default - retest"],
    ["Jev router (optional)", "Jev", None, None, "n/a", "Optional decision model behind the router flag.",
     "Off by default; fast but lower routing accuracy in the frozen study.", "LLM analyst", "Optional - not default"],
    ["Web search", "SerpAPI -> Wikipedia -> DeepSeek Search", None, None, "n/a", "Code-executed search; the LLM writes only the query.",
     "Code owns execution and the 2 x 4 cap; not a model-temperature setting.", "n/a", "Current chain - retest"],
]
add_sheet(wb, "Model_Settings", ["Part", "Current model", "Temperature", "Max output tokens", "Reasoning setting", "Role in the turn", "Why this setting", "Alternative(s)", "Test status", *REVIEW],
          [row + ["", "", ""] for row in model_rows], widths={"Part": 20, "Current model": 24, "Temperature": 11, "Max output tokens": 12, "Reasoning setting": 16, "Role in the turn": 45, "Why this setting": 70, "Alternative(s)": 28, "Test status": 24, "Review notes": 35})

# ---------------------------------------------------------------- temperature ablation
temperature_rows = []
for part, models, caps in [
    ("understand", ["claude-sonnet-5.5", "gemini-3.5-flash-lite", "deepseek-flash"], "4096"),
    ("planner", ["claude-sonnet-5.5", "gemini-3.5-flash-lite"], "12000"),
    ("editor", ["claude-sonnet-5.5", "deepseek-flash"], "2000"),
    ("ask", ["claude-sonnet-5.5", "deepseek-flash"], "1400"),
    ("memory", ["claude-sonnet-5.5", "deepseek-flash"], "700"),
    ("verify", ["deepseek-flash", "claude-sonnet-5.5"], "1500"),
]:
    for model in models:
        for temperature in (0.0, 0.2):
            temperature_rows.append([
                part, model, temperature, caps, "same prompt; same frozen cases",
                "Measure correctness, p50 latency, output tokens and cost at each temperature.",
                "Keep the current setting unless the retest shows a material, reproducible difference.",
                "Pending retest", "", "", "",
            ])
add_sheet(wb, "Temperature_Ablation", ["Part", "Model", "Temperature", "Cap", "Control", "Hypothesis / metric", "Decision rule", "Test status", *REVIEW],
          temperature_rows, widths={"Part": 16, "Model": 24, "Temperature": 11, "Cap": 8, "Control": 28, "Hypothesis / metric": 55, "Decision rule": 55, "Test status": 18, "Review notes": 35})

# ---------------------------------------------------------------- router comparison
router_rows = []
for r in ROUTING:
    router_rows.append([
        r["id"], r.get("split", ""), r["input"], r["expected_intent"],
        "Jev: pending", "Flash-Lite 0.0: pending", "Sonnet 0.0: pending", "Sonnet 0.2: pending",
        "Compare exact intent accuracy, p50 latency, token/call count and estimated cost; report a paired comparison.",
        "Pending retest", "", "", "",
    ])
add_sheet(wb, "Router_Comparison_84", ["Case ID", "Split", "Input", "Expected route", "Jev", "Flash-Lite 0.0", "Sonnet 0.0", "Sonnet 0.2", "Comparison rule", "Test status", *REVIEW],
          router_rows, widths={"Case ID": 9, "Split": 8, "Input": 55, "Expected route": 12, "Jev": 18, "Flash-Lite 0.0": 18, "Sonnet 0.0": 18, "Sonnet 0.2": 18, "Comparison rule": 55, "Test status": 16, "Review notes": 35})

# ---------------------------------------------------------------- pricing
pricing_rows = [
    ["Claude Sonnet 5.5", 2.00, 10.00, 0.00845, 8.45, "Current writer/analyst/planner/editor/ask/memory", "HF llm-pricing-calculator"],
    ["Gemini 3.5 Flash-Lite", 0.30, 2.50, 0.00157, 1.57, "Lower-cost alternative for analyst/planner/editor", "HF llm-pricing-calculator"],
    ["DeepSeek V4-Flash", 0.14, 0.28, 0.00047, 0.47, "Verifier and lower-cost ask/memory alternative", "HF llm-pricing-calculator"],
]
add_sheet(wb, "Pricing_Evidence", ["Model", "Input $/M tokens", "Output $/M tokens", "Est. cost per 3,017-token routing turn", "Est. cost per 1,000 routing turns", "Current / alternative role", "Source", *REVIEW],
          pricing_rows, widths={"Model": 22, "Input $/M tokens": 12, "Output $/M tokens": 12, "Est. cost per 3,017-token routing turn": 22, "Est. cost per 1,000 routing turns": 20, "Current / alternative role": 42, "Source": 28, "Review notes": 35})


# ---------------------------------------------------------------- model ablation
model_ablation_rows = []
for part, models, suite in [
    ("understand", ["claude-sonnet-5.5", "gemini-3.5-flash-lite", "deepseek-flash"], "Routing_84"),
    ("planner", ["claude-sonnet-5.5", "gemini-3.5-flash-lite"], "Planner_17"),
    ("editor", ["claude-sonnet-5.5", "deepseek-flash", "gemini-3.5-flash-lite"], "Editor_8"),
    ("ask", ["claude-sonnet-5.5", "deepseek-flash", "gemini-3.5-flash-lite"], "Ask_12"),
    ("memory", ["claude-sonnet-5.5", "deepseek-flash"], "Memory_8"),
    ("verify", ["deepseek-flash", "claude-sonnet-5.5"], "Verifier_17"),
]:
    for model in models:
        model_ablation_rows.append([
            part, model, suite, "same cases; same temperature and cap",
            "Measure correctness, output tokens, p50 latency and estimated cost.",
            "Keep the current model unless another arm is better on both correctness and a justified cost/latency trade.",
            "Pending retest", "", "", "",
        ])
add_sheet(wb, "Model_Ablation", ["Part", "Model arm", "Cases", "Control", "Hypothesis / metric", "Decision rule", "Test status", *REVIEW],
          model_ablation_rows, widths={"Part": 16, "Model arm": 24, "Cases": 16, "Control": 30, "Hypothesis / metric": 55, "Decision rule": 65, "Test status": 18, "Review notes": 35})

# ---------------------------------------------------------------- reasoning ablation
reasoning_rows = []
for part, model, suite in [
    ("writer", "claude-sonnet-5.5", "end-to-end / reply quality"),
    ("analyst", "claude-sonnet-5.5", "Routing_84"),
    ("planner", "claude-sonnet-5.5", "Planner_17"),
    ("editor", "claude-sonnet-5.5", "Editor_8"),
    ("ask", "claude-sonnet-5.5", "Ask_12"),
    ("memory", "claude-sonnet-5.5", "Memory_8"),
    ("verifier", "deepseek-flash", "Verifier_17"),
]:
    for setting in ("none", "low / on (if supported)"):
        reasoning_rows.append([
            part, model, setting, suite,
            "Same cases and temperature; inspect whether the endpoint emits reasoning_content.",
            "Measure correctness, output/reasoning tokens, p50/p95 latency and cost.",
            "Use reasoning only if it improves correctness enough to justify output tokens and latency; otherwise keep none.",
            "Pending retest", "", "", "",
        ])
add_sheet(wb, "Reasoning_Ablation", ["Part", "Model", "Reasoning setting", "Cases", "Control", "Hypothesis / metric", "Decision rule", "Test status", *REVIEW],
          reasoning_rows, widths={"Part": 14, "Model": 24, "Reasoning setting": 22, "Cases": 22, "Control": 40, "Hypothesis / metric": 55, "Decision rule": 65, "Test status": 18, "Review notes": 35})

# ---------------------------------------------------------------- index/readme additions
index = wb["Index"]
for row in [
    ["Model_Settings", len(model_rows), "Per-part model, temperature, cap and reasoning", "Design reference - retest", "build_case_workbook.py"],
    ["Temperature_Ablation", len(temperature_rows), "0.0 vs 0.2 per part/model", "Pending retest", "planned matrix"],
    ["Router_Comparison_84", len(router_rows), "Jev vs Flash-Lite vs Sonnet arms", "Pending retest", "routing.jsonl"],
    ["Pricing_Evidence", len(pricing_rows), "Rates and estimated routing cost", "Reference", "HF llm-pricing-calculator"],
    ["Model_Ablation", len(model_ablation_rows), "Per-part model-arm comparison", "Pending retest", "planned matrix"],
    ["Reasoning_Ablation", len(reasoning_rows), "Reasoning none vs low/on where supported", "Pending retest", "planned matrix"],
]:
    index.append(row)

ws = wb["README"]
extra = [
    ("Experiments", "The workbook now includes Model_Settings, Temperature_Ablation, Router_Comparison_84 and Pricing_Evidence. They are experiment designs, not results."),
    ("Temperature", "Every part is planned at 0.0 and 0.2 on the same frozen cases. No temperature result is recorded here."),
    ("Jev", "Router_Comparison_84 lists Jev, Flash-Lite, Sonnet 0.0 and Sonnet 0.2 as separate arms; no agreement or accuracy result is claimed."),
    ("Pricing", "Prices are a snapshot from the HF calculator. They are reference rates, not invoices."),
    ("Model ablation", "Model_Ablation compares Sonnet, Flash-Lite and DeepSeek on the same part-specific suites; no model result is recorded yet."),
    ("Reasoning ablation", "Reasoning_Ablation tests none vs low/on only where the endpoint exposes the switch; DeepSeek may emit reasoning_content even when none is requested."),
]
r = ws.max_row + 2
for key, value in extra:
    ws.cell(r, 1, key).font = Font(bold=True, size=10)
    ws.cell(r, 2, value).font = Font(size=10)
    ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
    r += 1

wb._sheets = [wb["README"], wb["Index"], wb["Changes"], wb["Model_Settings"], wb["Model_Ablation"], wb["Temperature_Ablation"], wb["Reasoning_Ablation"], wb["Router_Comparison_84"], wb["Pricing_Evidence"], wb["All_Cases"], wb["Routing_84"], wb["Planner_17"], wb["Verifier_17"], wb["Editor_8"], wb["Ask_12"], wb["Memory_8"], wb["Efficiency_10"], wb["Edit_vs_Replan_5"], wb["Injection_84"], wb["Cap_Stress"], wb["TravelPlanner_1225"]]
wb.save(OUT)
print(f"updated {OUT}")
