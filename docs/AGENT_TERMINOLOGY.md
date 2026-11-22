# Pathfinder v5 in COMP7608B terminology

This note maps the course language from **COMP7608B Part II · Agentic AI**
(Lec 04 *AI Agent: Components and System*, Lec 05 *Context and Tools*, Lec 06
*Skill and Memory*, Lec 07 *Harness Engineering and Agent Patterns*, Lec 08
*Evaluation*) onto the v5 code and UI.

## 1. Classification

**Pathfinder v5 is an LLM-based agentic system — more precisely, a task-oriented,
tool-using planning agent running under a bounded runtime harness.**

It is not a single prompt, and it is not an autonomous multi-agent swarm. One turn
is a **bounded agent loop**:

1. **Context builder** assembles instructions, the traveller's request, trip state,
   the current plan, recent turns and the available Skill.
2. The **LLM router** proposes one route: `plan`, `modify`, `ask` or `clarify`.
   (Jev, a *System 1* decision model, can be selected as the router.)
3. The **runtime/harness** validates the route and dispatches only the allowed
   work: parallel tool workers, the planner, or a question.
4. **Tool workers** call the permitted tools (places, stays, weather, flights,
   videos) and return **observations** as evidence records.
5. The **planner** selects sourced references and composes the plan; the
   **evaluator/verifier** checks it and returns specific failures for a **bounded
   repair** (an evaluator–optimizer loop).
6. The harness records the trajectory, stops on budget/completion, and can hand
   off to a person (**human-in-the-loop**).

### Course workflow patterns it uses

| Course pattern | Where in v5 |
|---|---|
| **Routing** | The LLM router (or optional Jev) selects plan / modify / ask / clarify and constrains the path's tools. |
| **Prompt chaining** | Context → route → gather → planner → evaluate → repair → persist; each stage consumes a checked previous artifact. |
| **Parallelization** | Places, stays, weather, flights and videos run as independent tool workers with a join rule. |
| **Evaluator–optimizer** | The verifier/evaluator returns specific defects; the planner repairs at most a bounded number of times; the final plan is re-checked. |
| **Orchestrator–workers (fixed form)** | The turn harness assigns bounded work to specialized workers. It is not dynamic delegation: workers are tool-backed evidence services, not independent LLM agents with their own loops. |
| **Human-in-the-loop** | Clarify cards, edit proposals/ticks, the live-agent Desk (queue → accept), and handoff. |
| **Guardrails** | Route allowlists, prompt-injection screening, PII masking, evidence-only facts, budget/time limits, validated tool adapters. |

### What kind of agent is it?

- **Task-oriented / planning agent** (travel planning), not a general computer-use
  or coding agent.
- **Single primary agent with tool workers**: one controller owns the run; the
  workers do not plan or act autonomously.
- **Stateful and conversational**: working memory is the trip context + plan;
  durable memory is the traveller's preference profile.
- **Bounded, not open-ended autonomous**: the loop has route limits, per-turn
  deadlines, repair limits, tool allowlists and human approval paths.

This matches the proposal's title, **“Pathfinder: Agentic Trip Planner”**, and its
description as an **Agentic AI system** with routing, pre-planning agents, a
planner, tools, memory and evaluation.

## 2. Terminology map

| COMP7608B term | Course meaning (short) | v5 name / implementation | User-facing label |
|---|---|---|---|
| **Agent** | Perceives context, acts through tools, under a runtime loop | One Pathfinder turn | “Pathfinder” / “trip assistant” |
| **Agentic system** | Model + context + tools + runtime + memory/skills | The whole v5 backend + UI | “Agentic trip planner” |
| **Agent–environment loop** | Policy π over observations and working state | Bounded turn loop in `turn/orchestrator.py` | “bounded turn” |
| **Context builder** | Selects and fits instructions, evidence and tool schemas | Prompt assembly + candidate pools + Skill text | “context” |
| **Model / planner** | LLM decision or plan proposal | Analyst router + planner/composer | **Router**; **Planner** |
| **Runtime / harness** | Software that runs and constrains the loop | Turn orchestrator, seat lock, budgets, SSE, guards | **Runtime harness** |
| **Tool / tool adapter** | Validated call to an external service | `evidence/providers.py` adapters | **Tool workers** |
| **Observation** | Result returned to the next decision | `Evidence`, `DataGap`, tool status | **evidence / gap** |
| **Skill** | Reusable procedural instructions + resources | `backend/app/skills/*.md` loaded by `skill()` | **Skill** |
| **Memory (working)** | Current goal, state and selected evidence | `TripContext`, `TripPlan`, chat history | **trip state** |
| **Memory (semantic)** | Durable facts/preferences | Traveller profile preferences | **memory** |
| **Memory (procedural)** | How to act | Skills files | **Skill** |
| **Memory (episodic)** | Specific past experiences | Not implemented as a vector store; finalised trips are kept as history | **saved trips** (no RAG) |
| **RAG** | Retrieve, select, ground evidence | Live provider lookup + Wikipedia; **no vector DB/reranker** | “looked up”, not “RAG” |
| **Routing** | Choose a specialised path | Analyst/Jev route | **Turn router** |
| **Orchestration** | Workflow dependencies and joins | Service calls + parallel gather + planner/evaluator | **workflow** |
| **Human-in-the-loop (HITL)** | Human approval or takeover | Clarify, proposals, Desk queue/accept, handoff | **Desk / handoff** |
| **Guardrail** | Constrain permitted actions | Allowlists, injection screen, PII, budget | **guardrail** |
| **Evaluation** | Task success, trajectory/tool checks, judge/rubric | Deterministic checks + verifier audit + eval suite | **evaluation** |
| **Trajectory / trace** | The recorded steps and tool observations | `TurnTrace` generations/tools/spans | **Why this decision / trace** |
| **System 1** (course proposal) | Fast calibrated decision model | **Jev** (optional); the default router is the LLM analyst | **Jev — System 1 router** |

### Deliberate differences from the proposal

- The proposal proposed a **vector DB + RAG + reranker** for saved trips. v5 does
  not have that; it stores durable **semantic preferences** and live evidence. Do
  not call v5 “RAG” unless that retrieval layer is added.
- The proposal's “multi-agent pipeline” is implemented as **one primary agent +
  parallel tool workers**, not independent autonomous agents. In course terms this
  is **parallelization / fixed orchestrator–workers**, not a multi-agent society.
- The proposal's router was **Jev (System 1)** by default. v5 defaults to the
  **LLM router**; Jev is an optional router because it scored lower on the routing
  set (92.9 % vs 98.8 %).

## 3. UI terms to use

| Avoid (v5 internal / old) | Use (course-aligned) |
|---|---|
| Analyst | **LLM router** (or “router / analyst” in code docs) |
| Composer | **Planner** |
| Evidence agents | **Tool workers** |
| Verifier / validator | **Evaluator** (or “verifier” when naming the check) |
| Turn orchestrator (settings) | **Turn router** |
| Atlas | **Offline evidence corpus** (keep “atlas” only as the file name) |
| Travel designer queue | **Human-in-the-loop Desk queue** (UI may keep “Desk”) |
| System 1 (for the analyst) | **Jev / System 1 router** only |

Internal module names (`understand`, `agents`, `composer`, `validator`, `atlas`)
can stay; the public copy and docs should use the course terms above.
