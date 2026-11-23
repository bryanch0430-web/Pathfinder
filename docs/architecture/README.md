# Architecture maps

These are standalone, explorable HTML diagrams. They are written as stories for a
reader who knows the travel problem but not the codebase. Code names appear only where
they are needed to explain a guarantee.

Start with the journey, then follow the branch that answers your question:

1. **[How Pathfinder plans a trip](pathfinder-v5-architecture.html)** — the whole
   journey: you ask, evidence is gathered, a plan is built and checked, a version is
   saved, and a person can take over.
2. **[Under the hood: how a plan is checked](pathfinder-v5-architecture-detailed.html)**
   — sources, draft, deterministic checks, constraint audit, repair, visible gaps and
   versioning.
3. **[What data moves through Pathfinder](pathfinder-v5-dataflow.html)** — words,
   privacy masking, source records, candidate facts, plan versions, memory and replies.
4. **Turn stories**
   - [Planning a trip](pathfinder-v5-flow-plan.html)
   - [Changing a plan](pathfinder-v5-flow-modify.html)
   - [Asking about a trip](pathfinder-v5-flow-ask.html)
   - [When Pathfinder asks you](pathfinder-v5-flow-clarify.html)

## Conventions for these maps

- **Start with the traveller.** A map should answer “what happens to my request?” before
  it names a component.
- **Name the role, then the code.** “Request reader” is the primary label; the typed
  schema or module name belongs in a tag or a technical note, not the headline.
- **Use cards for the questions a diagram cannot draw.** Cards cover what you see,
  what can go wrong, what is guaranteed, and what deliberately does not happen.
- **Do not draw a capability that is not wired.** The Judge model is reserved and not
  called in v5. The hybrid router is an experimental shadow, not a shipped mode.
- **Keep facts traceable.** “Every stop points to a source” and “code computes dates and
  totals” are design rules; a source gap is shown as a gap, not smoothed away.
- **One story per diagram.** The overview explains the system; the safety map explains
  checking; the dataflow follows data; each turn diagram follows one branch. Links
  between them are more useful than one unreadable master diagram.

The editable candidate files are kept under `.archify/` while a diagram is being
authored. The HTML files in this directory are the delivered artifacts.
