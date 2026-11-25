"""Move the six main design/evidence tables to Appendix E and renumber them."""

from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn
import os

DOC = os.environ.get("REPORT_DOC", "docs/report/Pathfinder_Final_Report_Updated.docx")
doc = Document(DOC)
body = doc.element.body
children = list(body.iterchildren())

# Find the first six main-body tables and their captions.
moved = []
for element in children:
    if element.tag != qn("w:tbl"):
        continue
    prev = element.getprevious()
    if prev is None or prev.tag != qn("w:p"):
        continue
    text = "".join(node.text or "" for node in prev.iter(qn("w:t")))
    if not text.startswith("Table ") or len(moved) >= 6:
        continue
    moved.append((prev, element))
    if len(moved) == 6:
        break

if len(moved) != 6:
    raise SystemExit(f"expected 6 tables, found {len(moved)}")

# Renumber in-text references before removing captions.
for n in range(1, 7):
    for p in doc.paragraphs:
        if p.text.startswith(f"Table {n}."):
            continue
        for run in p.runs:
            if f"Table {n}" in run.text:
                run.text = run.text.replace(f"Table {n}", f"Appendix Table E{n}")

# Remove captured paragraph/table pairs from the body.
for caption, table in moved:
    body.remove(caption)
    body.remove(table)

# Add the appendix heading and move the actual elements there.
doc.add_page_break()
p = doc.add_paragraph()
p.paragraph_format.space_before = doc.paragraphs[0].paragraph_format.space_before
r = p.add_run("Appendix E  Main design and evidence tables")
r.bold = True
r.font.name = "Times New Roman"
r.font.size = 12

intro = doc.add_paragraph()
intro.add_run(
    "These are the six tables referenced from the main text. They are kept in the appendix so the main report stays text-led."
).font.name = "Times New Roman"

for index, (caption, table) in enumerate(moved, start=1):
    # Rename the caption.
    old = "".join(node.text or "" for node in caption.iter(qn("w:t")))
    for node in caption.iter(qn("w:t")):
        if node.text:
            node.text = node.text.replace(f"Table {index}.", f"Table E{index}.")
    # Insert before the final section properties; appending after sectPr
    # corrupts the body and can drop earlier drawings on reload.
    sect_pr = body.sectPr
    sect_pr.addprevious(caption)
    sect_pr.addprevious(table)

doc.save(DOC)
print(f"moved {len(moved)} tables")
