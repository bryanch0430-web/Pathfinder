from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

PROJECT = Path(__file__).resolve().parents[2]
ART = PROJECT / "docs" / "eval-artifacts" / "v5-model-study"
FIX = PROJECT / "backend" / "eval" / "fixtures"
OUT = PROJECT / "docs" / "report" / "Pathfinder_Test_Cases_Review.xlsx"

NAVY = "1F3A5F"
LIGHT = "EEF2F6"
GREEN = "E2EFDA"
RED = "FCE4D6"
AMBER = "FFF2CC"
GREY = "F2F2F2"

REVIEW = ["Review status", "Reviewer", "Review notes"]
REVIEW_VALUES = '"pending,pass,fail,needs discussion"'
HEADERS = {
    "routing": ["Case ID", "Split", "Input", "Expected intent", "Run 1 predicted", "Run 1 correct", "Run 2 predicted", "Run 2 correct", "Confidence", "Latency ms", "Tokens", "Source", *REVIEW],
    "planner": ["Case ID", "Suite", "Split", "Input", "Expected constraints", "Passed", "Failure class", "Actual summary", "Model calls", "Tokens", "Latency ms", "Source", *REVIEW],
    "verifier": ["Case ID", "Constraint", "Mutation", "Gold", "Predicted", "Correct", "False pass", "False alarm", "Evidence", "Latency ms", "Source", *REVIEW],
    "editor": ["Case ID", "Input", "Check", "Passed", "Draft summary", "Tokens", "Latency ms", "Source", *REVIEW],
    "ask": ["Case ID", "Question", "Correct", "Expected fact present", "Reply", "Tokens", "Latency ms", "Model", "Source", *REVIEW],
    "memory": ["Case ID", "Preferences", "Gold preferences", "Hits", "Recall", "Latency ms", "Source", *REVIEW],
    "efficiency": ["Case ID", "Input", "Expected intent", "Destination", "Split", "Status", "Source", *REVIEW],
    "edit": ["Case ID", "Kind", "Input", "Destination", "Split", "Passed", "Preservation", "Model calls", "Tokens", "Status", "Source", *REVIEW],
    "injection": ["Case ID", "Attack", "Channel", "Input", "Destination", "Task", "Seed plan", "Split", "Status", "Source", *REVIEW],
    "cap": ["Part", "Condition", "OK", "Detail", "Cap", "Seconds", "Status", "Source", *REVIEW],
    "tp": ["UID", "Split", "Row", "Origin", "Destination", "Days", "Visiting cities", "Date(s)", "People", "Budget USD", "Level", "Local constraints", "Query", "Has annotated plan", "Has reference info", "Run status", "Source", *REVIEW],
}


