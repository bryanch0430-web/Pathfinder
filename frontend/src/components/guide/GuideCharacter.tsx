/*
 * GuideCharacter: the traveler's guide as a living mark.
 *
 * One mark, five moods, made from transforms only (bob, tilt, squash), so any
 * guide art works without extra frames. Reduced motion keeps the mark still;
 * the mood badge still shows, so the state is never carried by motion alone.
 */
import React, { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';

import { prefersReducedMotion } from '../home/v2/motion';
import type { Guide } from './guides';
import '../../styles/guide-character.css';

export type GuideMood = 'idle' | 'thinking' | 'drawing' | 'celebrating' | 'confused';

const BADGE: Record<GuideMood, string | null> = { idle: null, thinking: '…', drawing: '✎', celebrating: '✦', confused: '?' };

interface Props {
  guide: Guide;
  size: number;
  mood?: GuideMood;
  /** No idle bob: for portraits repeated down a list, like chat messages. */
  still?: boolean;
  /** Accessible name; leave empty when a visible label already names the guide. */
  label?: string;
  className?: string;
  /** Leans toward the cursor, lifts on hover, and celebrates when poked. */
  interactive?: boolean;
  onPoke?: () => void;
}

function moodTween(body: HTMLElement, badge: HTMLElement | null, mood: GuideMood, still: boolean) {
  const loop = { repeat: -1, yoyo: true, ease: 'sine.inOut' };
  if (badge) gsap.from(badge, { scale: 0, rotation: -30, duration: 0.35, ease: 'back.out(3)' });
  switch (mood) {
    case 'idle':
      if (!still) gsap.to(body, { y: -2, duration: 1.8, ...loop });
      return;
    case 'thinking':
      gsap.fromTo(body, { rotation: -5 }, { rotation: 5, y: -1.5, duration: 0.8, ...loop });
      return;
    case 'drawing':
      gsap.fromTo(body, { rotation: -3 }, { rotation: 3, y: 1, duration: 0.22, ...loop });
      return;
    case 'celebrating':
      gsap.timeline({ repeat: 2 })
        .to(body, { y: -8, duration: 0.16, ease: 'power2.out' })
        .to(body, { y: 0, duration: 0.14, ease: 'power2.in' })
        .to(body, { scaleY: 0.9, scaleX: 1.08, duration: 0.07, transformOrigin: '50% 100%' })
        .to(body, { scaleY: 1, scaleX: 1, duration: 0.14, ease: 'back.out(3)' });
      if (badge) gsap.to(badge, { rotation: 360, duration: 1.2, ease: 'power1.inOut' });
      return;
    case 'confused':
      gsap.to(body, { keyframes: { rotation: [0, 11, -6, 9, 6] }, duration: 0.9, ease: 'power1.out' });
      if (badge) gsap.to(badge, { y: -2, duration: 0.5, ...loop, delay: 0.35 });
  }
}

export function GuideCharacter({ guide, size, mood = 'idle', still = false, label, className = '', interactive = false, onPoke }: Props) {
  const root = useRef<HTMLSpanElement>(null);
  const [poked, setPoked] = useState(false);
  const shownMood: GuideMood = poked ? 'celebrating' : mood;

  useGSAP(() => {
    if (prefersReducedMotion() || !root.current) return;
    const body = root.current.querySelector<HTMLElement>('.tv-guide__body')!;
    moodTween(body, root.current.querySelector<HTMLElement>('.tv-guide__badge'), shownMood, still);
  }, { scope: root, dependencies: [shownMood, still, guide.id], revertOnUpdate: true });

  // Lean toward the cursor from anywhere on the page. Written straight to the element: no re-render per move.
  useEffect(() => {
    if (!interactive || prefersReducedMotion() || !window.matchMedia('(pointer: fine)').matches) return;
    const lean = gsap.quickTo(root.current, 'x', { duration: 0.5, ease: 'power3.out' });
    const lift = gsap.quickTo(root.current, 'y', { duration: 0.5, ease: 'power3.out' });
    const onMove = (e: PointerEvent) => {
      const r = root.current?.getBoundingClientRect();
      if (!r) return;
      const dx = e.clientX - (r.left + r.width / 2);
      const dy = e.clientY - (r.top + r.height / 2);
      const d = Math.hypot(dx, dy) || 1;
      const k = Math.min(1, d / 320) * Math.max(2, size * 0.06);
      lean((dx / d) * k);
      lift((dy / d) * k);
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, [interactive, size]);

  useEffect(() => {
    if (!poked) return;
    const t = window.setTimeout(() => setPoked(false), 1300);
    return () => window.clearTimeout(t);
  }, [poked]);

  const poke = () => { setPoked(true); onPoke?.(); };
  const badge = BADGE[shownMood];
  return (
    <span ref={root} className={`tv-guide is-${shownMood} ${interactive ? 'is-interactive' : ''} ${className}`}
      style={{ '--guide-size': `${size}px` } as React.CSSProperties}
      {...(interactive
        ? { role: 'button', tabIndex: 0, 'aria-label': label ?? `${guide.name}, say hi`, onClick: poke,
            onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); poke(); } } }
        : { role: label ? 'img' : undefined, 'aria-label': label })}>
      <span className="tv-guide__body">
        <img className="tv-guide__img" src={guide.image} alt="" draggable={false} />
      </span>
      {badge && <span className="tv-guide__badge" aria-hidden="true">{badge}</span>}
    </span>
  );
}
