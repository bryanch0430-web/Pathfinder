/*
 * The one-click trip PDF (A4 landscape):
 *   cover (the guide, destination, dates, travelers) → the sketch pages as vectors
 *   (overview, then a page per day) → the day-by-day plan with tips and sources,
 *   the budget, and credits.
 * Loaded on demand from the Export menu, so jsPDF, svg2pdf and the fonts never
 * touch the main bundle. Text stays real, selectable text on every page.
 */
import React from 'react';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { jsPDF } from 'jspdf';
import { svg2pdf } from 'svg2pdf.js';

import type { TripDocument } from '../../services/tripService';
import type { Guide } from '../../components/guide/guides';
import { layoutSketch } from '../../components/sketch/layout';
import { SketchPageSvg } from '../../components/sketch/render';
import type { SketchPage } from '../../components/sketch/types';
import '../../styles/sketch.css';
import { tripTitle } from './download';
import { embedFonts, fontFor, hasCjk } from './pdfFonts';
import { planRows, type Row } from './pdfPlan';

const W = 841.89, H = 595.28, M = 48; // A4 landscape in points
const INK: [number, number, number] = [34, 32, 28];
const MUTED: [number, number, number] = [111, 107, 99];
const ACCENT: [number, number, number] = [31, 108, 159];
const PAPER: [number, number, number] = [251, 248, 241];

// ---- sketch pages ----------------------------------------------------------

/** "rgba(1, 2, 3, 0.5)" → ["rgb(1, 2, 3)", "0.5"], since svg2pdf wants opacity separately. */
const splitAlpha = (color: string): [string, string | null] => {
  const match = color.match(/^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/);
  return match ? [`rgb(${match[1]}, ${match[2]}, ${match[3]})`, match[4]] : [color, null];
};

/**
 * Render a page with the app's own renderer, off screen and in the light theme, then
 * bake the theme tokens (CSS variables svg2pdf can't read) into plain attributes.
 */
