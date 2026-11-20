/*
 * motion.ts — the shared choreography vocabulary for the v2 home page.
 *
 * Every section composes these primitives so the whole page moves with one
 * hand. The house curve is a long, weighted `expo.out`; nothing bounces,
 * nothing springs back, and everything animates on `transform`/`opacity`
 * (plus `clip-path`, which is also compositor-friendly).
 */
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';

gsap.registerPlugin(ScrollTrigger, SplitText);

export const EASE = 'expo.out';
export const EASE_SOFT = 'power3.out';

export function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Split a headline into masked lines WITHOUT animating it.
 *
 * Use this when the caller drives the lines from its own timeline. Running
 * both this and `revealHeadline` on the same element would stack two `from`
 * tweens on the same targets — the second records the already-animated state
 * and the text never returns to visible.
 *
 * Always `revert()` the returned instance on cleanup; leaving split DOM in
 * place breaks text selection and re-wrapping on resize.
 */
export function splitLines(el: HTMLElement) {
  return new SplitText(el, {
    type: 'lines',
    linesClass: 'tv-line',
    // Each line gets its own overflow mask so the slide-up is clipped.
    mask: 'lines',
  });
}

/**
 * Line-by-line headline reveal — splits AND animates. For sections that just
 * want the house default. Do not also tween the returned lines yourself.
 */
export function revealHeadline(
  el: HTMLElement,
  opts: { trigger?: Element; start?: string; delay?: number; stagger?: number } = {},
) {
  const split = splitLines(el);

  gsap.from(split.lines, {
    yPercent: 115,
    duration: 1.15,
    ease: EASE,
    stagger: opts.stagger ?? 0.09,
    delay: opts.delay ?? 0,
    scrollTrigger: opts.trigger
      ? { trigger: opts.trigger, start: opts.start ?? 'top 78%' }
      : undefined,
  });

  return split;
}

/** Standard block entrance: a short rise with a soft fade. */
export function revealUp(
  targets: gsap.TweenTarget,
  opts: { trigger?: Element; start?: string; delay?: number; stagger?: number; y?: number } = {},
) {
  return gsap.from(targets, {
    y: opts.y ?? 26,
    opacity: 0,
    duration: 1,
    ease: EASE_SOFT,
    delay: opts.delay ?? 0,
    stagger: opts.stagger ?? 0.08,
    scrollTrigger: opts.trigger
      ? { trigger: opts.trigger, start: opts.start ?? 'top 82%' }
      : undefined,
  });
}

/**
 * Card/panel reveal — wipes open from the bottom edge while rising.
 * `clip-path` keeps the surface's own border and radius intact while it opens.
 */
export function revealPanel(
  targets: gsap.TweenTarget,
  opts: { trigger?: Element; start?: string; stagger?: number; delay?: number } = {},
) {
  return gsap.from(targets, {
    clipPath: 'inset(0% 0% 100% 0%)',
    y: 34,
    opacity: 0,
    duration: 1.25,
    ease: EASE,
    delay: opts.delay ?? 0,
    stagger: opts.stagger ?? 0.1,
    scrollTrigger: opts.trigger
      ? { trigger: opts.trigger, start: opts.start ?? 'top 80%' }
      : undefined,
  });
}

/**
 * Slow counter-drift inside a figure. The image is scaled up in CSS so it can
 * travel without exposing an edge.
 */
export function parallaxImage(
  img: HTMLElement,
  opts: { trigger?: Element; amount?: number } = {},
) {
  const amount = opts.amount ?? 12;
  return gsap.fromTo(
    img,
    { yPercent: -amount },
    {
      yPercent: amount,
      ease: 'none',
      scrollTrigger: {
        trigger: opts.trigger ?? img,
        start: 'top bottom',
        end: 'bottom top',
        scrub: true,
      },
    },
  );
}

/** Seamless marquee. The track must contain the content twice. */
export function marquee(track: HTMLElement, seconds = 42) {
  return gsap.to(track, {
    xPercent: -50,
    ease: 'none',
    duration: seconds,
    repeat: -1,
  });
}

/** Tabular number roll-up, triggered on entry. */
export function countUp(el: HTMLElement, to: number, trigger?: Element) {
  const obj = { v: 0 };
  return gsap.to(obj, {
    v: to,
    duration: 1.8,
    ease: EASE,
    snap: { v: 1 },
    onUpdate: () => {
      el.textContent = String(Math.round(obj.v));
    },
    scrollTrigger: { trigger: trigger ?? el, start: 'top 85%' },
  });
}
