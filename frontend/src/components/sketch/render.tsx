/*
 * SketchPageSvg: draws one laid-out page. Rough.js turns every shape into wobbly
 * strokes (seeded by element id, so a page never jitters between renders),
 * perfect-freehand paints the highlighter, and all words stay real SVG text.
 */
import React, { useMemo } from 'react';
import rough from 'roughjs';
import getStroke from 'perfect-freehand';

import { DOODLES } from './doodles';
import { hash } from './layout';
import { type Point, type SketchElement, type SketchPage, type Tone } from './types';

const gen = rough.generator();
const INK = 'S', FILL = 'F'; // placeholders, swapped for theme tokens at render time

const TONE: Record<Tone, string> = {
  ink: 'var(--sk-ink)', muted: 'var(--sk-muted)', accent: 'var(--sk-accent)', red: 'var(--sk-red)', green: 'var(--sk-green)',
};
const FILL_TONE: Record<Tone, string> = {
  ink: 'var(--sk-ink-soft)', muted: 'var(--sk-muted-soft)', accent: 'var(--sk-accent-soft)', red: 'var(--sk-red-soft)', green: 'var(--sk-green-soft)',
};

const seedOf = (id: string) => (hash(id) % 2147483646) + 1;

/**
 * Rough.js drawable → SVG paths, with the stroke/fill placeholders mapped to a tone.
 * Outlines are tagged data-ink (the motion inks them stroke by stroke) and fills data-fill.
 * toPaths drops strokeLineDash, so dashes are applied here.
 */
function Rough({ drawable, tone, width = 2, dash }: { drawable: ReturnType<typeof gen.path>; tone: Tone; width?: number; dash?: string }) {
  return (
    <>
      {gen.toPaths(drawable).map((info, index) => {
        const hachure = info.stroke === FILL;
        const solid = info.fill === FILL;
        const outline = !hachure && !solid;
        return (
          <path key={index} d={info.d} fill={solid ? undefined : 'none'} strokeWidth={hachure ? 1.2 : width}
            strokeLinecap="round" strokeLinejoin="round" strokeDasharray={outline ? dash : undefined}
            data-ink={outline && !dash ? '' : undefined} data-fill={outline ? undefined : ''}
            style={{ stroke: solid ? 'none' : hachure ? FILL_TONE[tone] : TONE[tone], fill: solid ? FILL_TONE[tone] : undefined }} />
        );
      })}
    </>
  );
}

function arrowHead(points: Point[], id: string) {
  const [x2, y2] = points[points.length - 1];
  const [x1, y1] = points[points.length - 2];
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const wing = (turn: number): Point => [x2 - 12 * Math.cos(angle + turn), y2 - 12 * Math.sin(angle + turn)];
  return gen.linearPath([wing(0.45), [x2, y2], wing(-0.45)], { seed: seedOf(`${id}-head`), roughness: 0.8, stroke: INK });
}

function Text({ el }: { el: Extract<SketchElement, { kind: 'text' }> }) {
  return (
    <text x={el.x} y={el.y} fontSize={el.size} textAnchor={el.anchor ?? 'start'} fontWeight={el.bold ? 700 : 400} style={{ fill: TONE[el.tone] }}>
      {el.lines.map((line, index) => <tspan key={index} x={el.x} dy={index ? el.size * 1.08 : 0}>{line}</tspan>)}
    </text>
  );
}

function Highlight({ el }: { el: Extract<SketchElement, { kind: 'highlight' }> }) {
  const d = useMemo(() => {
    const wobble = (hash(el.id) % 5) - 2;
    const points = Array.from({ length: 12 }, (_, i) => [el.x + (el.w * i) / 11, el.y + el.h / 2 + Math.sin(i / 2) * 1.5 + wobble * (i / 11)]);
    const outline = getStroke(points, { size: el.h, thinning: 0.1, smoothing: 0.6, streamline: 0.4, simulatePressure: false });
    return outline.length ? `M${outline.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join('L')}Z` : '';
  }, [el]);
  return <path d={d} className="tv-sketch__hl" />;
}