def jl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def short(value, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def add_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], *, widths: dict[str, int] | None = None) -> None:
    ws = wb.create_sheet(title[:31])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    header_fill = PatternFill("solid", fgColor=NAVY)
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D9E1F2")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = Font(size=9)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.row_dimensions[1].height = 30
    for idx, header in enumerate(headers, start=1):
        width = (widths or {}).get(header, 18)
        ws.column_dimensions[get_column_letter(idx)].width = width
    if "Review status" in headers:
        col = headers.index("Review status") + 1
        dv = DataValidation(type="list", formula1=REVIEW_VALUES, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(col)}2:{get_column_letter(col)}2000")
    if "Passed" in headers:
        col = headers.index("Passed") + 1
        rng = f"{get_column_letter(col)}2:{get_column_letter(col)}2000"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"yes"'], fill=PatternFill("solid", fgColor=GREEN)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"no"'], fill=PatternFill("solid", fgColor=RED)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"not run"'], fill=PatternFill("solid", fgColor=AMBER)))
    if "Status" in headers:
        col = headers.index("Status") + 1
        rng = f"{get_column_letter(col)}2:{get_column_letter(col)}2000"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"not run"'], fill=PatternFill("solid", fgColor=AMBER)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"pass"'], fill=PatternFill("solid", fgColor=GREEN)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"fail"'], fill=PatternFill("solid", fgColor=RED)))


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    # Routing: frozen 84 cases, two current runs.
    routing_cases = sorted(jl(FIX / "routing.jsonl"), key=lambda r: r["id"])
    run1 = read_json(ART / "routing-20261123-075040.json")
    run2 = read_json(ART / "routing-20261123-075208.json")
    def routing_rows_of(payload):
        cases = payload["cases"]
        return next(iter(cases.values())) if isinstance(cases, dict) else cases

    r1 = {r["id"]: r for r in routing_rows_of(run1)}
    r2 = {r["id"]: r for r in routing_rows_of(run2)}
    routing_rows = []
    for case in routing_cases:
        a = r1.get(case["id"], {})
        b = r2.get(case["id"], {})
        routing_rows.append([
            case["id"], case.get("split", ""), case["input"], case["expected_intent"],
            a.get("predicted", ""), "yes" if a.get("correct") else "no",
            b.get("predicted", ""), "yes" if b.get("correct") else "no",
            b.get("confidence", ""), b.get("latency_ms", ""), b.get("tokens", ""),
            "routing-20261123-075040/075208",
            "", "", "",
        ])

    # Planner: Hong Kong / Japan / three TravelPlanner-style cases.
    planner_fixtures = []
    for name in ("hongkong", "japan", "travelplanner"):
        planner_fixtures.extend(read_json(FIX / f"{name}.json"))
    datasets = read_json(ART / "datasets-latest.json")["datasets"]
    plan_results = {}
    for ds in ("hongkong", "japan", "travelplanner"):
        arm = datasets.get(ds, {}).get("sonnet", {})
        for row in arm.get("cases", []):
            plan_results[row["case_id"]] = {**row, "dataset": ds, "model": arm.get("model", "")}
    planner_rows = []
    for case in planner_fixtures:
        row = plan_results.get(case["id"], {})
        planner_rows.append([
            case["id"], {"hongkong": "Hong Kong", "japan": "Japan", "travelplanner": "TravelPlanner-style"}[next((k for k in ("hongkong", "japan", "travelplanner") if case["id"].startswith({"hongkong": "hk", "japan": "jp", "travelplanner": "tp"}[k])), "japan")],
            case.get("split", ""), case["input"], " | ".join(case.get("constraints", [])),
            "yes" if row.get("passed") else ("no" if row else "not run"),
            row.get("failure_class", ""), short(row.get("output_summary", "")),
            (row.get("scores") or {}).get("llm_calls", ""), (row.get("scores") or {}).get("tokens", ""),
            row.get("latency_ms", ""), f"datasets-latest/{row.get('dataset','')}",
            "", "", "",
        ])

    # Verifier: 20 frozen cases.
    verifier = read_json(ART / "verifier-current.json")
    verifier_rows = []
    for row in verifier["rows"]:
        verifier_rows.append([
            row["id"], row["constraint"], row["mutation"], row["gold"], row["predicted"],
            "yes" if row["correct"] else "no", "yes" if row["false_pass"] else "no",
            "yes" if row["false_alarm"] else "no", short(row.get("evidence", "")),
            row.get("latency_ms", ""), "verifier-current.json", "", "", "",
        ])

    # Editor: current Sonnet arm.
    editor = read_json(ART / "editor-direct-latest.json")["arms"]["sonnet"]
    editor_rows = []
    for row in editor["rows"]:
        editor_rows.append([
            row["id"], row.get("input", ""), row.get("check", ""), "yes" if row["passed"] else "no",
            short(row.get("summary", "")), row.get("tokens", ""), row.get("latency_ms", ""),
            "editor-direct-latest/sonnet", "", "", "",
        ])

    # Ask: DeepSeek arm (current choose), include all cases.
    ask = read_json(ART / "ask-latest.json")
    ask_arm = ask["arms"].get("deepseek") or next(iter(ask["arms"].values()))
    ask_rows = []
    for row in ask_arm["rows"]:
        ask_rows.append([
            row["id"], row["question"], "yes" if row["correct"] else "no",
            "; ".join(" ".join(x) if isinstance(x, list) else str(x) for x in row.get("missing", [])),
            short(row.get("reply", "")), row.get("tokens", ""), row.get("latency_ms", ""),
            row.get("model", ""), "ask-latest/deepseek", "", "", "",
        ])

    # Memory: Sonnet (shipped default).
    memory = read_json(ART / "memory-latest.json")["arms"]["sonnet"]
    memory_rows = []
    for row in memory["rows"]:
        memory_rows.append([
            row["id"], " | ".join(row.get("preferences", [])), " | ".join(row.get("gold", [])),
            " | ".join(row.get("hits", [])), row.get("recall", ""), row.get("latency_ms", ""),
            "memory-latest/sonnet", "", "", "",
        ])

    efficiency_cases = read_json(FIX / "efficiency.json")
    efficiency_rows = [[
        c["id"], c["input"], c["expected_intent"], c.get("destination", ""), c.get("split", ""),
        "not run", "efficiency.json", "", "", "",
    ] for c in efficiency_cases]

    edit_cases = read_json(FIX / "edit_vs_replan.json")
    edit_run = read_json(ART / "datasets-20261123-085453.json")["datasets"]["edit_vs_replan"]["sonnet"]
    edit_results = {r["case_id"]: r for r in edit_run["cases"]}
    edit_rows = []
    for case in edit_cases:
        row = edit_results.get(case["id"], {})
        edit_rows.append([
            case["id"], case["kind"], case["input"], case.get("destination", ""), case.get("split", ""),
            "yes" if row.get("passed") else ("no" if row else "not run"),
            (row.get("scores") or {}).get("preservation", ""), (row.get("scores") or {}).get("llm_calls", ""),
            (row.get("scores") or {}).get("tokens", ""),
            "run (edit only; no replan arm)" if row else "not run",
            "datasets-20261123-085453", "", "", "",
        ])

    injection_cases = sorted(jl(FIX / "injection.jsonl"), key=lambda r: r["id"])
    injection_rows = [[
        c["id"], c.get("attack", ""), c.get("channel", ""), c["input"], c.get("destination", ""),
        c.get("task", ""), "yes" if c.get("seed_plan") else "no", c.get("split", ""),
        "not run", "injection.jsonl", "", "", "",
    ] for c in injection_cases]

    cap = read_json(ART / "cap-stress-latest.json")
    cap_rows = []
    for part, conditions in cap.items():
        for condition, data in conditions.items():
            cap_rows.append([
                part, condition, "yes" if data.get("ok") else "no", short(data.get("detail", "")),
                data.get("cap", ""), data.get("seconds", ""), "reference", "cap-stress-latest.json",
                "", "", "",
            ])

    # Full TravelPlanner: 45 train + 180 validation + 1,000 test = 1,225.
    tp_rows = []
    tp_counts = {}
    for split in ("train", "validation", "test"):
        path = PROJECT / ".archify" / "travelplanner" / f"{split}.csv"
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            count = 0
            for index, row in enumerate(reader, start=1):
                count += 1
                tp_rows.append([
                    f"TP-{split.upper()}-{index:04d}", split, index,
                    row.get("org", ""), row.get("dest", ""), row.get("days", ""),
                    row.get("visiting_city_number", ""), row.get("date", ""), row.get("people_number", ""),
                    row.get("budget", ""), row.get("level", ""), short(row.get("local_constraint", ""), 1000),
                    row.get("query", ""), "yes" if row.get("annotated_plan") else "no",
                    "yes" if row.get("reference_information") else "no",
                    "Not run — reference only (full benchmark)", f"TravelPlanner/{split}.csv",
                    "", "", "",
                ])
            tp_counts[split] = count

    # README.
    ws = wb.create_sheet("README")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Pathfinder — Human Review Case Workbook"
    ws["A1"].font = Font(size=18, bold=True, color=NAVY)
    notes = [
        ("Generated", "2026-11-23"),
        ("Purpose", "One case-level workbook for human review. Every sheet has blank Review status, Reviewer and Review notes columns."),
        ("TravelPlanner", f"Full benchmark reference set: 45 train + 180 validation + 1,000 test = {sum(tp_counts.values())} cases. These are NOT Pathfinder run results."),
        ("TravelPlanner source", "https://huggingface.co/datasets/osunlp/TravelPlanner (CC-BY-4.0); test.csv revision with official structured annotations."),
        ("Current results", "Routing, Planner, Verifier, Editor, Ask, Memory and Edit vs Replan contain recorded v5 results where available."),
        ("Not run", "Injection (84), Efficiency (10) and all 1,225 TravelPlanner cases are included for review but were not run for this report."),
        ("Evaluation tab", "The in-app Evaluation tab and error-analysis view are reference/demo surfaces, not the source of these results."),
        ("Status values", "yes / no / not run. Review status dropdown: pending / pass / fail / needs discussion."),
        ("Caveat", "Edit vs Replan measures the edit arm only; there is no paired full-replan arm in this workbook."),
        ("Caveat", "Ask_12 contains all 12 ask cases; 11 are in scope. q11 is a scope case and should be reviewed manually."),
        ("Caveat", "Cap stress rows are aggregate condition results, not individual model calls."),
    ]
    r = 3
    for key, value in notes:
        ws.cell(r, 1, key).font = Font(bold=True, size=10)
        ws.cell(r, 2, value).font = Font(size=10)
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120

    # Index.
    index_rows = [
        ["Routing_84", 84, "two current Flash-Lite runs", "84 cases; 81/84 and 82/84", "routing-20261123-075040/075208"],
        ["Planner_17", 17, "Hong Kong 8 + Japan 6 + TravelPlanner-style 3", "14/17 Sonnet frozen", "datasets-latest"],
        ["Verifier_20", 20, "DeepSeek Flash, T=0", "18/20; 2 false passes", "verifier-current.json"],
        ["Editor_8", 8, "Sonnet direct edit", "8/8", "editor-direct-latest"],
        ["Ask_12", len(ask_arm["rows"]), "DeepSeek arm", "recorded exact/adjudicated", "ask-latest"],
        ["Memory_8", 8, "Sonnet arm", "component recall", "memory-latest"],
        ["Efficiency_10", 10, "fixture", "not run", "efficiency.json"],
        ["Edit_vs_Replan_5", 5, "edit arm only", "5/5; no replan baseline", "datasets-20261123-085453"],
        ["Injection_84", 84, "fixture", "not run", "injection.jsonl"],
        ["Cap_Stress", len(cap_rows), "aggregate conditions", "reference only", "cap-stress-latest"],
        ["TravelPlanner_1225", sum(tp_counts.values()), "45 train + 180 validation + 1,000 test", "not run; reference only", "HuggingFace osunlp/TravelPlanner"],
    ]
    add_sheet(wb, "Index", ["Sheet", "Cases", "Scope", "Recorded result", "Source"], index_rows,
              widths={"Sheet": 24, "Cases": 10, "Scope": 48, "Recorded result": 38, "Source": 40})

    # All cases (single flat view).
    all_rows = []
    for row in routing_rows:
        all_rows.append([f"routing-{row[0]}", "Routing", row[0], row[1], row[2], row[3], f"{row[4]} / {row[6]}", f"{row[5]} / {row[7]}", "run", row[11], row[9], row[10], "", "", ""])
    for row in planner_rows:
        all_rows.append([f"planner-{row[0]}", "Planner", row[0], row[2], row[3], row[4], row[7], row[5], "run" if row[5] != "not run" else "not run", row[11], row[10], row[9], "", "", ""])
    for row in verifier_rows:
        all_rows.append([f"verifier-{row[0]}", "Verifier", row[0], "", row[1], row[3], row[4], row[5], "run", row[10], row[9], "", "", "", ""])
    for row in editor_rows:
        all_rows.append([f"editor-{row[0]}", "Editor", row[0], "", row[1], row[2], row[4], row[3], "run", row[7], row[6], row[5], "", "", ""])
    for row in ask_rows:
        all_rows.append([f"ask-{row[0]}", "Ask", row[0], "", row[1], "", row[4], row[2], "run", row[8], row[6], row[5], "", "", ""])
    for row in memory_rows:
        all_rows.append([f"memory-{row[0]}", "Memory", row[0], "", " | ".join(row[1]) if isinstance(row[1], list) else row[1], "", "", "", "run", row[6], row[5], "", "", "", ""])
    for row in efficiency_rows:
        all_rows.append([f"efficiency-{row[0]}", "Efficiency", row[0], row[4], row[1], row[2], "", "", "not run", row[6], "", "", "", "", ""])
    for row in edit_rows:
        all_rows.append([f"edit-{row[0]}", "Edit vs Replan", row[0], row[4], row[2], "preserve stops", row[6], row[5], "run" if row[5] != "not run" else "not run", row[10], "", row[8], "", "", ""])
    for row in injection_rows:
        all_rows.append([f"injection-{row[0]}", "Injection", row[0], row[7], row[3], "no leak", "", "", "not run", row[9], "", "", "", "", ""])
    for row in cap_rows:
        all_rows.append([f"cap-{row[0]}-{row[1]}", "Cap stress", f"{row[0]}/{row[1]}", "", row[3], row[2], "", "yes" if row[2] == "yes" else ("no" if row[2] == "no" else ""), "reference", row[7], "", "", "", "", ""])
    for row in tp_rows:
        all_rows.append([row[0], "TravelPlanner", row[1], f"{row[2]}/{row[1]}", row[12], row[11], "", "", "not run", row[16], "", "", "", "", ""])
    add_sheet(wb, "All_Cases", ["UID", "Suite", "Case ID", "Split/Group", "Input / Query", "Expected", "Actual / Result", "Passed", "Status", "Source", "Latency ms", "Tokens/Calls", "Review status", "Reviewer", "Review notes"], all_rows,
              widths={"UID": 22, "Suite": 16, "Case ID": 12, "Split/Group": 14, "Input / Query": 80, "Expected": 34, "Actual / Result": 34, "Passed": 9, "Status": 10, "Source": 30, "Latency ms": 11, "Tokens/Calls": 11, "Review status": 14, "Reviewer": 14, "Review notes": 40})

    add_sheet(wb, "Routing_84", HEADERS["routing"], routing_rows,
              widths={"Case ID": 10, "Split": 9, "Input": 55, "Expected intent": 12, "Run 1 predicted": 12, "Run 1 correct": 10, "Run 2 predicted": 12, "Run 2 correct": 10, "Confidence": 10, "Latency ms": 10, "Tokens": 9, "Source": 30, "Review notes": 32})
    add_sheet(wb, "Planner_17", HEADERS["planner"], planner_rows,
              widths={"Case ID": 9, "Suite": 16, "Split": 8, "Input": 55, "Expected constraints": 30, "Passed": 9, "Failure class": 14, "Actual summary": 60, "Model calls": 9, "Tokens": 9, "Latency ms": 10, "Source": 28, "Review notes": 32})
    add_sheet(wb, "Verifier_20", HEADERS["verifier"], verifier_rows,
              widths={"Case ID": 9, "Constraint": 42, "Mutation": 30, "Gold": 8, "Predicted": 9, "Correct": 8, "False pass": 9, "False alarm": 9, "Evidence": 50, "Latency ms": 10, "Source": 24, "Review notes": 32})
    add_sheet(wb, "Editor_8", HEADERS["editor"], editor_rows,
              widths={"Case ID": 9, "Input": 50, "Check": 12, "Passed": 8, "Draft summary": 55, "Tokens": 9, "Latency ms": 10, "Source": 24, "Review notes": 32})
    add_sheet(wb, "Ask_12", HEADERS["ask"], ask_rows,
              widths={"Case ID": 8, "Question": 45, "Correct": 8, "Expected fact present": 30, "Reply": 75, "Tokens": 9, "Latency ms": 10, "Model": 18, "Source": 22, "Review notes": 32})
    add_sheet(wb, "Memory_8", HEADERS["memory"], memory_rows,
              widths={"Case ID": 8, "Preferences": 50, "Gold preferences": 35, "Hits": 35, "Recall": 8, "Latency ms": 10, "Source": 22, "Review notes": 32})
    add_sheet(wb, "Efficiency_10", HEADERS["efficiency"], efficiency_rows,
              widths={"Case ID": 9, "Input": 60, "Expected intent": 12, "Destination": 14, "Split": 8, "Status": 10, "Source": 22, "Review notes": 32})
    add_sheet(wb, "Edit_vs_Replan_5", HEADERS["edit"], edit_rows,
              widths={"Case ID": 9, "Kind": 8, "Input": 55, "Destination": 14, "Split": 8, "Passed": 8, "Preservation": 10, "Model calls": 9, "Tokens": 9, "Status": 10, "Source": 26, "Review notes": 32})
    add_sheet(wb, "Injection_84", HEADERS["injection"], injection_rows,
              widths={"Case ID": 9, "Attack": 20, "Channel": 10, "Input": 70, "Destination": 14, "Task": 12, "Seed plan": 9, "Split": 8, "Status": 10, "Source": 20, "Review notes": 32})
    add_sheet(wb, "Cap_Stress", HEADERS["cap"], cap_rows,
              widths={"Part": 12, "Condition": 12, "OK": 8, "Detail": 55, "Cap": 8, "Seconds": 9, "Status": 10, "Source": 24, "Review notes": 32})
    add_sheet(wb, "TravelPlanner_1225", HEADERS["tp"], tp_rows,
              widths={"UID": 20, "Split": 11, "Row": 7, "Origin": 16, "Destination": 16, "Days": 7, "Visiting cities": 8, "Date(s)": 28, "People": 7, "Budget USD": 9, "Level": 8, "Local constraints": 40, "Query": 90, "Has annotated plan": 10, "Has reference info": 10, "Run status": 34, "Source": 30, "Review notes": 32})

    # Reorder sheets.
    wb._sheets = [wb["README"], wb["Index"], wb["All_Cases"], wb["Routing_84"], wb["Planner_17"], wb["Verifier_20"], wb["Editor_8"], wb["Ask_12"], wb["Memory_8"], wb["Efficiency_10"], wb["Edit_vs_Replan_5"], wb["Injection_84"], wb["Cap_Stress"], wb["TravelPlanner_1225"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"{OUT} rows={len(all_rows)} travelplanner={sum(tp_counts.values())} counts={tp_counts}")


if __name__ == "__main__":
    main()
