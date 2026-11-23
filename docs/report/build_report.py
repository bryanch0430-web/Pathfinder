from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

REPORT_DIR = Path(__file__).resolve().parent
ASSETS = REPORT_DIR / "assets"
OUT = REPORT_DIR / "Pathfinder_Final_Report.docx"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
GREY = RGBColor(0x44, 0x4A, 0x52)
LIGHT = RGBColor(0xEE, 0xF2, 0xF6)


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_text(cell, text: str, *, bold: bool = False, size: float = 8.2, color: RGBColor | None = None) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Times New Roman"
    if color is not None:
        run.font.color.rgb = color


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)
    run.font.size = Pt(8)
    run.font.name = "Times New Roman"
    run.font.color.rgb = GREY


def body(doc: Document, text: str, *, first_indent: bool = True, space_after: float = 4, italic: bool = False) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.08
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if first_indent:
        p.paragraph_format.first_line_indent = Inches(0.16)
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(10)
    run.italic = italic


def heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8 if level == 1 else 5)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.bold = True
    run.font.name = "Times New Roman"
    run.font.size = Pt(11.5 if level == 1 else 10.2)
    run.font.color.rgb = NAVY if level == 1 else GREY


def bullet(doc: Document, text: str, *, level: int = 0) -> None:
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.03
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(9.5)


def numbered(doc: Document, text: str) -> None:
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.03
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(9.5)


