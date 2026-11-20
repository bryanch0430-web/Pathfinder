/*
 * RouteCurtain — the transition shared by every route change.
 *
 * A bone panel carrying the Pathfinder mark rises over the current page, the
 * view is swapped while it is fully covered, and the panel continues upward
 * off the top. Pages mount under the curtain, so their own entrance
 * timelines begin as it lifts and the whole app moves as one piece.
 *
 * Reduced-motion users get an instant swap.
 */
import { forwardRef, useImperativeHandle, useRef } from 'react';
import gsap from 'gsap';

import { Logo } from './Logo';
import { companionGuide } from '../guide/guides';
import { prefersReducedMotion } from '../home/v2/motion';

export interface RouteCurtainHandle {
  /** Cover the screen, run `swap` while covered, then reveal. */
  play: (label: string, swap: () => void) => void;
}

export const RouteCurtain = forwardRef<RouteCurtainHandle>(function RouteCurtain(_, ref) {
  const panel = useRef<HTMLDivElement>(null);
  const mark = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const guideRef = useRef<HTMLImageElement>(null);
  const running = useRef<gsap.core.Timeline | null>(null);

  useImperativeHandle(ref, () => ({
    play(label, swap) {
      if (prefersReducedMotion() || !panel.current) {
        swap();
        return;
      }

      // A click mid-transition jumps the running one to its end first, so
      // two timelines never fight over the same panel.
      running.current?.progress(1);

      if (labelRef.current) labelRef.current.textContent = label;
      if (guideRef.current) guideRef.current.src = companionGuide().image;

      const tl = gsap.timeline({
        onComplete: () => {
          running.current = null;
        },
      });

      tl.set(panel.current, { display: 'flex', yPercent: 100 })
        .set(mark.current, { y: 30, opacity: 0 })
        .to(panel.current, { yPercent: 0, duration: 0.62, ease: 'expo.inOut' })
        .to(mark.current, { y: 0, opacity: 1, duration: 0.45, ease: 'expo.out' }, '-=0.25')
        .add(() => {
          swap();
          window.scrollTo(0, 0);
        })
        // Let the incoming page paint before lifting.
        .to({}, { duration: 0.12 })
        .to(mark.current, { y: -24, opacity: 0, duration: 0.35, ease: 'power2.in' })
        .to(panel.current, { yPercent: -100, duration: 0.7, ease: 'expo.inOut' }, '-=0.15')
        .set(panel.current, { display: 'none' });

      running.current = tl;
    },
  }));

  return (
    <div ref={panel} className="tv-curtain" aria-hidden="true">
      <div ref={mark} className="tv-curtain__mark">
        <img ref={guideRef} className="tv-curtain__guide" src={companionGuide().image} alt="" draggable={false} />
        <Logo width={46} height={41} />
        <span ref={labelRef} className="tv-curtain__label" />
      </div>
    </div>
  );
});
