"""Generate the v5-only modify-vs-replan figures (stdlib SVG, no plotting library).

Run from the repository root:
    python docs/report/modify-vs-replan/make_figures.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "figures"

PLAN = json.loads((DATA / "v5-runtime-applied-session-2026-11-25.json").read_text(encoding="utf-8"))
TRIALS = list(csv.DictReader((DATA / "v5_runtime_trials.csv").open(encoding="utf-8")))
EDITOR = list(csv.DictReader((DATA / "editor_direct_sonnet.csv").open(encoding="utf-8")))

INK = "#1f2933"
MUTED = "#5b6770"
RULE = "#c8d0d8"
PLAN_C = "#4C78A8"
EDIT_C = "#F58518"
GOOD = "#54A24B"

FONT = "Arial, Helvetica, sans-serif"


def svg(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">\n'
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>\n'
        f'<g font-family="{FONT}" fill="{INK}">\n{body}</g>\n</svg>\n'
    )


def text(x: int, y: int, value: str, size: int = 13, fill: str = INK, weight: str = "normal") -> str:
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}">{value}</text>'


def bar(x: int, y: int, w: int, h: int, fill: str) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" rx="2"/>'


def figure_cost() -> str:
    w, h = 900, 480
    plan_s = float(PLAN["plan_seconds"])
    edit_s = float(PLAN["edit_seconds"])
    plan_t = sum(g[2] + g[3] for g in PLAN["plan_generations"])
    edit_t = sum(g[2] + g[3] for g in PLAN["edit_generations"])
    max_s, max_t = 45.0, 22000.0
    y0 = 350
    scale_s, scale_t = 210 / max_s, 210 / max_t
    body = [
        text(40, 36, "v5.2.11: initial plan vs local edit — observed cost", 22, weight="bold"),
        text(40, 58, "One applied session, 25 Nov 2026. Descriptive runtime observation; the two requests differ.", 12, MUTED),
        text(60, 100, "A. Wall-clock time (s)", 14, weight="bold"),
        text(510, 100, "B. Total model tokens", 14, weight="bold"),
    ]
    # Panel A
    body.append(f'<line x1="60" y1="{y0}" x2="420" y2="{y0}" stroke="{RULE}"/>')
    for tick in (0, 15, 30, 45):
        y = y0 - int(tick * scale_s)
        body += [f'<line x1="60" y1="{y}" x2="420" y2="{y}" stroke="{RULE}" stroke-dasharray="2 4"/>', text(24, y + 4, str(tick), 11, MUTED)]
    for x, value, colour, label in ((140, plan_s, PLAN_C, "Plan"), (270, edit_s, EDIT_C, "Edit")):
        hh = int(value * scale_s)
        body += [bar(x, y0 - hh, 80, hh, colour), text(x + 40, y0 - hh - 10, f"{value:.2f} s", 13, INK, "bold"), text(x + 40, y0 + 20, label, 12, MUTED)]
    body.append(text(60, 392, f"{plan_s / edit_s:.1f}× faster; {(plan_s - edit_s) / plan_s * 100:.1f}% lower wall-clock time.", 12, MUTED))
    # Panel B
    body.append(f'<line x1="510" y1="{y0}" x2="870" y2="{y0}" stroke="{RULE}"/>')
    for tick in (0, 10000, 20000):
        y = y0 - int(tick * scale_t)
        label = "0" if tick == 0 else f"{tick // 1000}k"
        body += [f'<line x1="510" y1="{y}" x2="870" y2="{y}" stroke="{RULE}" stroke-dasharray="2 4"/>', text(464, y + 4, label, 11, MUTED)]
    for x, value, colour, label in ((590, plan_t, PLAN_C, "Plan"), (720, edit_t, EDIT_C, "Edit")):
        hh = int(value * scale_t)
        body += [bar(x, y0 - hh, 80, hh, colour), text(x + 40, y0 - hh - 10, f"{value:,}", 13, INK, "bold"), text(x + 40, y0 + 20, label, 12, MUTED)]
    body.append(text(510, 392, f"{(plan_t - edit_t) / plan_t * 100:.1f}% fewer total tokens; completion tokens {sum(g[3] for g in PLAN['plan_generations']):,} → {sum(g[3] for g in PLAN['edit_generations']):,}.", 12, MUTED))
    body += [
        text(40, 432, "Requests: new 2-day Taipei plan vs “Remove all attractions from Day 2.” Same session.", 11, MUTED),
        text(40, 450, "Planner: claude-sonnet-5.5. Editor: deepseek-flash. Both turns use 2 model calls; this is not a paired replan arm.", 11, MUTED),
        text(40, 468, "Source: data/v5-runtime-applied-session-2026-11-25.json (v5.2.11).", 11, MUTED),
    ]
    return svg(w, h, "\n".join(body))


def figure_quality() -> str:
    w, h = 900, 420
    rows = [
        ("Applied edit: requested targets removed", 3, 3, GOOD),
        ("Applied edit: untouched items kept", 3, 3, GOOD),
        ("Editor-direct: correct edits (Sonnet, n=8)", 8, 8, GOOD),
    ]
    body = [
        text(40, 36, "v5 edit-path quality evidence", 22, weight="bold"),
        text(40, 58, "v5.2.11 applied run plus the v5 model study; no paired replan quality arm exists.", 12, MUTED),
    ]
    y = 105
    for label, num, den, colour in rows:
        body += [text(40, y + 18, label, 13), f'<rect x="390" y="{y}" width="430" height="26" fill="#eef1f4" rx="3"/>', bar(390, y, int(430 * num / den), 26, colour), text(832, y + 18, f"{num}/{den}", 13, INK, "bold")]
        y += 62
    body += [
        text(40, y + 12, "Paired edit vs full-replan quality", 13),
        f'<rect x="390" y="{y - 8}" width="430" height="26" fill="#ffffff" stroke="{RULE}" stroke-dasharray="6 4" rx="3"/>',
        text(605, y + 10, "NOT MEASURED", 12, MUTED, "bold"),
        text(40, 366, "The v5 evaluation suite has an edit arm only; it cannot establish equal quality to a replan.", 11, MUTED),
        text(40, 384, "The older 5-case “preservation 100%” row is not used: its edits are proposals that are never applied.", 11, MUTED),
        text(40, 404, "Sources: data/v5_quality_evidence.csv, data/editor_direct_sonnet.csv, v5-model-study artifacts.", 11, MUTED),
    ]
    return svg(w, h, "\n".join(body))


def figure_editor_latency() -> str:
    values = sorted(float(row["latency_ms"]) / 1000 for row in EDITOR)
    w, h = 900, 390
    left, right, base = 90, 850, 300
    max_v = 26.0
    scale = (right - left) / max_v
    body = [
        text(40, 36, "v5 editor latency on 8 frozen edit cases", 22, weight="bold"),
        text(40, 58, "claude-sonnet-5.5, v5 model study (23 Nov 2026); isolated editor calls, not full turns.", 12, MUTED),
        f'<line x1="{left}" y1="{base}" x2="{right}" y2="{base}" stroke="{RULE}"/>',
    ]
    for tick in (0, 5, 10, 15, 20, 25):
        x = left + int(tick * scale)
        body += [f'<line x1="{x}" y1="{base}" x2="{x}" y2="{base + 5}" stroke="{RULE}"/>', text(x, base + 22, str(tick), 11, MUTED)]
    body.append(text((left + right) // 2, base + 42, "Latency (s)", 12, MUTED))
    median = sorted(values)[len(values) // 2 - 1 : len(values) // 2 + 1]
    median_v = sum(median) / 2
    mx = left + int(median_v * scale)
    body.append(f'<line x1="{mx}" y1="95" x2="{mx}" y2="{base}" stroke="{MUTED}" stroke-dasharray="5 4"/>')
    body.append(text(mx + 6, 88, f"median {median_v:.2f} s", 11, MUTED, "bold"))
    for index, value in enumerate(values):
        x = left + int(value * scale)
        y = 255 - (index % 4) * 28
        body += [f'<circle cx="{x}" cy="{y}" r="5" fill="{PLAN_C}"/>', text(x + 9, y + 4, f"{value:.2f}", 10, MUTED)]
    body.append(text(40, 360, "Range 3.99–24.79 s across 8 cases; one 24.8 s case is an outlier. Tokens: mean 11,550.", 11, MUTED))
    body.append(text(40, 378, "Source: data/editor_direct_sonnet.csv; v5 model study, Sonnet arm.", 11, MUTED))
    return svg(w, h, "\n".join(body))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fig-v5-modify-cost.svg").write_text(figure_cost(), encoding="utf-8")
    (OUT / "fig-v5-modify-quality.svg").write_text(figure_quality(), encoding="utf-8")
    (OUT / "fig-v5-editor-latency.svg").write_text(figure_editor_latency(), encoding="utf-8")
    print("wrote", *sorted(p.name for p in OUT.glob("*.svg")))


if __name__ == "__main__":
    main()
