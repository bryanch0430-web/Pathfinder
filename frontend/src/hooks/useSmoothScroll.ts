/*
 * useSmoothScroll — Lenis driven by GSAP's ticker, wired into ScrollTrigger.
 *
 * The older `useLenis` hook runs its own rAF loop and never tells ScrollTrigger
 * about it, so pinned sections and scrubbed timelines drift out of sync with
 * the smoothed scroll position. This hook instead:
 *
 *   1. forwards every Lenis scroll event to ScrollTrigger.update()
 *   2. drives lenis.raf() from gsap.ticker so both share one rAF loop
 *   3. disables lagSmoothing, which would otherwise make GSAP skip frames
 *      and visibly desync the pinned sections after a stall
 *
 * Returns the Lenis instance (via ref) for programmatic scrolling.
 */
import { useEffect, useRef } from 'react';
import Lenis from 'lenis';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { useReducedMotion } from './useReducedMotion';

gsap.registerPlugin(ScrollTrigger);

export function useSmoothScroll(enabled = true) {
  const lenisRef = useRef<Lenis | null>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    // Reduced-motion users get native scrolling and no smoothing.
    if (!enabled || reducedMotion) return;

    const lenis = new Lenis({
      duration: 1.1,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
      touchMultiplier: 1.2,
    });

    lenisRef.current = lenis;

    const onScroll = () => ScrollTrigger.update();
    lenis.on('scroll', onScroll);

    const tick = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);

    // Pins are measured from real layout; recalculate once Lenis is live.
    ScrollTrigger.refresh();

    return () => {
      lenis.off('scroll', onScroll);
      gsap.ticker.remove(tick);
      gsap.ticker.lagSmoothing(500, 33);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, [enabled, reducedMotion]);

  return lenisRef;
}