function Element({ el }: { el: SketchElement }) {
  const seed = seedOf(el.id);
  switch (el.kind) {
    case 'text':
      return <Text el={el} />;
    case 'highlight':
      return <Highlight el={el} />;
    case 'box':
      return <Rough tone={el.tone} dash={el.dashed ? '9 8' : undefined} drawable={gen.rectangle(el.x, el.y, el.w, el.h, {
        seed, roughness: 1.1, bowing: 1.5, stroke: INK, disableMultiStroke: el.dashed,
      })} />;
    case 'ellipse':
      return <Rough tone={el.tone} width={el.fill ? 2 : 2.4} drawable={gen.ellipse(el.cx, el.cy, el.w, el.h, {
        seed, roughness: 1.4, stroke: INK, fill: el.fill ? FILL : undefined, fillStyle: 'hachure', hachureGap: 7, hachureAngle: -41,
      })} />;
    case 'path': {
      const options = { seed, roughness: 1, bowing: 2, stroke: INK, disableMultiStroke: el.dashed };
      return (
        <g>
          <Rough tone={el.tone} dash={el.dashed ? '10 9' : undefined} drawable={el.curve ? gen.curve(el.points, options) : gen.linearPath(el.points, options)} />
          {el.arrow && <Rough tone={el.tone} drawable={arrowHead(el.points, el.id)} />}
        </g>
      );
    }
    case 'doodle':
      return (
        <g transform={`translate(${el.x} ${el.y}) scale(${el.size / 24})`}>
          <Rough tone={el.tone} width={1.7} drawable={gen.path(DOODLES[el.name], { seed, roughness: 0.6, stroke: INK, disableMultiStroke: true })} />
        </g>
      );
    case 'note':
      return (
        <g transform={`rotate(${el.rotate} ${el.x + el.w / 2} ${el.y + el.h / 2})`} className="tv-sketch__note">
          <Rough tone="muted" width={1.4} drawable={gen.rectangle(el.x, el.y, el.w, el.h, {
            seed, roughness: 0.8, stroke: INK, fill: FILL, fillStyle: 'solid',
          })} />
          <text x={el.x + 12} y={el.y + 28} fontSize={18} fontWeight={700} style={{ fill: TONE.ink }}>{el.title}</text>
          <text x={el.x + 12} y={el.y + 54} fontSize={19} style={{ fill: TONE.ink }}>
            {el.lines.map((line, index) => <tspan key={index} x={el.x + 12} dy={index ? 20.5 : 0}>{line}</tspan>)}
          </text>
          {el.tag && <text x={el.x + el.w - 6} y={el.y + el.h + 20} fontSize={17} textAnchor="end" style={{ fill: TONE.accent }}>via {el.tag}</text>}
        </g>
      );
    case 'stamp':
      return (
        <g transform={`rotate(${el.rotate} ${el.x + el.w / 2} ${el.y + 20})`}>
          <Rough tone={el.tone} width={2.2} drawable={gen.rectangle(el.x, el.y, el.w, 40, { seed, roughness: 1.6, stroke: INK })} />
          <text x={el.x + el.w / 2} y={el.y + 28} fontSize={22} fontWeight={700} textAnchor="middle" style={{ fill: TONE[el.tone] }}>{el.text}</text>
        </g>
      );
  }
}

/** The guide's pen: tip at (0, 0), with the guide's portrait holding it. */
function Pen({ image }: { image: string }) {
  return (
    <g className="tv-sketch__pen" opacity={0} aria-hidden="true">
      <clipPath id="sk-pen-face"><circle cx={34} cy={-40} r={18} /></clipPath>
      <path d="M0 0l4-13 17-17 9 9-17 17z M4-13l9 9" className="tv-sketch__pen-body" />
      <circle cx={34} cy={-40} r={19} className="tv-sketch__pen-ring" />
      <image href={image} x={16} y={-58} width={36} height={36} clipPath="url(#sk-pen-face)" preserveAspectRatio="xMidYMid slice" />
    </g>
  );
}

export const SketchPageSvg = React.forwardRef<SVGSVGElement, { page: SketchPage; className?: string; penImage?: string }>(
  function SketchPageSvg({ page, className, penImage }, ref) {
    const titleId = `sk-${page.id}-title`, descId = `sk-${page.id}-desc`;
    return (
      <svg ref={ref} className={className} viewBox={`0 0 ${page.w} ${page.h}`} role="img" aria-labelledby={`${titleId} ${descId}`}
        preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
        <title id={titleId}>{page.title}</title>
        <desc id={descId}>{page.alt}</desc>
        <defs>
          <pattern id="sk-dots" width="24" height="24" patternUnits="userSpaceOnUse">
            <circle cx="12" cy="12" r="1.1" className="tv-sketch__dot" />
          </pattern>
          <filter id="sk-grain" x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise" />
            <feColorMatrix in="noise" type="saturate" values="0" />
            <feComponentTransfer><feFuncA type="linear" slope="0.07" /></feComponentTransfer>
          </filter>
        </defs>
        <rect width={page.w} height={page.h} className="tv-sketch__paper" />
        <rect width={page.w} height={page.h} fill="url(#sk-dots)" />
        <rect width={page.w} height={page.h} filter="url(#sk-grain)" className="tv-sketch__grain" />
        <g className="tv-sketch__ink">
          {page.elements.map((el) => <g key={el.id} data-el={el.id} data-kind={el.kind}><Element el={el} /></g>)}
        </g>
        {penImage && <Pen image={penImage} />}
      </svg>
    );
  },
);
