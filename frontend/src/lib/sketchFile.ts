import { createElement, Fragment } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { SketchPageSvg } from "../design-system/sketch/render";
import { layoutSketch } from "../design-system/sketch/layout";
import { planToDocument } from "../design-system/sketch/fromPlan";
import type { TripPlan } from "../design-system/types";
import { planFileName } from "./export";

/** Standalone colours. A saved SVG cannot see the app's CSS variables. */
const INK: Record<string, string> = {
  "var(--sk-ink)": "#22201c",
  "var(--sk-ink-soft)": "rgba(34,32,28,.08)",
  "var(--sk-muted)": "#6f6b63",
  "var(--sk-muted-soft)": "#fbf0c4",
  "var(--sk-accent)": "#1f6c9f",
  "var(--sk-accent-soft)": "rgba(31,108,159,.28)",
  "var(--sk-red)": "#c0392b",
  "var(--sk-red-soft)": "rgba(192,57,43,.2)",
  "var(--sk-green)": "#346538",
  "var(--sk-green-soft)": "rgba(52,101,56,.2)",
  "var(--sk-yellow)": "#956400",
  "var(--sk-yellow-soft)": "#fbf3db",
};

const PAPER = `<style>
.tv-sketch__paper{fill:#fbf8f1}
.tv-sketch__dot{fill:rgba(60,50,30,.14)}
.tv-sketch__hl{fill:rgba(255,214,10,.5)}
text,tspan{font-family:Caveat,Yomogi,cursive}
</style>`;

let caveatFaces: Promise<string> | null = null;

function bytesToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

/** The same Caveat the page wraps with, inside the file, so a saved page does not fall back to a wider font. */
function loadCaveatFaces(): Promise<string> {
  if (!caveatFaces) {
    caveatFaces = (async () => {
      const css = await fetch("https://fonts.googleapis.com/css2?family=Caveat:wght@400;700&display=swap").then((response) => {
        if (!response.ok) throw new Error("font");
        return response.text();
      });
      const faces: string[] = [];
      for (const block of css.split("@font-face").slice(1)) {
        const url = block.match(/url\(([^)]+)\)/)?.[1]?.replace(/['"]/g, "");
        if (!url) continue;
        const weight = /font-weight:\s*(\d+)/.exec(block)?.[1] ?? "400";
        const data = await fetch(url).then((response) => {
          if (!response.ok) throw new Error("font");
          return response.arrayBuffer();
        });
        faces.push(
          `@font-face{font-family:'Caveat';font-style:normal;font-weight:${weight};src:url(data:font/woff2;base64,${bytesToBase64(data)}) format('woff2');}`,
        );
      }
      if (!faces.length) throw new Error("font");
      return faces.join("");
    })().catch((error) => {
      caveatFaces = null;
      throw error;
    });
  }
  return caveatFaces;
}

function download(name: string, contents: string) {
  const blob = new Blob([contents], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Every sketchbook page, stacked, as one SVG the traveller can open or print. */
export async function downloadPlanSketch(plan: TripPlan): Promise<void> {
  const faces = await loadCaveatFaces().catch(() => "");
  const pages = layoutSketch(planToDocument(plan), "landscape");
  if (!pages.length) throw new Error("sketch");
  const host = document.createElement("div");
  host.className = "tv-sketch";
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;left:-12000px;top:0;width:1200px;pointer-events:none;";
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    flushSync(() => {
      root.render(createElement(Fragment, null, pages.map((page) => createElement(SketchPageSvg, { key: page.id, page }))));
    });
    const gap = 48;
    let y = 0;
    let width = 0;
    const parts: string[] = [];
    const svgs = [...host.querySelectorAll("svg")];
    svgs.forEach((svg, index) => {
      const page = pages[index];
      if (!page) return;
      const clone = svg.cloneNode(true) as SVGSVGElement;
      const prefix = `p${index}-`;
      clone.querySelectorAll("[id]").forEach((node) => {
        const id = node.getAttribute("id");
        if (id) node.setAttribute("id", prefix + id);
      });
      let html = clone.innerHTML;
      html = html.replaceAll("url(#", `url(#${prefix}`).replaceAll('href="#', `href="#${prefix}`);
      for (const [from, to] of Object.entries(INK)) html = html.replaceAll(from, to);
      parts.push(`<g transform="translate(0 ${y})">${html}</g>`);
      y += page.h + gap;
      width = Math.max(width, page.w);
    });
    if (!parts.length) throw new Error("sketch");
    const height = y - gap;
    const doc = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
<style>${faces}</style>
${PAPER}
${parts.join("\n")}
</svg>`;
    download(planFileName(plan, "svg"), doc);
  } finally {
    root.unmount();
    host.remove();
  }
}