def caption(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    run.italic = True
    run.font.name = "Times New Roman"
    run.font.size = Pt(8.2)
    run.font.color.rgb = GREY


def figure(doc: Document, name: str, *, width: float = 5.5, cap: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(1)
    p.add_run().add_picture(str(ASSETS / name), width=Inches(width))
    caption(doc, cap)


def table(doc: Document, headers: list[str], rows: list[list[str]], *, widths: list[float] | None = None, font: float = 8.2, cap: str | None = None) -> None:
    if cap:
        caption(doc, cap)
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = "Table Grid"
    t.autofit = False
    for i, header in enumerate(headers):
        set_cell_text(t.rows[0].cells[i], header, bold=True, size=font, color=RGBColor(0xFF, 0xFF, 0xFF))
        set_cell_shading(t.rows[0].cells[i], "1F3A5F")
    for row in rows:
        cells = t.add_row().cells
        for i, value in enumerate(row):
            set_cell_text(cells[i], str(value), size=font)
            if len(t.rows) % 2 == 0:
                set_cell_shading(cells[i], "EEF2F6")
    if widths:
        for row in t.rows:
            for i, width in enumerate(widths):
                row.cells[i].width = Inches(width)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.0
    run = p.add_run(" ")
    run.font.size = Pt(2)


doc = Document()
section = doc.sections[0]
section.page_width = Inches(8.5)
section.page_height = Inches(11)
section.top_margin = Inches(0.9)
section.bottom_margin = Inches(0.9)
section.left_margin = Inches(1.25)
section.right_margin = Inches(1.25)

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Times New Roman"
normal.font.size = Pt(10)
normal.paragraph_format.line_spacing = 1.08
normal.paragraph_format.space_after = Pt(4)

footer = section.footer.paragraphs[0]
add_page_number(footer)
header = section.header.paragraphs[0]
header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
hr = header.add_run("COMP7608B · Group 1 · Pathfinder")
hr.font.name = "Times New Roman"
hr.font.size = Pt(8)
hr.font.color.rgb = GREY

# Title block
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(2)
r = p.add_run("Pathfinder: A State-Aware Agentic Trip Planner")
r.bold = True
r.font.name = "Times New Roman"
r.font.size = Pt(17)
r.font.color.rgb = NAVY

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(8)
r = p.add_run("Final report · COMP7608B · Group 1")
r.font.name = "Times New Roman"
r.font.size = Pt(10)
r.font.color.rgb = GREY

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(1)
r = p.add_run("Chan Yuk Ming · Lai Helen · Wong Man Kit · Ng Tsan Fung")
r.font.name = "Times New Roman"
r.font.size = Pt(9.5)
r.bold = True

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(8)
r = p.add_run("The University of Hong Kong · 23 November 2026")
r.font.name = "Times New Roman"
r.font.size = Pt(9)
r.font.color.rgb = GREY

# Abstract
p = doc.add_paragraph()
p.paragraph_format.left_indent = Inches(0.35)
p.paragraph_format.right_indent = Inches(0.35)
p.paragraph_format.space_after = Pt(7)
p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
r = p.add_run("Abstract. ")
r.bold = True
r.font.name = "Times New Roman"
r.font.size = Pt(9.5)
r = p.add_run(
    "Pathfinder is a planning agent for multi-day travel. It turns one conversational request into a "
    "versioned itinerary by routing the turn, gathering source-labelled evidence, composing only from "
    "sourced candidates, validating the result, and asking the traveller when the request is underspecified. "
    "We describe the delivered system, the parts of the proposal that changed, and the tests that support "
    "the claims. The core design follows the workflow-first pattern rather than a free ReAct loop: the "
    "orchestrator owns the path and the budget; the model supplies judgement at typed boundaries. On frozen "
    "component suites, the shipped default achieves 97.0% routing accuracy across two current runs, 14/17 "
    "planner cases, 8/8 editor cases, 18/20 verifier cases and 11/11 in-scope quick answers. The verifier's "
    "two failures are transfer-arithmetic cases, and routing degrades on ambiguous requests. We therefore "
    "report the system as a bounded, auditable planner rather than an autonomous travel agent."
)
r.font.name = "Times New Roman"
r.font.size = Pt(9.5)

# 1 Introduction
heading(doc, "1  Introduction and continuity", 1)
body(
    doc,
    "Travellers already know the destination long before they know the itinerary. The work that remains is "
    "fragmented across hotels, flights, maps, opening hours, weather and notes, and a change to one input can "
    "invalidate several downstream choices. The proposal identified four needs: one integrated planning "
    "surface; iterative personalisation; cheap handling of repeated low-level requests; and durable state so "
    "a useful plan can be revised rather than rebuilt. The delivered product addresses those needs through a "
    "single chat/plan/map workspace, a typed turn router, parallel evidence workers, a sourced-only composer, "
    "deterministic validation and versioned plans.",
)
body(
    doc,
    "The core objective did not change, but several implementation choices did. Table 1 records the "
    "planned/delivered comparison. The most consequential change is that the long-term memory uses the "
    "application store and a preference profile, not the proposed vector database with a reranker. The "
    "second is that the model portfolio changed with provider availability: the delivered default uses Claude "
    "Sonnet 5.5 for the writer path and DeepSeek Flash for the constraint verifier, with Gemini Flash-Lite and "
    "Jev available as tested options. The third is that booking and payment were never in scope and remain "
    "read-only gaps, not simulated actions. These are implementation-level deviations; the user problem and "
    "the deliverable boundary are unchanged. We record them here so that the final artefact can be assessed "
    "against what was actually built, rather than against the provisional model names in the proposal.",
)
table(
    doc,
    ["Proposal element", "Delivered in this report", "Status / reason"],
    [
        ["Jev System 1 router, typed choice + confidence", "LLM analyst/router by default; Jev optional; two separate confidence thresholds", "Delivered; Jev not default"],
        ["Grok 4.7 for agents, planner and chat", "Sonnet 5.5 writer default; Flash-Lite, DeepSeek and Jev tested as alternatives", "Changed after provider access and component study"],
        ["Four pre-planning agents", "Five evidence workers: places, stays, weather, tickets, videos", "Delivered and parallelised"],
        ["Planner with bounded JSON repair", "Composer + deterministic validator + constraint verifier; repair ≤ 2", "Delivered; evaluator-optimizer pattern"],
        ["Vector DB + reranker for useful trips", "SQLite plan versions + preference memory; no vector store", "Scope reduction; disclosed"],
        ["Langfuse observability", "Built-in turn trace, SSE trace and Why fold; Langfuse not integrated", "Simplified; trace data remains"],
        ["TravelPlanner full split and 70/30 custom split", "Small frozen suites: 84 routing, 17 planner, 20 verifier, 8 editor, 11 ask, 8 memory", "Screening evidence, not a powered benchmark"],
        ["Autonomous booking / payment", "No booking; external reads only", "Out of scope; no unsafe action"],
    ],
    widths=[1.7, 2.3, 1.75],
    cap="Table 1. Planned versus delivered. Deviations are implementation decisions, not a change of the user problem.",
)

body(
    doc,
    "One further deviation is the response to ambiguous requests. The proposal described a low-confidence "
    "router that asks the user before acting. The delivered system keeps that behaviour, but separates the "
    "analyst's reported confidence from Jev's acceptance confidence: they are not comparable, and the analyst "
    "prompt is no longer told a numeric threshold. This avoids anchoring the model on the gate, although the "
    "current routing runs show a small accuracy cost on unclear utterances (Section 6).",
)

# 2 Related and design
heading(doc, "2  Related work and design rationale", 1)
body(
    doc,
    "The design follows Anthropic's distinction between workflows, where LLMs and tools follow predefined "
    "code paths, and agents, where the model directs its own process [1]. Pathfinder is deliberately a "
    "workflow on the outside: the turn router selects a path, the evidence workers are fixed adapters, and "
    "the validator owns the stop condition. The model is used inside the workflow for judgement that cannot "
    "be enumerated: reading intent, proposing an edit, selecting among sourced candidates and auditing a "
    "written constraint. The bounded repair step is an evaluator–optimizer pattern [1].",
)
body(
    doc,
    "We did not use LangGraph or a general agent framework. The application is a single Python process with "
    "typed Pydantic contracts, one SSE turn stream, a SQLite state store and explicit counters for seats, "
    "repairs and wall-clock budget. A framework would add an abstraction layer over prompts and tool calls "
    "while leaving the hard parts—schema validation, source provenance, lock preservation, deterministic "
    "dates and arithmetic—outside the graph. The engineering handbook makes the same point in its agent-vs-"
    "workflow section: draw the workflow on the outside and give an agent a fenced sandbox only where the "
    "path is genuinely unknown [2]. Our use of a free agent loop is therefore restricted to bounded repair "
    "within a single planner step.",
)
body(
    doc,
    "The academic framing follows the course vocabulary: intent routing, context builder, evidence workers, "
    "planner, validation, constraint verification, runtime harness and evaluation. The planner is not an "
    "autonomous multi-agent swarm: no worker can spawn another worker, no model chooses an arbitrary tool, "
    "and every model call is traced. This is a narrower claim than the proposal's 'agentic system', but it "
    "is the claim the implementation and the evidence support.",
)

# 3 Implementation
heading(doc, "3  Design and implementation", 1)
heading(doc, "3.1  One turn, one path", 2)
body(
    doc,
    "A turn starts at the React workspace and enters the FastAPI edge. The orchestrator masks personal data, "
    "loads the trip context, the latest plan and recent messages, and claims a per-session seat. The analyst "
    "then returns a typed intent, slot update, constraints and a clarification decision. The four routes are:",
)
numbered(doc, "plan: run the parallel evidence workers, compose a day plan from sourced candidates, validate structure, audit constraints, repair within budget, persist a version and stream the reply;")
numbered(doc, "modify: gather only the evidence needed for the change, propose typed edit operations, preserve locked or confirmed items, and write a new version only after the traveller accepts;")
numbered(doc, "ask: answer from the plan first; issue at most two bounded web queries (four results each) when the plan lacks a fact; never write a plan version;")
numbered(doc, "clarify: ask only for the missing or ambiguous slot, keep the known details, and do no research or plan write.")
body(
    doc,
    "Figure 1 is the delivered journey map. Figure 2 is the data-flow view: it separates the words and files "
    "that enter, the privacy mask and trip context, the source records, the plan version and the reply. The "
    "figures are explanatory abstractions of the code paths in Appendix C, not screenshots of a user session.",
)
figure(doc, "fig-architecture.png", width=5.5, cap="Figure 1. The delivered turn path. The model is called at the Understand, Evidence and Composer boundaries; the orchestrator owns routing, budget, validation, persistence and handoff.")
figure(doc, "fig-dataflow.png", width=5.2, cap="Figure 2. Data flow. Personal details are masked before model calls; source records carry provenance and freshness; only validated plan versions reach the UI.")

heading(doc, "3.2  State, grounding and tools", 2)
body(
    doc,
    "TripContext is the mutable request state: destination, origin, dates, length, party, budget, currency, "
    "preferences, hard constraints, ordered stops and finalisation. TripPlan is the rendered state: days, "
    "items, hotels, tickets, legs, costs, constraints, gaps and an assessment. Plans are append-only versions "
    "in SQLite (WAL); a new edit, a restored version or an accepted proposal creates a version, while a "
    "rejected proposal does not. A seat row prevents two turns from writing the same session concurrently.",
)
body(
    doc,
    "Evidence is the only route by which a fact can enter a plan. An Evidence record carries a name, source "
    "references, a freshness timestamp, a data status and the facts returned by a provider. The composer may "
    "choose only candidate refs; code copies the address, time, price and source. A failed lookup becomes a "
    "visible DataGap, not a model-filled sentence. The five workers are fixed adapters (places, stays, "
    "weather, flights and videos), run concurrently, and share a per-turn deadline. Web search is a code "
    "adapter, not a model tool: SerpAPI Google results are tried first, Wikipedia second, and DeepSeek Search "
    "only as a final fallback when both return nothing. The model writes the query; it does not browse.",
)
body(
    doc,
    "The constraint verifier is a second model pass. It receives the plan text with source lines, the "
    "traveller's hard constraints and the cost summary, and returns a typed pass/fail/unknown row for each "
    "constraint. It is not the reserved Judge model, and it is not a general LLM-as-a-judge for the report. "
    "In the shipped default it runs on DeepSeek Flash with its own key and endpoint; if the plan has no hard "
    "constraints it is skipped, and the Why trace says so. A verifier endpoint failure is also recorded, but "
    "the current behaviour is fail-open: the plan proceeds and the limitation is visible in the trace.",
)

heading(doc, "3.3  Models, prompts and configuration", 2)
table(
    doc,
    ["Role", "Shipped default", "Temperature / cap", "Boundary"],
    [
        ["Writer / analyst / planner / editor / ask / memory", "Claude Sonnet 5.5", "0.2 / 4,096 (per-part cap can override)", "OpenAI-compatible provider; typed JSON at each purpose"],
        ["Constraint verifier", "DeepSeek Flash", "0 / 1,500", "Own endpoint/key; pass/fail/unknown per constraint"],
        ["Turn router", "Analyst LLM; Jev optional", "0.0 for tested arms", "One path per message; no tool choice"],
        ["Web search", "SerpAPI → Wikipedia → DeepSeek Search", "—", "Code executor; 2 queries × 4 results in ask"],
        ["Memory extraction", "Writer model with no-invention check", "0 / 700", "After finalisation; cap 20 preferences"],
    ],
    widths=[1.55, 1.8, 1.2, 1.35],
    cap="Table 2. Shipped model configuration. The part-model study tested a lower-cost profile (Flash-Lite analyst/planner/editor; DeepSeek ask/memory) but it is not the shipped default in this build.",
)
body(
    doc,
    "Prompts are markdown skills with a typed output schema. JSON is parsed defensively, validated by "
    "Pydantic, repaired once on a parse or validation error and otherwise failed loudly. The analyst prompt "
    "does not contain a numeric confidence threshold; the policy is applied after the call. The verifier "
    "prompt receives the plan as written, not a hidden chain of thought, and must return a row for every "
    "constraint. The ask prompt treats retrieved snippets as data and schema-invalid replies as failures, "
    "not as a reason to invent facts.",
)

heading(doc, "3.4  Safety and human oversight", 2)
body(
    doc,
    "Personal data is masked before storage and before a model call. A canary token makes prompt leakage a "
    "string check; a typed route means an untrusted message cannot select an arbitrary tool; the search, "
    "places, weather and flight adapters are read-only. The human-in-the-loop path is a real Desk queue, not "
    "a decorative button: a chat can hand off on an explicit request, a frustration threshold or repeated "
    "stuck edits; an operator can take over, leave a note, run the workflow, release a draft or hand back. "
    "Finalisation freezes the plan and moves the chat to history. There is no autonomous booking, payment or "
    "external write in the current design.",
)

# 4 Reliability
heading(doc, "4  Reliability and recovery", 1)
body(
    doc,
    "We report two injected failure situations required by the brief, plus two acceptance bugs found while "
    "using the delivered system. The first is a flight-boundary violation: a stop scheduled before the "
    "selected arrival or after the selected departure. The deterministic validator emits "
    "before_flight_arrival or after_flight_departure; the turn sends the problem back to the composer within "
    "the repair budget; if repair fails, the plan still carries the visible problem rather than hiding it. "
    "The three boundary tests cover an early stop, a late stop and a day fully inside the flight window.",
)
body(
    doc,
    "The second is a whole-turn timeout. The runtime budget defaults to 300 s, with a minimum of 60 s and a "
    "maximum of 900 s. The API test holds a turn open, verifies that the stream emits turn_timeout, and "
    "verifies that the seat is released for the next turn. This is a safe stop: unfinished work is cancelled "
    "rather than left writing after the reply. A smaller 60 s floor prevents an accidental near-zero budget. "
    "The cost is that a slow planner can lose a partially completed turn; the user receives an explicit "
    "failure and can retry.",
)
table(
    doc,
    ["Situation", "Detection", "Bounded response", "Evidence"],
    [
        ["Stop before landing / after departure", "before_flight_arrival; after_flight_departure", "Return to composer; repair ≤ 2; visible problem otherwise", "test_flight_windows.py: 3/3 pass"],
        ["Turn exceeds budget", "turn_timeout from the harness", "Cancel work; release seat; explicit stream error", "test_api.py: timeout and seat-release test"],
        ["Verifier unavailable / no constraints", "LLMError or empty constraint list", "Skip audit; record Why line; fail-open disclosed", "test_models.py: trace line; live 18/20"],
        ["Locking the current hotel", "choose endpoint rejected current stay", "Accept current stay; preserve other hotels", "test_api.py: regression test added"],
        ["Selected stop not used in answer", "ask payload omitted selected_stop", "Pass selected context; prompt resolves 'this'", "live acceptance: answer names One Dim Sum"],
    ],
    widths=[1.4, 1.55, 1.85, 1.1],
    cap="Table 3. Failure and recovery checks. The last two were acceptance bugs, not simulated failures; both were backend defects, not UI-only defects.",
)
body(
    doc,
    "The two acceptance bugs deserve emphasis because they are the kind of failure a component benchmark "
    "misses. The UI correctly sent the selected item to the analyst, but the ask path dropped it; the answer "
    "therefore treated 'this' as unresolved. The hotel lock failed because the choose endpoint searched only "
    "the alternative list and not the current stay; the UI request was correct. Both are now regression-tested. "
    "Repeated external actions are limited by design: evidence adapters are read-only, search is capped, and "
    "the only durable writes are local SQLite versions and preference memory. The main residual risk is "
    "therefore a stale or incomplete external fact, which is surfaced as a gap rather than retried forever.",
)

# 5 NFR
heading(doc, "5  Non-functional requirements", 1)
body(
    doc,
    "The targets below are operating assumptions, not service-level guarantees. They are drawn from the "
    "runtime configuration and the component study. Latency is measured per turn or per model call on the "
    "study host; provider load, network distance and model choice dominate the spread.",
)
table(
    doc,
    ["Requirement", "Target / implemented measure", "Observed evidence", "Trade-off"],
    [
        ["Bounded turn", "300 s default; 60–900 s; seat held once", "timeout test; no overrun", "Slow planner may lose partial work"],
        ["Routing latency", "≈4 s p50 on the tested provider", "3.97–3.99 s p50 current", "Faster model is less accurate on unclear"],
        ["Ask latency", "≤20 s p50 goal", "16.1 s p50 (DeepSeek); 20.2 s (Sonnet)", "Faster answer trades some scope handling"],
        ["Verifier latency", "≤4 s p50", "1.96 s p50 current", "Small audit, not arithmetic"],
        ["Cost", "Token/call counts; pricing not fixed", "3,017 mean routing tokens; per-part caps", "No dollar figure claimed"],
        ["Workload", "One active turn per session; one seat", "seat claim and timeout test", "Concurrent edit waits"],
        ["Privacy", "Mask HKID, phone, email, card before model/storage", "PII tests pass; canary test pass", "Some free-form sensitive text remains user-controlled"],
        ["Grounding", "Every stop/stay has source refs; gap otherwise", "planner evidence rule; source-only composer", "Coverage can be lower than a fully generative plan"],
    ],
    widths=[1.15, 1.85, 1.7, 1.2],
    cap="Table 4. Non-functional checks. Dollar cost is not reported because provider pricing was not fixed in this environment.",
)
body(
    doc,
    "The central trade-off is latency and coverage against groundedness. Grounding every stop increases the "
    "number of provider calls and produces visible gaps; generating from parametric knowledge would be faster "
    "and more fluent but would make the plan unverifiable. We choose the slower, auditable behaviour. The "
    "ask path is deliberately bounded rather than a general web agent: it will say that it cannot confirm a "
    "fact instead of guessing.",
)

# 6 Evaluation
heading(doc, "6  Evaluation", 1)
body(
    doc,
    "We use frozen, project-specific suites because they isolate the component whose behaviour changed. "
    "Headline scores are deterministic or exact-match; no LLM-as-a-judge is used for the main tables. The "
    "constraint verifier is a model audit with explicit pass/fail/unknown criteria and a human check on its "
    "20 cases; the reserved Judge model is not invoked. All runs are one run per case unless stated. Wilson "
    "95% intervals are reported for routing; the other suites are too small for powered claims.",
)
table(
    doc,
    ["Suite (n)", "Current result", "Comparison / baseline", "Interpretation"],
    [
        ["Routing (84)", "81/84 = 96.4%; second run 82/84 = 97.6%", "Frozen Sonnet 83/84; earlier Flash 83/84", "Prompt change and unclear-utterance variance; all misses are 'unclear'"],
        ["Planner (17)", "14/17 = 82.4%", "Sonnet 14/17; Flash 14/17", "Same pass count; different failures"],
        ["Editor (8)", "8/8 = 100%", "all three arms 8/8", "Small, remove/move/time only"],
        ["Verifier (20)", "18/20 = 90%; 2 false passes, 0 false alarms", "Sonnet 18/20; Flash 17/20", "Two transfer-arithmetic misses (v06, v14)"],
        ["Ask (11 in scope)", "11/11 = 100%", "Sonnet 11/11; Flash 10/11", "q11 scope case excluded; human-adjudicated"],
        ["Memory (8)", "91.7% recall with optional DeepSeek; 79.8% shipped Sonnet", "Sonnet 79.8%; DeepSeek 1 invented preference", "Optional profile not shipped; two no-fact checks"],
    ],
    widths=[1.1, 1.45, 1.65, 1.7],
    cap="Table 5. Component evidence. Routing is the only suite re-run twice after the prompt change; the other frozen suites are reported as component study results, not as a single end-to-end run.",
)
figure(doc, "fig-results.png", width=5.2, cap="Figure 3. Component pass rates on different case sets. Routing uses the mean of two current 84-case runs; planner/editor/verifier/ask use the frozen component suites.")
figure(doc, "fig-verifier.png", width=5.2, cap="Figure 4. Verifier outcome on 20 frozen cases. Both errors are false passes on transfer arithmetic (10 min and 29 min against a 30 min constraint), not false alarms.")

body(
    doc,
    "The strongest positive result is not a single percentage: it is the provenance discipline. In the planner "
    "suite, all accepted plans used sourced candidates and source lines; the composer cannot write an address, "
    "price or opening time that was not returned. The strongest negative result is also clear: the model "
    "verifier does not perform arithmetic. Both v06 (10-minute transfer) and v14 (29-minute transfer) passed "
    "a 30-minute constraint. That is why the deterministic validator, not the verifier, owns travel-time and "
    "date checks. A model-only audit would be unsafe.",
)
body(
    doc,
    "Routing is the least stable result. The two current runs score 81/84 and 82/84; the earlier frozen run "
    "scored 83/84. All current misses are unclear utterances, where the analyst chooses a concrete path too "
    "readily. This is consistent with the proposal's concern about ambiguous input and with the model-study "
    "finding that confidence is not a calibrated probability. The split threshold is a policy setting, not a "
    "guarantee; the current design would need a larger held-out set before claiming a reliable operating point.",
)
body(
    doc,
    "The baseline is component-level, not an end-to-end alternative system. Sonnet, Flash-Lite and DeepSeek "
    "were compared on the same frozen cases; the default writer remains Sonnet while the verifier uses "
    "DeepSeek. The low-cost profile from the study is supported by the same UI and environment settings, but "
    "it is not the shipped default, so we do not present its numbers as the product's headline. This is a "
    "fair controlled component comparison, not a claim that one model is generally better.",
)
body(
    doc,
    "Representative failure cases are retained in the appendix: v06/v14 (verifier false passes), the "
    "unclear routing cases r68/r71/r80, the q11 off-topic ask case, and the two acceptance bugs. The manual "
    "adjudication of q01/q05 in the ask suite is disclosed in the appendix; no model judged its own answer.",
)

# 7 Contributions
heading(doc, "7  Contributions and attribution", 1)
body(
    doc,
    "The repository history in the submitted checkout is squashed under one shared GitHub identity, so "
    "commit-level attribution is not independently recoverable. The table records the team's declared "
    "workstreams; all members should confirm it before submission. Percentages are deliberately omitted.",
)
table(
    doc,
    ["Member", "Declared primary workstream", "Secondary contribution"],
    [
        ["Chan Yuk Ming", "Agent runtime and integration; orchestrator, validation/repair, final integration", "Test design and report review"],
        ["Lai Helen", "Evaluation design and analysis; fixtures, component study, evidence tables", "Prompt review and result adjudication"],
        ["Wong Man Kit", "Product interface; Chat/Plan/Trip map, Direct edit, Desk/HITL, accessibility", "Acceptance testing and visual review"],
        ["Ng Tsan Fung", "Evidence and state; providers, place corpus, SQLite/versioning, privacy", "Deployment and configuration"],
    ],
    widths=[1.15, 2.55, 1.9],
    cap="Table 6. Declared contribution allocation. The shared repository account prevents independent commit-level verification.",
)
body(
    doc,
    "Reused material: the React interface is carried from the earlier Pathfinder line; the backend uses "
    "FastAPI, Pydantic, httpx, SQLite, React/Vite and Archify. Evidence comes from Google Places, Open-Meteo, "
    "SerpAPI, Wikimedia and the curated place corpus. Jev is TypeSafe's decision model. TravelPlanner is the "
    "open benchmark referenced in the proposal. The AI Engineer Handbook and Anthropic's building-agents "
    "article informed the architecture vocabulary; neither is a source of code. The diagrams and this "
    "report were drafted with an AI coding/report assistant and checked against the repository and tests; "
    "the authors remain responsible for every claim.",
)
body(
    doc,
    "Unfinished or mocked functionality is disclosed rather than hidden: no autonomous booking or payment; "
    "intercity transport has no live price or booking source; the curated corpus covers a limited city set; "
    "the Judge model is reserved and unused; the hybrid router is an experimental shadow, not shipped; the "
    "vector RAG and Langfuse layers from the proposal were not integrated; the part-model low-cost profile is "
    "tested but not the default; and the full TravelPlanner split, 70/30 custom split and 84-probe injection "
    "suite are not claimed. The report's numbers are therefore screening evidence, not a production "
    "certification.",
)

# 8 Conclusion
heading(doc, "8  Conclusion", 1)
body(
    doc,
    "Pathfinder delivers a bounded, state-aware trip planner: typed routing, parallel evidence, sourced-only "
    "composition, deterministic validation, bounded repair, versioned plans, a human Desk and an inspectable "
    "turn trace. The design follows the simplest agentic pattern that supports the problem rather than an "
    "autonomous swarm, and the evaluation shows both its value and its limits. The system is reliable where "
    "the constraints are explicit and the evidence is available; it is weaker on ambiguous intent and "
    "arithmetic that the model should never own. The next engineering steps are a larger held-out routing "
    "set, a fail-closed verifier policy, a live evaluation of the optional low-cost model profile, and "
    "source-backed intercity transport.",
)

# References
heading(doc, "References", 1)
refs = [
    "[1] Anthropic. Building effective agents. Research note, 2024 (updated 2026). https://www.anthropic.com/research/building-effective-agents",
    "[2] Me0wX. AI Engineer Handbook. GitHub, 2026. https://github.com/Me0wX-LR/AI_Engineer_Handbook_Me0wX",
    "[3] J. Xie, K. Zhang, J. Chen, T. Zhu, R. Lou, Y. Tian, Y. Xiao, and Y. Su. TravelPlanner: A benchmark for real-world planning with language agents. ICML, 2024. arXiv:2308.13206.",
    "[4] D. Almeida. Introducing System One Models & Jev. TypeSafe AI, 15 September 2026. https://typesafe.ai/blog/introducing-system-one-models-and-jev",
    "[5] L. Zheng et al. Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. NeurIPS 36, Datasets and Benchmarks, 2023.",
    "[6] C. Guo, G. Pleiss, Y. Sun, and K. Q. Weinberger. On calibration of modern neural networks. ICML, 2017. arXiv:1706.04599.",
    "[7] M. Xiong, Z. Hu, X. Lu, Y. Li, J. Fu, J. He, and B. Hooi. Can LLMs express their uncertainty? An empirical evaluation of confidence elicitation in LLMs. ICLR, 2024. arXiv:2306.13063.",
    "[8] Y. Geifman and R. El-Yaniv. Selective classification for deep neural networks. NeurIPS, 2017. arXiv:1705.08500.",
    "[9] DeepSeek. DeepSeek API documentation, 2026. https://api-docs.deepseek.com/",
    "[10] Google. Places API (New) documentation. https://developers.google.com/maps/documentation/places/web-service",
    "[11] Open-Meteo. Weather forecast API. https://open-meteo.com/",
    "[12] SerpAPI. Google Search API. https://serpapi.com/",
    "[13] Wikimedia Foundation. MediaWiki API. https://www.mediawiki.org/wiki/API:Main_page",
    "[14] Pathfinder repository, branch pathfinder_v5, commit 9801670 (23 November 2026). https://github.com/bryanch0430-web/Pathfinder/tree/pathfinder_v5",
    "[15] Archify. Diagram renderer and authoring skill, 2026. https://github.com/tt-a1i/archify",
]
for ref in refs:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    p.paragraph_format.first_line_indent = Inches(-0.25)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.0
    r = p.add_run(ref)
    r.font.name = "Times New Roman"
    r.font.size = Pt(8.4)
    r.font.color.rgb = GREY

# Appendix
doc.add_page_break()
heading(doc, "Appendix A  Test protocol and raw results", 1)
body(
    doc,
    "All tests were run on the submitted branch with the repository virtual environment; no network is "
    "required for the offline pytest suite. Provider-dependent component runs used the settings recorded in "
    "Appendix B. The routing runs were repeated because the analyst prompt changed after the frozen study. "
    "The verifier was re-run with the shipped DeepSeek endpoint. The other component suites are frozen "
    "results from the 23 November part-model study and are labelled as such.",
)
table(
    doc,
    ["Run", "Setting", "Result", "Date / note"],
    [
        ["pytest backend", "Sandbox; no network; current tree", "83 passed", "23 Nov 2026, current tree"],
        ["Routing run 1", "Flash-Lite 0.0; 84 cases", "81/84 (96.4%); p50 3,967 ms", "23 Nov 2026 15:50 HKT"],
        ["Routing run 2", "Flash-Lite 0.0; 84 cases", "82/84 (97.6%); p50 3,985 ms", "23 Nov 2026 15:52 HKT"],
        ["Verifier", "DeepSeek Flash; 20 cases; T=0; cap 1,500", "18/20; 2 false pass; 0 false alarm; p50 1,958 ms", "23 Nov 2026 15:05 HKT"],
        ["Planner", "17 cases; Sonnet and Flash frozen study", "14/17 each", "Part-model study, 23 Nov"],
        ["Editor", "8 cases; three arms", "8/8 each", "Part-model study, 23 Nov"],
        ["Ask", "11 in-scope; three arms", "11/11 Sonnet/DeepSeek; 10/11 Flash", "Part-model study, 23 Nov"],
        ["Memory", "8 cases", "Sonnet 79.8%; DeepSeek 91.7% with 1 invented preference", "Part-model study, 23 Nov"],
    ],
    widths=[1.0, 1.9, 1.65, 1.25],
    cap="Table A1. Runs used in the report. Routing and verifier are current; the other component rows are frozen study results.",
)
table(
    doc,
    ["Verifier case", "Constraint", "Mutation", "Gold", "DeepSeek result"],
    [
        ["v06", "30 min between Fushimi Inari and Nishiki Market", "10-minute transfer", "fail", "pass (false pass)"],
        ["v14", "30 min between Fushimi Inari and Nishiki Market", "29-minute transfer", "fail", "pass (false pass)"],
        ["v01-v05, v07-v13, v15-v20", "budget / inclusion / source / date constraints", "good or injected", "mixed", "correct (18)"],
    ],
    widths=[1.1, 2.1, 1.3, 0.6, 1.2],
    cap="Table A2. The two verifier errors are the same transfer-arithmetic cases across model arms. Exact-match rows are in verifier-current.json.",
)
heading(doc, "Appendix B  Settings, prompts and search path", 1)
body(
    doc,
    "The current runtime defaults are: Claude Sonnet 5.5 as writer/analyst/planner/editor/ask/memory; "
    "DeepSeek Flash as constraint verifier; temperature 0 for verifier and tested structured parts; per-part "
    "caps of 4,096 (analyst), 12,000 (planner), 2,000 (editor), 1,500 (verifier), 1,400 (ask), 700 (memory). "
    "The plan validator is deterministic; the constraint verifier is the only model audit. The orchestrator "
    "holds a seat, a whole-turn deadline and a repair cap. The optional Jev router is off by default; the "
    "hybrid Jev/analyst agreement experiment is a shadow result, not a shipped setting.",
)
table(
    doc,
    ["Parameter", "Value", "Why"],
    [
        ["Turn deadline", "300 s default; 60–900 s", "Bound the whole turn, not one model call"],
        ["Repair attempts", "≤2", "Bounded evaluator–optimizer loop"],
        ["Search", "2 queries × 4 results in ask; 2 × 3 in research", "Bound latency and provider cost"],
        ["Verifier", "DeepSeek Flash; T=0; cap 1,500; own key", "Independent model family; fixed audit schema"],
        ["PII", "HKID, HK phone, email, Luhn card masks", "First-layer privacy before model calls"],
        ["Memory", "Similarity cutoff 0.72; cap 20", "Avoid weak or duplicated preferences"],
        ["Search order", "SerpAPI → Wikipedia → DeepSeek Search", "DeepSeek is final fallback only"],
    ],
    widths=[1.4, 2.3, 1.7],
    cap="Table B1. Settings that shape the reported behaviour.",
)
body(
    doc,
    "The verifier prompt asks for a row per constraint with status pass/fail/unknown and a short evidence "
    "sentence. It is instructed to audit the plan as written, not to re-plan it. The ask prompt treats "
    "retrieved snippets as untrusted data, uses the runtime clock for current dates, and says when a fact "
    "cannot be confirmed. The planner prompt receives candidate refs and a source-labelled bundle; the "
    "composer may not write a fact that is absent from the bundle.",
)
heading(doc, "Appendix C  Claim-to-code map", 1)
table(
    doc,
    ["Claim", "Code path / evidence"],
    [
        ["Typed intent routing", "app/understand/pipeline.py: understand, _route; app/understand/schemas.py"],
        ["Parallel evidence workers", "app/plan/agents.py; app/evidence/providers.py; app/plan/builder.py"],
        ["Sourced-only plan", "app/plan/builder.py: build_plan; evidence models; refs copied into PlanItem"],
        ["Deterministic validation", "app/plan/validator.py: check_structure, hours_violation, flight windows"],
        ["Constraint audit", "app/plan/validator.py: audit_constraints; app/skills/verify.md"],
        ["Bounded repair", "app/turn/orchestrator.py: _plan_turn; MAX_REPAIRS; fallback_plan"],
        ["Versioned state", "app/core/store.py: plans append-only; app/domain/models.py: TripPlan"],
        ["Selected stop context", "app/turn/orchestrator.py: _ask_turn payload; app/skills/ask.md"],
        ["Current-hotel lock", "app/api/journeys.py: choose_option hotel branch; test_api.py regression"],
        ["Turn timeout / seat", "app/api/turn.py; test_api.py timeout test"],
        ["DeepSeek search fallback", "app/evidence/providers.py: _deepseek_search; providers.search"],
        ["DeepSeek verifier", "app/core/llm.py: provider_for; app/config.py: VERIFIER_*"],
        ["HITL Desk", "app/turn/orchestrator.py: _handoff; app/api/admin.py; frontend learn/desk"],
        ["PII masking", "app/security/pii.py; test_safety.py"],
    ],
    widths=[1.6, 4.2],
    cap="Table C1. High-level trace from report claims to implementation. Exact line ranges move; the repository commit is the source of truth.",
)
heading(doc, "Appendix D  Disclosure checklist", 1)
for item in [
    "No autonomous booking, payment or external write; providers are read-only.",
    "Intercity transport is a visible gap; no live schedule or fare is claimed.",
    "No vector database/RAG; memory is a preference profile with similarity retrieval, not embeddings.",
    "No fine-tuning, RLHF or model-side web browsing; the LLM writes queries but code executes search.",
    "Judge model unused; the constraint verifier is a separate model audit, not an LLM judge for the report.",
    "Hybrid Jev/analyst agreement is an experimental shadow; it is not a shipped router.",
    "TravelPlanner is used as a small frozen subset, not the full 1,225-query benchmark; custom 70/30 split was not executed.",
    "The optional low-cost part-model profile (Flash-Lite/DeepSeek) is tested but not the shipped default.",
    "Component results are one run per case; the only repeated current suite is routing (two runs).",
    "Live search/provider results depend on keys and current data; stale or missing facts are surfaced as gaps.",
]:
    bullet(doc, item)
body(
    doc,
    "Ethics and data. The project stores account/guest identity, trip text and preference memory; personal "
    "data is masked before model calls, and a canary supports leakage checks. Provider terms and API keys are "
    "not redistributed in this report. The report's figures are design diagrams, not screenshots containing "
    "personal data.",
)

doc.save(OUT)
print(OUT)
