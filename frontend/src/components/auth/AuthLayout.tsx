/*
 * AuthLayout — split auth shell on the v2 design system.
 *
 * Form on the left over bone canvas, rotating photography on the right.
 * Collapses to a single column below 900px, where the photographic pane is
 * dropped rather than stacked (it would push the form below the fold).
 */
import React, { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { LogoLockup } from '../common/Logo';
import { AuthVisual } from './AuthVisual';
import { ArrowRightIcon } from '../home/v2/IconsV2';
import { EASE, prefersReducedMotion, splitLines } from '../home/v2/motion';
import type { AuthVisualItem } from '../../constants/authVisuals';

interface AuthLayoutProps {
  title: string;
  /** Optional italic second clause, set in muted ink. */
  titleAccent?: string;
  eyebrow?: string;
  subtitle?: string;
  children: React.ReactNode;
  onNavigateHome?: () => void;
  visual?: AuthVisualItem;
  visualImage?: string;
  destinationName?: string;
  locationName?: string;
  experienceName?: string;
  objectPosition?: string;
}

export const AuthLayout: React.FC<AuthLayoutProps> = ({
  title,
  titleAccent,
  eyebrow = 'Pathfinder account',
  subtitle,
  children,
  onNavigateHome,
  visual,
  visualImage,
  destinationName,
  locationName,
  experienceName,
  objectPosition,
}) => {
  const root = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;

      const split = titleRef.current ? splitLines(titleRef.current) : null;
      const tl = gsap.timeline({ defaults: { ease: EASE } });

      tl.from('.tv-auth__top', { y: -14, opacity: 0, duration: 0.7 })
        .from('.tv-auth__eyebrow', { y: 14, opacity: 0, duration: 0.7 }, '-=0.45');

      if (split) tl.from(split.lines, { yPercent: 115, duration: 1.05, stagger: 0.08 }, '-=0.5');

      tl.from('.tv-auth__sub', { y: 16, opacity: 0, duration: 0.8 }, '-=0.6')
        .from('.tv-auth__body form > *, .tv-auth__body .tv-divider, .tv-auth__body .tv-social > *', {
          y: 18,
          opacity: 0,
          duration: 0.7,
          stagger: 0.06,
        }, '-=0.55')
        .from('.tv-auth__alt, .tv-auth__foot', { y: 12, opacity: 0, duration: 0.6, stagger: 0.06 }, '-=0.4');

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <div className="tv2 tv2-app tv-auth" ref={root}>
      <div className="tv-auth__pane">
        <div className="tv-auth__top">
          <button type="button" onClick={onNavigateHome} aria-label="Pathfinder home">
            <LogoLockup size={22} />
          </button>

          {onNavigateHome && (
            <button type="button" className="tv-auth__back" onClick={onNavigateHome}>
              <ArrowRightIcon width={14} height={14} style={{ transform: 'rotate(180deg)' }} />
              <span>Back to home</span>
            </button>
          )}
        </div>

        <div className="tv-auth__body">
          <span className="tv-eyebrow tv-auth__eyebrow">{eyebrow}</span>

          <h1 className="tv-display tv-auth__title" ref={titleRef}>
            {title}
            {titleAccent && (
              <>
                {' '}
                <em>{titleAccent}</em>
              </>
            )}
          </h1>

          {subtitle && <p className="tv-lead tv-auth__sub">{subtitle}</p>}

          {children}
        </div>

        <div className="tv-auth__foot">
          <span className="tv-meta">© {new Date().getFullYear()} Pathfinder</span>
          <div className="tv-auth__legal">
            <a href="#privacy" className="tv-meta">Privacy</a>
            <a href="#terms" className="tv-meta">Terms</a>
          </div>
        </div>
      </div>

      <AuthVisual
        visual={visual}
        imageSrc={visualImage}
        destination={destinationName}
        location={locationName}
        experience={experienceName}
        objectPosition={objectPosition}
      />
    </div>
  );
};
