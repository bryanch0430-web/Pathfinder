"""Build the final full review workbook from the clean catalogue plus latest results."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PROJECT = Path(__file__).resolve().parents[2]
ART = PROJECT / "docs" / "eval-artifacts" / "autotest"
MASTER = PROJECT / "docs" / "report" / "Pathfinder_Test_Cases_Review.xlsx"
OUT = PROJECT / "docs" / "report" / "Pathfinder_Full_Workbook.xlsx"
NAVY = "1F3A5F"
AMBER = "FFF2CC"
GREEN = "E2EFDA"
RED = "FCE4D6"
REVIEW = ["Review status", "Reviewer", "Review notes"]


def load(name):
    return json.loads((ART / name).read_text(encoding="utf-8"))


def add_sheet(wb, title, headers, rows, widths=None):
    ws = wb.create_sheet(title[:31])
    ws.append(headers)
    for row in rows:
        ws.append([json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for value in row])
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
    ws.row_dimensions[1].height = 28
    for i, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(header, 18)
    return ws


def main():
    wb = load_workbook(MASTER)

    # ---------------------------------------------------------------- summary
    summary = [
        ["Routing", "84", "4 ChatAnywhere arms + Jev", "Sonnet 84/84; GPT-5.5 82/84; DeepSeek 81/84; Flash 80/84; Jev 79/84 with plan context", "valid"],
        ["Verifier", "17 unique", "DeepSeek and Sonnet, T=0/0.2", "15/17 best; 2 false passes (v06, v14), 0 false alarms", "valid"],
        ["Editor", "8", "Sonnet, T=0/0.2", "7/8 at T=0; 8/8 at T=0.2", "valid"],
        ["Ask", "12", "Sonnet", "11/12 total; 11/11 in-scope; q11 is the scope case", "valid"],
        ["Memory", "8", "Sonnet", "6/8 exact; mean recall 91.7%", "valid"],
        ["Planner HK + Japan", "14", "Sonnet writer; DeepSeek verifier", "Hong Kong 7/8; Japan 6/6", "valid"],
        ["Injection", "84", "Sandbox fixture", "84/84 containment", "valid"],
        ["TravelPlanner pilot", "20", "Relevance-selected; fixed 2022 clock", "15 plans; 5 correct ISO-4217 currency clarifications", "pilot only"],
        ["Efficiency", "10", "No valid context", "0 calls/0 tokens; invalid harness", "invalid"],
        ["Edit vs Replan", "5", "No seed plan", "0 stops before/after; invalid harness", "invalid"],
        ["Cap stress", "10", "Attribution/context", "effective conditions failed; not credible", "invalid"],
        ["TravelPlanner-style", "3", "Missing start date", "all no_plan; invalid setup", "invalid"],
        ["TravelPlanner relevant", "423", "External reference frame", "147 Tier A, 276 Tier B; not fully run", "reference"],
    ]
    add_sheet(wb, "Result_Summary", ["Suite", "Cases", "Setting", "Latest result", "Status"], summary,
              widths={"Suite": 24, "Cases": 12, "Setting": 45, "Latest result": 85, "Status": 14})

    # ---------------------------------------------------------------- routing
    routing = load("routing-chatanywhere-20261123-113541.json")
    routing_rows = []
    for arm, data in routing["arms"].items():
        for row in data["rows"]:
            routing_rows.append([
                arm, row["id"], row["input"], row["expected"], row["predicted"],
                "yes" if row["correct"] else "no", row.get("confidence"), row.get("latency_ms"), row.get("tokens"),
                data.get("model"), "ChatAnywhere", "", "", "",
            ])
    jev = load("jev-with-plan.json")
    for row in jev["rows"]:
        routing_rows.append([
            "jev-latest (plan context)", row["id"], "", row["expected"], row["predicted"],
            "yes" if row["correct"] else "no", row.get("confidence"), None, None,
            "jev-latest", "Jev env key", "", "", "",
        ])
    add_sheet(wb, "Results_Routing_84", ["Arm", "Case ID", "Input", "Expected", "Predicted", "Correct", "Confidence", "Latency ms", "Tokens", "Model", "Provider", *REVIEW], routing_rows,
              widths={"Arm": 28, "Case ID": 8, "Input": 60, "Expected": 10, "Predicted": 10, "Correct": 8, "Confidence": 10, "Latency ms": 10, "Tokens": 8, "Model": 22, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- verifier
    verifier = load("verifier-chatanywhere-20261123-120337.json")
    verifier_rows = []
    for arm, data in verifier["arms"].items():
        for row in data["rows"]:
            verifier_rows.append([
                arm, row["id"], row["constraint"], row["mutation"], row["gold"], row["predicted"],
                "yes" if row["correct"] else "no", "yes" if row["false_pass"] else "no",
                "yes" if row["false_alarm"] else "no", row.get("evidence", ""), row.get("latency_ms"),
                data.get("model"), "ChatAnywhere", "", "", "",
            ])
    add_sheet(wb, "Results_Verifier_17", ["Arm", "Case ID", "Constraint", "Mutation", "Gold", "Predicted", "Correct", "False pass", "False alarm", "Evidence", "Latency ms", "Model", "Provider", *REVIEW], verifier_rows,
              widths={"Arm": 26, "Case ID": 8, "Constraint": 38, "Mutation": 22, "Gold": 7, "Predicted": 9, "Correct": 8, "False pass": 9, "False alarm": 9, "Evidence": 50, "Latency ms": 10, "Model": 22, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- editor
    editor = load("editor-chatanywhere-20261123-121217.json")
    editor_rows = []
    for arm, data in editor["arms"].items():
        for row in data["rows"]:
            editor_rows.append([
                arm, row["id"], row["input"], "yes" if row["passed"] else "no", row.get("latency_ms"),
                row.get("tokens"), row.get("error", ""), data.get("model"), "ChatAnywhere", "", "", "",
            ])
    add_sheet(wb, "Results_Editor_8", ["Arm", "Case ID", "Input", "Passed", "Latency ms", "Tokens", "Error", "Model", "Provider", *REVIEW], editor_rows,
              widths={"Arm": 24, "Case ID": 8, "Input": 55, "Passed": 8, "Latency ms": 10, "Tokens": 8, "Error": 35, "Model": 22, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- ask
    ask = load("ask-chatanywhere-20261123-121646.json")
    ask_rows = [[
        row["id"], row["question"], row.get("reply", ""), "yes" if row["correct"] else "no",
        row.get("missing"), row.get("forbidden"), row.get("expect_gap", False),
        row.get("latency_ms"), row.get("tokens"), ask.get("model"), "ChatAnywhere", "", "", "",
    ] for row in ask["rows"]]
    add_sheet(wb, "Results_Ask_12", ["Case ID", "Question", "Reply", "Correct", "Missing", "Forbidden", "Expect gap", "Latency ms", "Tokens", "Model", "Provider", *REVIEW], ask_rows,
              widths={"Case ID": 8, "Question": 45, "Reply": 70, "Correct": 8, "Missing": 22, "Forbidden": 22, "Expect gap": 9, "Latency ms": 10, "Tokens": 8, "Model": 22, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- memory
    memory = load("memory-chatanywhere-20261123-124643.json")
    memory_rows = [[
        row["id"], " | ".join(row.get("preferences", [])), " | ".join(row.get("gold", [])),
        " | ".join(row.get("hits", [])), row.get("recall"), row.get("latency_ms"),
        memory.get("model"), "ChatAnywhere", "", "", "",
    ] for row in memory["rows"]]
    add_sheet(wb, "Results_Memory_8", ["Case ID", "Preferences", "Gold", "Hits", "Recall", "Latency ms", "Model", "Provider", *REVIEW], memory_rows,
              widths={"Case ID": 8, "Preferences": 55, "Gold": 40, "Hits": 40, "Recall": 8, "Latency ms": 10, "Model": 22, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- planner and injection
    remaining = load("remaining-chatanywhere-latest.json")["datasets"]
    planner_rows = []
    for ds in ("hongkong", "japan"):
        for row in remaining[ds]["cases"]:
            planner_rows.append([
                ds, row["case_id"], "yes" if row["passed"] else "no", row.get("failure_class", ""),
                (row.get("scores") or {}).get("constraints_met", ""), (row.get("scores") or {}).get("constraints_total", ""),
                (row.get("scores") or {}).get("grounding", ""), row.get("output_summary", ""),
                "ChatAnywhere", "", "", "",
            ])
    add_sheet(wb, "Results_Planner_14", ["Suite", "Case ID", "Passed", "Failure", "Constraints met", "Constraints total", "Grounding", "Output summary", "Provider", *REVIEW], planner_rows,
              widths={"Suite": 12, "Case ID": 8, "Passed": 8, "Failure": 14, "Constraints met": 9, "Constraints total": 10, "Grounding": 9, "Output summary": 70, "Provider": 16, "Review notes": 30})
    injection_rows = [[
        row["case_id"], "yes" if row["passed"] else "no", row.get("failure_class", ""), row.get("output_summary", ""),
        "ChatAnywhere", "", "", "",
    ] for row in remaining["injection"]["cases"]]
    add_sheet(wb, "Results_Injection_84", ["Case ID", "Passed", "Failure", "Output", "Provider", *REVIEW], injection_rows,
              widths={"Case ID": 8, "Passed": 8, "Failure": 14, "Output": 70, "Provider": 16, "Review notes": 30})

    # ---------------------------------------------------------------- TP pilot
    pilot = load("travelplanner-pilot20-latest.json")
    pilot_rows = []
    for row in pilot["cases"]:
        pilot_rows.append([
            row["uid"], row.get("stratum", [""])[0], row.get("destination", ""),
            "yes" if row.get("has_plan") else "no", row.get("day_count", 0),
            " | ".join(str(item) for item in row.get("stratum", [])), " | ".join(str(x) for x in row.get("why", [])),
            "", "", "",
        ])
    add_sheet(wb, "Results_TP_Pilot20", ["Case UID", "Tier", "Destination", "Plan generated", "Days", "Stratum", "Why / failure", *REVIEW], pilot_rows,
              widths={"Case UID": 20, "Tier": 7, "Destination": 16, "Plan generated": 9, "Days": 7, "Stratum": 28, "Why / failure": 70, "Review notes": 30})

    # ---------------------------------------------------------------- invalid and refs
    invalid = [
        ["Efficiency_10", "0 calls / 0 tokens; no context", "invalid harness", "Do not report as a model failure"],
        ["Edit_vs_Replan_5", "0 stops before/after; no seed plan", "invalid harness", "Fix seed plan first"],
        ["Cap_Stress", "effective conditions failed; attribution unclear", "invalid run", "Fix context and rerun"],
        ["TravelPlanner-style 3", "all no_plan; missing start date", "invalid setup", "Do not report as model failure"],
        ["423 TravelPlanner", "screened only; 20-case pilot run", "reference", "Not a full-set result"],
    ]
    add_sheet(wb, "Invalid_Or_Reference", ["Suite", "Observed", "Status", "Interpretation"], invalid,
              widths={"Suite": 24, "Observed": 55, "Status": 18, "Interpretation": 50})

    refs = [
        ["TravelPlanner dataset", "https://huggingface.co/datasets/osunlp/TravelPlanner", "Official 1,225-case population and source of relevance screen"],
        ["LLM pricing calculator", "https://huggingface.co/spaces/Presidentlin/llm-pricing-calculator", "Reference prices used in pricing sheet"],
        ["ChatAnywhere", "https://api.chatanywhere.org/v1", "Test-only provider; key not stored in artifact"],
        ["Jev", "https://api.typesafe.ai/v1/systemone", "Jev env key; plan-context comparison"],
    ]
    add_sheet(wb, "Sources_And_Refs", ["Source", "URL", "Use"], refs,
              widths={"Source": 26, "URL": 80, "Use": 60})

    wb._sheets = [wb["README"], wb["Result_Summary"], wb["Final_Test_List"], wb["Index"], wb["Model_Settings"], wb["Model_Ablation"], wb["Temperature_Ablation"], wb["Reasoning_Ablation"], wb["Router_Comparison_84"], wb["Pricing_Evidence"], wb["All_Cases"], wb["Results_Routing_84"], wb["Results_Verifier_17"], wb["Results_Editor_8"], wb["Results_Ask_12"], wb["Results_Memory_8"], wb["Results_Planner_14"], wb["Results_Injection_84"], wb["Results_TP_Pilot20"], wb["Invalid_Or_Reference"], wb["Sources_And_Refs"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"{OUT} sheets={len(wb.sheetnames)}")


if __name__ == "__main__":
    main()
