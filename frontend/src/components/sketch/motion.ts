/*
 * Drawing a sketch page: one GSAP timeline that inks the page in reading order,
 * the way a person would draw it. Outlines are drawn stroke by stroke (DrawSVG) with the
 * guide's pen riding the first stroke (MotionPath), labels are written left to right
 * (a clip-path wipe, since SplitText can't split SVG text), doodles pop with a squash,
 * sticky notes slap on and stamps thud down.
 *
 * Transforms, opacity, clip-path and stroke dashes only. Every tween is a from(), so
 * reverting the owning gsap.context always leaves the finished page.
 */
import gsap from 'gsap';
import { DrawSVGPlugin } from 'gsap/DrawSVGPlugin';
import { MotionPathPlugin } from 'gsap/MotionPathPlugin';

gsap.registerPlugin(DrawSVGPlugin, MotionPathPlugin);

const STEP = 0.1; // each element starts this long after the previous one begins its ink

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

/** How long an element takes to draw, before the page-level speed-up. */
function inkTime(group: SVGGElement) {
  switch (group.dataset.kind) {
    case 'text': return clamp((group.textContent?.length ?? 0) * 0.022, 0.16, 0.6);
    case 'doodle': return 0.3;
    case 'highlight': return 0.35;
    case 'note': return 0.42;
    case 'stamp': return 0.3;
    case 'ellipse': return 0.45;
    default: return 0.36;
  }
}

function addElement(tl: gsap.core.Timeline, group: SVGGElement, at: number, pen: SVGGElement | null) {
  const kind = group.dataset.kind;
  const time = inkTime(group);
  const ink = [...group.querySelectorAll<SVGPathElement>('path[data-ink]')];
  const fills = group.querySelectorAll('path[data-fill]');

  if (pen) {
    const box = group.getBBox();
    if (kind === 'text') {
      tl.to(pen, { x: box.x, y: box.y + box.height, duration: 0.12, ease: 'power2.out' }, at - 0.12)
        .to(pen, { x: box.x + box.width, duration: time, ease: 'none' }, at);
    } else if (ink.length && kind !== 'doodle') {
      tl.to(pen, { motionPath: { path: ink[0], align: ink[0], alignOrigin: [0, 1] }, duration: time, ease: 'power1.inOut' }, at);
    } else {
      tl.to(pen, { x: box.x + box.width / 2, y: box.y + box.height / 2, duration: 0.14, ease: 'power2.inOut' }, at - 0.1);
    }
  }

  switch (kind) {
    case 'text':
      tl.fromTo(group, { clipPath: 'inset(-30% 100% -30% 0)' },
        { clipPath: 'inset(-30% 0% -30% 0)', duration: time, ease: 'none', clearProps: 'clipPath' }, at);
      return;
    case 'note':
      tl.from(group, { y: -46, rotation: 9, scale: 1.12, opacity: 0, transformOrigin: '50% 0%', duration: time, ease: 'back.out(2.2)' }, at);
      return;
    case 'stamp':
      tl.from(group, { scale: 1.7, opacity: 0, transformOrigin: '50% 50%', duration: time * 0.6, ease: 'power3.in' }, at)
        .to(group, { keyframes: { rotation: [0, -2, 1.5, 0] }, transformOrigin: '50% 50%', duration: 0.2 }, '>');
      return;
    case 'highlight':
      tl.from(group, { scaleX: 0, transformOrigin: '0% 50%', duration: time, ease: 'power2.out' }, at);
      return;
  }
  // Outlined shapes: ink each outline, then wash in any fill.
  if (ink.length) tl.from(ink, { drawSVG: 0, duration: time, stagger: time * 0.25, ease: 'power1.inOut' }, at);
  else tl.from(group, { opacity: 0, duration: time }, at); // dashed lines: DrawSVG would eat the dashes
  if (fills.length) tl.from(fills, { opacity: 0, duration: 0.25 }, at + time * 0.7);
  if (kind === 'doodle') tl.from(group, { scale: 0.55, transformOrigin: '50% 50%', duration: time, ease: 'back.out(3)' }, at);
}

/**
 * Build the timeline that draws `ids` (or the whole page) on an already-rendered page.
 * The pen shows the guide drawing; `maxSeconds` speeds long pages up to fit.
 * Call inside a gsap.context so an unmount or a new page can revert it to the finished state.
 */
export function drawPage(svg: SVGSVGElement, ids: Set<string> | null, maxSeconds: number) {
  const groups = [...svg.querySelectorAll<SVGGElement>('.tv-sketch__ink > [data-el]')]
    .filter((group) => !ids || ids.has(group.dataset.el!));
  const pen = svg.querySelector<SVGGElement>('.tv-sketch__pen');
  const tl = gsap.timeline();
  if (!groups.length) return tl;

  if (pen) {
    const first = groups[0].getBBox();
    tl.set(pen, { x: first.x, y: first.y + first.height }, 0).to(pen, { opacity: 1, duration: 0.15 }, 0);
  }
  groups.forEach((group, index) => addElement(tl, group, 0.15 + index * STEP, pen));
  if (pen) tl.to(pen, { opacity: 0, y: '-=24', duration: 0.3, ease: 'power2.in' }, '>');

  if (tl.duration() > maxSeconds) tl.timeScale(tl.duration() / maxSeconds);
  return tl;
}
