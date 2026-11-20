/*
 * SidebarV2 — navigation drawer.
 *
 * Rebuilt from the first pass, which left a large dead zone between the five
 * links and the footer buttons and repeated a chevron on every row. Now:
 *
 *   - the index lists pages only (My trips, Explore, the guide); the page's own
 *     sections live in the top bar, so repeating them here was noise
 *   - the index is numbered and set in the display serif, so the drawer reads
 *     as part of the editorial system rather than as a generic app menu
 *   - the dead space carries a featured trip with real imagery
 *   - the arrow only appears on the row you are pointing at
 *   - opening staggers the rows in rather than sliding one flat panel
 */
import { useEffect, useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { useAuth } from '../../../context/AuthContext';
import { openGuide, openTrips } from '../../common/AppBar';
import { img } from './content';
import { ArrowUpRightIcon, CloseIcon, LogoMark, ClockIcon } from './IconsV2';
import { EASE, prefersReducedMotion } from './motion';

// Pages only: the sections of this page are already in the top bar, and planning is the button below.
const LINKS = [
  { n: '01', label: 'My trips', hint: 'Every trip you started', key: 'trips' },
  { n: '02', label: 'Explore destinations', hint: 'Places to start', key: 'explore' },
  { n: '03', label: 'How to use Pathfinder', hint: 'The full guide', key: 'guide' },
] as const;

export interface SidebarV2Props {
  open: boolean;
  onClose: () => void;
  onGetStarted: () => void;
  onLogin: () => void;
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
  onNavigateHome?: () => void;
  onNavigateProfile?: () => void;
}

export function SidebarV2({
  open,
  onClose,
  onGetStarted,
  onLogin,
  onStartPlanning,
  onNavigateExplore,
  onNavigateHome,
  onNavigateProfile,
}: SidebarV2Props) {
  const { user } = useAuth();
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    // Closed, the drawer only slides off-screen; inert keeps Tab out of its buttons (React 18 has no inert prop).
    if (panelRef.current) panelRef.current.inert = !open;
    if (open) panelRef.current?.focus();
  }, [open]);

  // Stagger the contents in behind the panel slide, which CSS still owns.
  useGSAP(
    () => {
      if (!open || prefersReducedMotion()) return;
      gsap.fromTo(
        '.tv-drawer__item, .tv-drawer__feature, .tv-drawer__foot > *',
        { x: -22, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.75, ease: EASE, stagger: 0.045, delay: 0.16 },
      );
    },
    { scope: panelRef, dependencies: [open] },
  );

  const go = (key: (typeof LINKS)[number]['key']) => {
    onClose();
    if (key === 'trips') openTrips();
    else if (key === 'explore') onNavigateExplore?.();
    else openGuide();
  };

  return (
    <>
      <div
        className={`tv-drawer__scrim ${open ? 'is-open' : ''}`}
        aria-hidden="true"
        onClick={onClose}
      />

      <aside
        ref={panelRef}
        tabIndex={-1}
        className={`tv-drawer ${open ? 'is-open' : ''}`}
        aria-hidden={!open}
        aria-label="Navigation"
      >
        <div className="tv-drawer__head">
          <button
            type="button"
            className="tv-drawer__brand"
            onClick={() => {
              onClose();
              onNavigateHome?.();
            }}
          >
            <LogoMark width={22} height={22} />
            <span>Pathfinder</span>
          </button>

          <button type="button" className="tv-drawer__close" aria-label="Close menu" onClick={onClose}>
            <CloseIcon width={18} height={18} />
          </button>
        </div>

        <nav className="tv-drawer__nav">
          {LINKS.map(({ n, label, hint, key }) => (
            <button key={key} type="button" className="tv-drawer__item" onClick={() => go(key)}>
              <span className="tv-drawer__n tv-meta">{n}</span>
              <span className="tv-drawer__text">
                <span className="tv-display tv-drawer__label">{label}</span>
                <span className="tv-drawer__hint">{hint}</span>
              </span>
              <ArrowUpRightIcon className="tv-drawer__chev" width={16} height={16} />
            </button>
          ))}
        </nav>

        {/*
          Featured trip. It is `flex: 1` with a growing figure so it absorbs
          whatever vertical space is left between the index and the actions —
          otherwise the drawer opens with an obvious dead zone in the middle.
        */}
        <button type="button" className="tv-drawer__feature" onClick={() => go('explore')}>
          <figure className="tv-drawer__feature-fig">
            <img src={img.blossomFuji} alt="Mount Fuji framed by cherry blossom" className="tv-img" loading="lazy" />
            <span className="tv-drawer__feature-badge tv-label">Featured destination</span>
          </figure>
          <span className="tv-drawer__feature-body">
            <span className="tv-drawer__feature-title">Japan in blossom season</span>
            <span className="tv-drawer__feature-meta tv-meta">
              <ClockIcon width={12} height={12} />
              Late March to early April
            </span>
          </span>
        </button>

        <div className="tv-drawer__foot">
          {user ? (
            <>
              <button
                type="button"
                className="tv-btn tv-btn--primary tv-drawer__btn"
                onClick={() => {
                  onClose();
                  onStartPlanning?.();
                }}
              >
                Plan a trip
              </button>
              <button
                type="button"
                className="tv-btn tv-btn--ghost tv-drawer__btn"
                onClick={() => {
                  onClose();
                  onNavigateProfile?.();
                }}
              >
                Account
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="tv-btn tv-btn--primary tv-drawer__btn"
                onClick={() => {
                  onClose();
                  onGetStarted();
                }}
              >
                Start planning
              </button>
              <button
                type="button"
                className="tv-btn tv-btn--ghost tv-drawer__btn"
                onClick={() => {
                  onClose();
                  onLogin();
                }}
              >
                Sign in
              </button>
            </>
          )}

          <p className="tv-meta tv-drawer__note">Plan as a guest. An account only saves it.</p>
        </div>
      </aside>
    </>
  );
}
