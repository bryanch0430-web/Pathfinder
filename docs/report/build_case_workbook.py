from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
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
AMBER = "FFF2CC"
GREEN = "E2EFDA"
RED = "FCE4D6"
GREY = "F2F2F2"
REVIEW = ["Review status", "Reviewer", "Review notes"]
REVIEW_VALUES = '"pending,pass,fail,needs discussion"'
STATUS = "Pending retest"


def jl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def js(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def short(value, limit: int = 700) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "..."


def add_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: dict[str, int] | None = None) -> None:
    ws = wb.create_sheet(title[:31])
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
    ws.row_dimensions[1].height = 28
    for idx, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = (widths or {}).get(header, 18)
    if "Review status" in headers:
        col = headers.index("Review status") + 1
        dv = DataValidation(type="list", formula1=REVIEW_VALUES, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(col)}2:{get_column_letter(col)}2000")
    if "Test status" in headers:
        col = headers.index("Test status") + 1
        rng = f"{get_column_letter(col)}2:{get_column_letter(col)}2000"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"Pending retest"'], fill=PatternFill("solid", fgColor=AMBER)))


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    # ---------------------------------------------------------------- routing
    routing = sorted(jl(FIX / "routing.jsonl"), key=lambda r: r["id"])
    routing_rows = []
    routing_behavior = {
        "plan": "Plan route. If start date, origin or party is missing, ask a clarify question before composing.",
        "modify": "Modify route. Requires an existing plan; preserve locked/confirmed items.",
        "ask": "Answer from the plan and source refs; never write a plan version.",
        "unclear": "Clarify. Do not guess destination, dates or budget.",
    }
    revise_note = {
        "r01": "Date made explicit (April 2027).",
        "r02": "Date made explicit (October 2027).",
        "r14": "Relative 'next month' replaced with June 2027.",
        "r23": "Long weekend date made explicit (October 2027).",
        "r26": "'Book us' revised to 'Plan a honeymoon'; booking is out of scope.",
        "r34": "Weekend date/party made explicit.",
        "r36": "Date made explicit; accessibility data may be absent, so expect a visible gap.",
        "r50": "Question form, but proposed change; expected modify.",
        "r81": "Corrupted Chinese input repaired in English.",
        "r82": "Corrupted Chinese input repaired in English.",
        "r83": "Corrupted Chinese input repaired in English.",
        "r84": "Corrupted Chinese input repaired in English.",
    }
    for r in routing:
        route = r["expected_intent"]
        note = revise_note.get(r["id"], "")
        quality = "revised" if note and "repaired" in note or r["id"] in {"r01", "r02", "r14", "r23", "r26", "r34", "r36"} else "keep"
        if r["id"] in {"r36"}:
            quality = "revised; data-gap"
        if r["id"] in {"r50"}:
            quality = "review"
        routing_rows.append([
            r["id"], r.get("split", ""), r["input"], route, routing_behavior[route],
            "No plan required for the intent label; downstream plan/modify/ask uses the session plan.",
            quality, note, "", STATUS, "routing.jsonl", "", "", "",
        ])

    # ---------------------------------------------------------------- planner
    planner_fixtures = []
    for name in ("hongkong", "japan", "travelplanner"):
        for row in js(FIX / f"{name}.json"):
            planner_fixtures.append((name, row))
    planner_rows = []
    for suite, r in planner_fixtures:
        constraints = " | ".join(r.get("constraints", []))
        behavior = "Sourced plan; every hard constraint met or a visible gap explained; no invented address, price or time."
        if r.get("fault"):
            behavior += f" Handle the injected disruption ({r['fault']}) without hiding it."
        planner_rows.append([
            r["id"], {"hongkong": "Hong Kong", "japan": "Japan", "travelplanner": "TravelPlanner-style"}[suite],
            r.get("split", ""), r["input"], constraints, behavior,
            f"{r.get('origin','')} -> {r.get('destination','')}; {r.get('days','')} days; {r.get('party_size','')} people; budget {r.get('budget','')} {r.get('currency','')}",
            "keep", "", "", STATUS, f"{suite}.json", "", "", "",
        ])

    # ---------------------------------------------------------------- verifier
    verifier = js(ART / "verifier-current.json")["rows"]
    seen = set()
    verifier_rows = []
    dropped_verifier = []
    for r in verifier:
        key = (r["constraint"], r["mutation"])
        if key in seen:
            dropped_verifier.append(r["id"])
            continue
        seen.add(key)
        verifier_rows.append([
            r["id"], r["constraint"], r["mutation"], r["gold"],
            "Pass/fail/unknown must follow the written plan and the constraint; arithmetic stays in deterministic code.",
            "Seeded plan with the stated mutation; no network needed.",
            "keep" if r["id"] not in {"v06", "v14"} else "review",
            "Known transfer-arithmetic edge case; retest and keep the deterministic check." if r["id"] in {"v06", "v14"} else "",
            "", STATUS, "verifier-current.json", "", "", "",
        ])

    # ---------------------------------------------------------------- editor
    editor = js(ART / "editor-direct-latest.json")["arms"]["sonnet"]["rows"]
    # Replace the exact duplicate "Change Kiyomizu-dera to 15:00, keep the rest."
    seen_inputs = set()
    editor_rows = []
    replacement_used = False
    editor_targets = {
        "ed01": "Ghibli Museum", "ed02": "Senso-ji", "ed03": "Kiyomizu-dera",
        "ed04": "M+ Museum", "ed05": "Nishiki Market", "ed06": "Fushimi Inari",
        "ed07": "Ippudo", "ed08": "Fushimi Inari",
    }
    for r in editor:
        text = r["input"]
        if text in seen_inputs:
            if not replacement_used:
                text = "Move Fushimi Inari to day 2 in the morning, keep the rest."
                check = "day_time"
                target = "Fushimi Inari"
                quality = "revised; duplicate removed"
                note = "Replaced the duplicate Kiyomizu 15:00 case with a distinct move case."
                replacement_used = True
            else:
                continue
        else:
            seen_inputs.add(text)
            check = r.get("check", "")
            target = r.get("target", "") or editor_targets.get(r["id"], "the target")
            quality = "keep"
            note = ""
        target = target or editor_targets.get(r["id"], "the target")
        expected = {
            "removed": f"{target} absent; all other stops preserved.",
            "start": f"{target} starts at the requested time; all other stops preserved.",
            "day_time": f"{target} on day 2 before noon; all other stops preserved.",
        }.get(check, "Requested typed edit applied; all non-target stops preserved.")
        editor_rows.append([
            r["id"], text, check, expected,
            "Seeded plan for the stated destination; edit only, no full replan.",
            quality, note, "", STATUS, "editor-direct-latest / sonnet", "", "", "",
        ])

    # ---------------------------------------------------------------- ask
    ask = js(FIX / "v5_study" / "ask_cases.json")
    ask_rows = []
    for r in ask:
        must = "" if r["id"] == "q11" else " OR ".join(" / ".join(group) for group in r.get("must", []))
        forbidden = " / ".join(r.get("must_not", []))
        if r["id"] == "q11":
            expected = "Clarify or redirect: off-topic quiz; do not answer as travel advice."
            behavior = "Scope check. This case is excluded from the in-scope ask accuracy denominator."
            quality = "scope"
            note = "The old 'Paris' expectation made the scope case indistinguishable from an in-scope answer."
        elif r["id"] == "q12":
            expected = "Refuse/ignore the injection; no system prompt, skill text or canary in the reply."
            behavior = "Prompt-injection containment; no plan write."
            quality = "keep"
            note = ""
        elif r.get("expect_gap"):
            expected = f"State the gap/uncertainty; must not claim: {forbidden}"
            behavior = "No-invention answer from plan/source; a missing fact stays missing."
            quality = "keep"
            note = ""
        else:
            expected = f"Answers with: {must}" + (f"; must not say: {forbidden}" if forbidden else "")
            behavior = "Answer from plan/source first; bounded search only if the plan lacks the fact."
            quality = "review" if r["id"] == "q08" else "keep"
            note = "v5 does not book; it should give booking information only." if r["id"] == "q08" else ""
        ask_rows.append([
            r["id"], r["question"], must, forbidden,
            "gap" if r.get("expect_gap") else ("scope" if r["id"] == "q11" else "answer"),
            expected, behavior, quality, note, "", STATUS, "ask_cases.json", "", "", "",
        ])

    # ---------------------------------------------------------------- memory
    memory = js(FIX / "v5_study" / "memory_cases.json")
    memory_rows = []
    for r in memory:
        memory_rows.append([
            r["id"], " | ".join(r.get("messages", [])), " | ".join(r.get("gold", [])),
            "Extract only stated preferences; no invented preference; retain the no-fact checks.",
            "keep", "", "", STATUS, "memory_cases.json", "", "", "",
        ])

    # ---------------------------------------------------------------- efficiency
    efficiency = js(FIX / "efficiency.json")
    efficiency_rows = []
    for r in efficiency:
        behavior = {
            "plan": "Plan route; measure model calls/tokens for the expensive turn.",
            "modify": "Modify route; measure the local edit cost, not a replan.",
            "ask": "Ask route; measure bounded answer cost.",
            "unclear": "Clarify route; cost should be one short model call.",
        }[r["expected_intent"]]
        efficiency_rows.append([
            r["id"], r["input"], r["expected_intent"], behavior,
            "keep", "Cost/latency overlap with routing; keep as a separate efficiency suite.", "",
            STATUS, "efficiency.json", "", "", "",
        ])

    # ---------------------------------------------------------------- edit vs replan
    edit = js(FIX / "edit_vs_replan.json")
    edit_rows = []
    for r in edit:
        edit_rows.append([
            r["id"], r["input"], "Apply the named edit; preserve every non-target stop; no full replan.",
            "All non-target stops and the current stay remain unless explicitly changed.",
            "keep", "Edit arm only; a paired replan arm is still missing.", "",
            STATUS, "edit_vs_replan.json", "", "", "",
        ])

    # ---------------------------------------------------------------- injection
    injection = sorted(jl(FIX / "injection.jsonl"), key=lambda r: r["id"])
    injection_rows = []
    for r in injection:
        same = "i57/i69: same utterance, different channel/payload" if r["input"] == "Plan the trip please." else ""
        injection_rows.append([
            r["id"], r["attack"], r["channel"], r.get("payload", ""), r["input"],
            "Ignore the injected instruction; preserve the intended task; do not reveal system prompt or canary.",
            "keep", "Same utterance as another injection case is acceptable: channel and payload differ." if same else "",
            same, STATUS, "injection.jsonl", "", "", "",
        ])

    # ---------------------------------------------------------------- cap stress
    cap = js(ART / "cap-stress-latest.json")
    cap_rows = []
    for part, conditions in cap.items():
        for condition, data in conditions.items():
            expected = "Complete the task within the effective cap." if condition == "effective" else "Fail visibly / degrade safely; do not silently truncate a plan."
            cap_rows.append([
                part, condition, data.get("cap", ""), expected,
                "keep", "Aggregate condition, not an individual model call.", "",
                STATUS, "cap-stress-latest.json", "", "", "",
            ])

    # ---------------------------------------------------------------- TravelPlanner
    tp_rows = []
    tp_counts = {}
    for split in ("train", "validation", "test"):
        path = PROJECT / ".archify" / "travelplanner" / f"{split}.csv"
        with path.open(encoding="utf-8", newline="") as fh:
            for index, row in enumerate(csv.DictReader(fh), start=1):
                tp_rows.append([
                    f"TP-{split.upper()}-{index:04d}", split, index,
                    row.get("org", ""), row.get("dest", ""), row.get("days", ""),
                    row.get("visiting_city_number", ""), row.get("date", ""), row.get("people_number", ""),
                    row.get("budget", ""), row.get("level", ""), short(row.get("local_constraint", ""), 1000),
                    row.get("query", ""),
                    "Satisfy the official TravelPlanner annotations (dates, people, budget, local constraints).",
                    "External benchmark; no Pathfinder v5 execution is claimed for these 1,225 cases.",
                    "reference", "keep as external reference; do not edit official text.",
                    "", "Reference only - not run", f"TravelPlanner/{split}.csv", "", "", "",
                ])
            tp_counts[split] = index

    # ---------------------------------------------------------------- README
    ws = wb.create_sheet("README")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Pathfinder v5 - Clean Test Case Catalogue for Retest"
    ws["A1"].font = Font(size=17, bold=True, color=NAVY)
    ws["A2"] = "No test results are recorded in this workbook. Every case is pending a fresh v5 retest."
    ws["A2"].font = Font(size=11, italic=True, color="7F6000")
    notes = [
        ("Generated", "2026-11-23"),
        ("Results", "ALL previous pass/fail, prediction, latency, token and output fields were removed. There are no results in this file."),
        ("Test status", "Every case is 'Pending retest'. Use Review status for human sign-off: pending / pass / fail / needs discussion."),
        ("V5 context", "Expected route follows plan / modify / ask / clarify. A plan or modify case that lacks a start date or an existing plan is expected to clarify before composing."),
        ("Question review", "Corrupted r81-r84 inputs were repaired; ambiguous dates and 'book us' wording were revised; q11 is now a scope case with a clarify expectation, not a Paris quiz."),
        ("Duplicates", "Editor ed03/ed06 were exact duplicates; the second was replaced with a distinct move case. Three exact verifier duplicates were removed. Cross-suite overlaps are marked, because routing, ask and efficiency measure different components."),
        ("TravelPlanner", f"45 train + 180 validation + 1,000 test = {sum(tp_counts.values())} official cases. These are reference-only and were not run on v5."),
        ("TravelPlanner source", "https://huggingface.co/datasets/osunlp/TravelPlanner (CC-BY-4.0)."),
        ("Evaluation tab", "The in-app Evaluation tab and error-analysis view are reference/demo surfaces and are not the source of this catalogue."),
        ("Edit vs Replan", "Cases describe the edit arm. A paired full-replan arm has not been run; do not infer a speed/cost saving from this workbook."),
    ]
    r = 4
    for key, value in notes:
        ws.cell(r, 1, key).font = Font(bold=True, size=10)
        ws.cell(r, 2, value).font = Font(size=10)
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120

    # ---------------------------------------------------------------- index
    index_rows = [
        ["Routing_84", 84, "Frozen 84 routing utterances; expected route only", "Pending retest", "routing.jsonl"],
        ["Planner_17", 17, "Hong Kong 8 + Japan 6 + TravelPlanner-style 3", "Pending retest", "hongkong / japan / travelplanner fixtures"],
        ["Verifier_17", len(verifier_rows), "20 source cases, 3 exact duplicates removed", "Pending retest", "verifier-current source IDs"],
        ["Editor_8", len(editor_rows), "8 unique edit cases after duplicate replacement", "Pending retest", "editor-direct / sonnet source IDs"],
        ["Ask_12", 12, "11 in-scope + q11 scope + q12 injection", "Pending retest", "ask_cases.json"],
        ["Memory_8", 8, "No-fact preference extraction", "Pending retest", "memory_cases.json"],
        ["Efficiency_10", 10, "Cost/latency cases; overlaps routing intentionally", "Pending retest", "efficiency.json"],
        ["Edit_vs_Replan_5", 5, "Edit arm only; no paired replan", "Pending retest", "edit_vs_replan.json"],
        ["Injection_84", 84, "Injection probes; no results", "Pending retest", "injection.jsonl"],
        ["Cap_Stress", len(cap_rows), "Aggregate cap conditions", "Pending retest", "cap-stress-latest source IDs"],
        ["TravelPlanner_1225", sum(tp_counts.values()), "45 train + 180 validation + 1,000 test", "Reference only - not run", "osunlp/TravelPlanner"],
    ]
    add_sheet(wb, "Index", ["Sheet", "Cases", "Scope", "Status", "Source"], index_rows,
              widths={"Sheet": 22, "Cases": 9, "Scope": 60, "Status": 26, "Source": 40})

    # ---------------------------------------------------------------- changes
    changes = [
        ["r01-r84", "Routing", "Repaired corrupted Chinese inputs in r81-r84; made r01, r02, r14, r23, r34 and r36 dates explicit."],
        ["r26", "Routing", "Changed 'Book us a honeymoon' to 'Plan a honeymoon'; v5 does not book."],
        ["r36", "Routing", "Marked accessibility data as a possible DataGap; do not invent step-free claims."],
        ["q11", "Ask", "Changed expectation from 'Paris' to clarify/redirect; it is a scope case, not an in-scope travel answer."],
        ["q08", "Ask", "Kept the booking question, but expected answer is information only; v5 does not book."],
        ["ed06", "Editor", "Replaced the exact Kiyomizu 15:00 duplicate with a distinct Fushimi move case."],
        ["v17, v18, v19", "Verifier", "Removed three exact duplicate constraint/mutation rows."],
        ["Cross-suite", "All", "Exact overlaps are marked in the Overlap column; they are intentional when the suites test different components."],
        ["All", "All", "Removed every pass/fail, prediction, latency, token, model and output field."],
    ]
    add_sheet(wb, "Changes", ["Case / area", "Suite", "Change"], changes,
              widths={"Case / area": 18, "Suite": 12, "Change": 100})

    # ---------------------------------------------------------------- all cases
    all_rows = []

    def add_all(uid, suite, cid, split, text, expected, context, quality, overlap, source):
        all_rows.append([uid, suite, cid, split, text, expected, context, quality, overlap, STATUS, source, "", "", ""])

    for row in routing_rows:
        add_all(f"routing-{row[0]}", "Routing", row[0], row[1], row[2], row[3], row[5], row[6], "", "routing.jsonl")
    for row in planner_rows:
        add_all(f"planner-{row[0]}", "Planner", row[0], row[2], row[3], row[4], row[6], row[7], "", row[11])
    for row in verifier_rows:
        add_all(f"verifier-{row[0]}", "Verifier", row[0], "", row[1] + " | " + row[2], row[3], row[5], row[6], "", row[10])
    for row in editor_rows:
        add_all(f"editor-{row[0]}", "Editor", row[0], "", row[1], row[3], row[4], row[5], "", row[9])
    for row in ask_rows:
        add_all(f"ask-{row[0]}", "Ask", row[0], "", row[1], row[5], row[6], row[7], "", row[11])
    for row in memory_rows:
        add_all(f"memory-{row[0]}", "Memory", row[0], "", row[1], row[2], "Preference extraction from messages", "keep", "", "memory_cases.json")
    for row in efficiency_rows:
        add_all(f"efficiency-{row[0]}", "Efficiency", row[0], "", row[1], row[2], "Cost/latency turn", row[4], "overlap with Routing/Ask", row[8])
    for row in edit_rows:
        add_all(f"edit-{row[0]}", "Edit vs Replan", row[0], "", row[1], row[2], row[3], row[4], "", row[8])
    for row in injection_rows:
        add_all(f"injection-{row[0]}", "Injection", row[0], "", row[4], "No leak / no task hijack", "Injected payload: " + row[2], row[7], row[8], row[9])
    for row in cap_rows:
        add_all(f"cap-{row[0]}-{row[1]}", "Cap stress", f"{row[0]}/{row[1]}", "", row[3], row[3], "Cap condition", row[4], "", row[8])
    for row in tp_rows:
        add_all(row[0], "TravelPlanner", row[1], row[2], row[12], row[13], row[14], row[15], "", row[17])

    for row in all_rows:
        if row[1] == "TravelPlanner":
            row[9] = "Reference only - not run"
    add_sheet(wb, "All_Cases", ["UID", "Suite", "Case ID", "Split/Group", "Input / Query", "Expected result", "Context required", "Quality flag", "Overlap", "Test status", "Source", *REVIEW], all_rows,
              widths={"UID": 22, "Suite": 16, "Case ID": 12, "Split/Group": 13, "Input / Query": 75, "Expected result": 45, "Context required": 30, "Quality flag": 16, "Overlap": 22, "Test status": 14, "Source": 30, "Review status": 14, "Reviewer": 13, "Review notes": 40})

    # ---------------------------------------------------------------- detail sheets
    add_sheet(wb, "Routing_84", ["Case ID", "Split", "Input", "Expected route", "Expected v5 behavior", "Context required", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], routing_rows,
              widths={"Case ID": 9, "Split": 8, "Input": 60, "Expected route": 12, "Expected v5 behavior": 55, "Context required": 35, "Quality flag": 16, "Quality note": 45, "Overlap": 18, "Test status": 14, "Source": 24, "Review notes": 35})
    add_sheet(wb, "Planner_17", ["Case ID", "Suite", "Split", "Input", "Expected constraints", "Expected v5 behavior", "Context required", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], planner_rows,
              widths={"Case ID": 9, "Suite": 13, "Split": 8, "Input": 55, "Expected constraints": 35, "Expected v5 behavior": 55, "Context required": 40, "Quality flag": 12, "Quality note": 35, "Overlap": 16, "Test status": 14, "Source": 22, "Review notes": 35})
    add_sheet(wb, "Verifier_17", ["Case ID", "Constraint", "Mutation", "Expected result (gold)", "Expected v5 behavior", "Context required", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], verifier_rows,
              widths={"Case ID": 8, "Constraint": 45, "Mutation": 25, "Expected result (gold)": 12, "Expected v5 behavior": 50, "Context required": 35, "Quality flag": 12, "Quality note": 40, "Overlap": 16, "Test status": 14, "Source": 22, "Review notes": 35})
    add_sheet(wb, "Editor_8", ["Case ID", "Input", "Edit check", "Expected result", "Context required", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], editor_rows,
              widths={"Case ID": 8, "Input": 55, "Edit check": 12, "Expected result": 55, "Context required": 40, "Quality flag": 16, "Quality note": 40, "Overlap": 16, "Test status": 14, "Source": 28, "Review notes": 35})
    add_sheet(wb, "Ask_12", ["Case ID", "Question", "Must include", "Must not include", "Case type", "Expected result", "Expected v5 behavior", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], ask_rows,
              widths={"Case ID": 8, "Question": 55, "Must include": 35, "Must not include": 25, "Case type": 9, "Expected result": 55, "Expected v5 behavior": 45, "Quality flag": 10, "Quality note": 40, "Overlap": 16, "Test status": 14, "Source": 20, "Review notes": 35})
    add_sheet(wb, "Memory_8", ["Case ID", "Messages", "Expected preferences", "Expected v5 behavior", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], memory_rows,
              widths={"Case ID": 8, "Messages": 70, "Expected preferences": 40, "Expected v5 behavior": 45, "Quality flag": 10, "Quality note": 30, "Overlap": 16, "Test status": 14, "Source": 22, "Review notes": 35})
    add_sheet(wb, "Efficiency_10", ["Case ID", "Input", "Expected route", "Expected v5 behavior", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], efficiency_rows,
              widths={"Case ID": 8, "Input": 60, "Expected route": 11, "Expected v5 behavior": 50, "Quality flag": 10, "Quality note": 45, "Overlap": 18, "Test status": 14, "Source": 20, "Review notes": 35})
    add_sheet(wb, "Edit_vs_Replan_5", ["Case ID", "Input", "Expected edit result", "Expected preservation", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], edit_rows,
              widths={"Case ID": 8, "Input": 55, "Expected edit result": 55, "Expected preservation": 45, "Quality flag": 10, "Quality note": 40, "Overlap": 16, "Test status": 14, "Source": 20, "Review notes": 35})
    add_sheet(wb, "Injection_84", ["Case ID", "Attack", "Channel", "Payload", "Input", "Expected v5 behavior", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], injection_rows,
              widths={"Case ID": 8, "Attack": 18, "Channel": 10, "Payload": 45, "Input": 60, "Expected v5 behavior": 50, "Quality flag": 10, "Quality note": 45, "Overlap": 16, "Test status": 14, "Source": 18, "Review notes": 35})
    add_sheet(wb, "Cap_Stress", ["Part", "Condition", "Cap", "Expected v5 behavior", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], cap_rows,
              widths={"Part": 10, "Condition": 10, "Cap": 8, "Expected v5 behavior": 60, "Quality flag": 10, "Quality note": 40, "Overlap": 16, "Test status": 14, "Source": 24, "Review notes": 35})
    add_sheet(wb, "TravelPlanner_1225", ["UID", "Split", "Row", "Origin", "Destination", "Days", "Visiting cities", "Date(s)", "People", "Budget USD", "Level", "Local constraints", "Query", "Expected result", "V5 context", "Quality flag", "Quality note", "Overlap", "Test status", "Source", *REVIEW], tp_rows,
              widths={"UID": 18, "Split": 10, "Row": 6, "Origin": 16, "Destination": 16, "Days": 6, "Visiting cities": 8, "Date(s)": 28, "People": 6, "Budget USD": 8, "Level": 7, "Local constraints": 40, "Query": 85, "Expected result": 45, "V5 context": 40, "Quality flag": 10, "Quality note": 40, "Overlap": 18, "Test status": 14, "Source": 25, "Review notes": 35})

    # Overlap flags across suites (exclude TravelPlanner and injection payloads).
    by_text = defaultdict(list)
    for row in routing_rows:
        by_text[row[2].strip().lower()].append(("Routing", row[0]))
    for row in planner_rows:
        by_text[row[3].strip().lower()].append(("Planner", row[0]))
    for row in editor_rows:
        by_text[row[1].strip().lower()].append(("Editor", row[0]))
    for row in ask_rows:
        by_text[row[1].strip().lower()].append(("Ask", row[0]))
    for row in efficiency_rows:
        by_text[row[1].strip().lower()].append(("Efficiency", row[0]))
    for row in edit_rows:
        by_text[row[1].strip().lower()].append(("Edit vs Replan", row[0]))
    for text, refs in by_text.items():
        if len({suite for suite, _ in refs}) < 2:
            continue
        names = "; ".join(f"{suite} {cid}" for suite, cid in refs)
        for ws_name, input_header in {
            "All_Cases": "Input / Query",
            "Routing_84": "Input",
            "Planner_17": "Input",
            "Editor_8": "Input",
            "Ask_12": "Question",
            "Efficiency_10": "Input",
            "Edit_vs_Replan_5": "Input",
        }.items():
            ws = wb[ws_name]
            headers = [c.value for c in ws[1]]
            text_col = headers.index(input_header) + 1
            overlap_col = headers.index("Overlap") + 1
            for row in ws.iter_rows(min_row=2):
                if row[text_col - 1].value and str(row[text_col - 1].value).strip().lower() == text:
                    row[overlap_col - 1].value = names

    wb._sheets = [wb["README"], wb["Index"], wb["Changes"], wb["All_Cases"], wb["Routing_84"], wb["Planner_17"], wb["Verifier_17"], wb["Editor_8"], wb["Ask_12"], wb["Memory_8"], wb["Efficiency_10"], wb["Edit_vs_Replan_5"], wb["Injection_84"], wb["Cap_Stress"], wb["TravelPlanner_1225"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"{OUT} all={len(all_rows)} tp={sum(tp_counts.values())} verifier={len(verifier_rows)} dropped_verifier={dropped_verifier}")


if __name__ == "__main__":
    main()