function pageSvg(page: SketchPage, host: HTMLElement): SVGSVGElement {
  const root = createRoot(host);
  flushSync(() => root.render(React.createElement(SketchPageSvg, { page })));
  const live = host.querySelector('svg')!;
  live.querySelector('[filter]')?.remove(); // paper grain is a raster filter; plain paper is fine in print
  // Document order: a <text> gets its attributes before its <tspan>s read what they inherit.
  live.querySelectorAll<SVGElement>('path, rect, circle, ellipse, text, tspan').forEach((el) => {
    const style = getComputedStyle(el);
    const [fill, fillOpacity] = splitAlpha(style.fill);
    const [stroke, strokeOpacity] = splitAlpha(style.stroke);
    el.removeAttribute('style');
    el.removeAttribute('class');
    el.setAttribute('fill', fill.replace(/"/g, ''));
    el.setAttribute('stroke', stroke);
    if (fillOpacity) el.setAttribute('fill-opacity', fillOpacity);
    if (strokeOpacity) el.setAttribute('stroke-opacity', strokeOpacity);
    if (el.tagName === 'text') {
      el.setAttribute('font-family', hasCjk(el.textContent ?? '') ? 'Yomogi' : 'Caveat');
      el.setAttribute('font-weight', el.getAttribute('font-weight') === '700' ? 'bold' : 'normal');
    }
  });
  const svg = live.cloneNode(true) as SVGSVGElement;
  root.unmount();
  host.append(svg);
  return svg;
}

// ---- text pages ------------------------------------------------------------

const STYLE: Record<Row['kind'], { size: number; bold?: boolean; color: [number, number, number]; indent: number; before: number }> = {
  title: { size: 22, bold: true, color: INK, indent: 0, before: 6 },
  heading: { size: 14, bold: true, color: INK, indent: 0, before: 12 },
  label: { size: 9, bold: true, color: MUTED, indent: 0, before: 8 },
  item: { size: 11, bold: true, color: INK, indent: 10, before: 4 },
  meta: { size: 10, color: MUTED, indent: 0, before: 2 },
  tip: { size: 10, color: MUTED, indent: 22, before: 1 },
  text: { size: 10, color: INK, indent: 0, before: 4 },
  gap: { size: 10, color: INK, indent: 0, before: 6 },
};

function writeRows(pdf: jsPDF, rows: Row[]) {
  let y = H; // forces a fresh page for the first row
  const newPage = () => {
    pdf.addPage('a4', 'landscape');
    pdf.setFillColor(...PAPER).rect(0, 0, W, H, 'F');
    y = M;
  };
  for (const row of rows) {
    if (row.kind === 'gap') { y += STYLE.gap.before; continue; }
    const style = STYLE[row.kind];
    if (row.kind === 'title' && y > M) y = H; // each section starts on its own page
    const font = fontFor(row.text);
    // Caveat runs small, so it gets a size bump to sit level with Helvetica.
    const size = font === 'Caveat' ? style.size * 1.25 : style.size;
    pdf.setFont(font, style.bold && font !== 'Yomogi' ? 'bold' : 'normal').setFontSize(size).setTextColor(...(row.link ? ACCENT : style.color));
    const text = row.kind === 'item' ? `•  ${row.text}` : row.kind === 'label' ? row.text.toUpperCase() : row.text;
    const lines: string[] = pdf.splitTextToSize(text, W - 2 * M - style.indent);
    const height = lines.length * size * 1.25;
    if (y + style.before + height > H - M) newPage();
    y += style.before;
    lines.forEach((line, index) => {
      const baseline = y + size + index * size * 1.25;
      if (row.link) pdf.textWithLink(line, M + style.indent, baseline, { url: row.link });
      else pdf.text(line, M + style.indent, baseline);
    });
    y += height;
  }
}

// ---- cover -------------------------------------------------------------------

type CoverArt = { data: string; format: 'PNG' | 'JPEG' };

/** jsPDF only embeds raster art, and the guide marks ship as SVG, so rasterise those first. */
async function imageData(url: string): Promise<CoverArt> {
  const blob = await fetch(url).then((response) => (response.ok ? response.blob() : Promise.reject(new Error('image'))));
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  if (!blob.type.includes('svg')) return { data: dataUrl, format: blob.type.includes('jpeg') ? 'JPEG' : 'PNG' };

  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 512;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas');
  context.drawImage(image, 0, 0, 512, 512);
  return { data: canvas.toDataURL('image/png'), format: 'PNG' };
}

const dateLabel = (iso: string | null) => (iso
  ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
  : null);

async function cover(pdf: jsPDF, doc: TripDocument, guide: Guide) {
  pdf.setFillColor(...PAPER).rect(0, 0, W, H, 'F');
  const title = doc.destination || 'Your trip';
  pdf.setFont(fontFor(title) === 'Yomogi' ? 'Yomogi' : 'Caveat', 'bold').setFontSize(60).setTextColor(...INK);
  pdf.text(pdf.splitTextToSize(title, W - 2 * M - 220), M, 200);
  const people = doc.travelers.adults + doc.travelers.children;
  const facts = [
    doc.start_date ? `${dateLabel(doc.start_date)} – ${dateLabel(doc.end_date || doc.start_date)}` : null,
    doc.duration_days ? `${doc.duration_days} days` : null,
    `${people} traveler${people === 1 ? '' : 's'}`,
    doc.origin ? `from ${doc.origin}` : null,
  ].filter(Boolean).join(' · ');
  pdf.setFont(fontFor(facts), 'normal').setFontSize(fontFor(facts) === 'helvetica' ? 14 : 18).setTextColor(...MUTED);
  pdf.text(pdf.splitTextToSize(facts, W - 2 * M - 220), M, 250);

  try {
    const art = await imageData(guide.image);
    pdf.addImage(art.data, art.format, W - M - 170, 150, 170, 170);
    pdf.setDrawColor(...INK).setLineWidth(1.5).rect(W - M - 170, 150, 170, 170);
  } catch { /* the cover still works without the guide mark */ }
  pdf.setFont('Caveat', 'bold').setFontSize(22).setTextColor(...INK).text(`Sketched with ${guide.name}`, W - M - 170, 350);
  pdf.setFont('Caveat', 'normal').setFontSize(16).setTextColor(...MUTED).text(pdf.splitTextToSize(guide.line, 170), W - M - 170, 372);
  pdf.setFont('helvetica', 'normal').setFontSize(9).setTextColor(...MUTED)
    .text(`Made with Pathfinder on ${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}`, M, H - M);
}

// ---- the whole thing ---------------------------------------------------------------

export async function tripPdf(doc: TripDocument, guide: Guide): Promise<Blob> {
  const pages = layoutSketch(doc);
  const rows = planRows(doc);
  const pdf = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4', compress: true });
  pdf.setProperties({ title: tripTitle(doc), subject: 'Trip plan', creator: 'Pathfinder' });
  await embedFonts(pdf, hasCjk(JSON.stringify([doc.destination, doc.origin, doc.days, doc.enrichment?.holidays])));

  await cover(pdf, doc, guide);

  const host = document.createElement('div');
  host.className = 'tv2 tv-sketch';
  host.setAttribute('aria-hidden', 'true');
  Object.assign(host.style, { position: 'fixed', left: '-10000px', top: '0', width: '1123px', height: '794px' });
  document.body.append(host);
  try {
    for (const page of pages) {
      pdf.addPage('a4', 'landscape');
      const svg = pageSvg(page, host);
      await svg2pdf(svg, pdf, { x: 0, y: 0, width: W, height: H });
      svg.remove();
    }
  } finally {
    host.remove();
  }

  writeRows(pdf, rows);
  return pdf.output('blob'); // the Export menu saves it, like every other file
}
