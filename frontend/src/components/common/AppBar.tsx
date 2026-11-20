/*
 * AppBar — the sticky header shared by the application surfaces
 * (Explore, Profile). The marketing page keeps its own `NavbarV2`; this is
 * the denser working-surface equivalent.
 */
import { useAuth } from '../../context/AuthContext';
import { LogoLockup } from './Logo';
import { ArrowUpRightIcon } from '../home/v2/IconsV2';

export interface AppBarProps {
  onNavigateHome?: () => void;
  onNavigateExplore?: () => void;
  onNavigateProfile?: () => void;
  onNavigateTrips?: () => void;
  onStartPlanning?: () => void;
  /** Marks the active destination so it can be de-emphasised in the bar. */
  current?: 'explore' | 'profile' | 'create' | 'trips' | 'guide';
}

/** Open a page from any surface; App's popstate listener runs the route transition. */
export function openPath(path: string) {
  if (window.location.pathname === path) return;
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export const openGuide = () => openPath('/guide');
export const openTrips = () => openPath('/trips');
export const openCredits = () => openPath('/credits');

export function AppBar({
  onNavigateHome,
  onNavigateExplore,
  onNavigateProfile,
  onNavigateTrips,
  onStartPlanning,
  current,
}: AppBarProps) {
  const { user } = useAuth();

  const links: Array<{ key: NonNullable<AppBarProps['current']>; label: string; go?: () => void }> = [
    { key: 'explore', label: 'Explore', go: onNavigateExplore },
    { key: 'trips', label: 'My trips', go: onNavigateTrips },
    { key: 'profile', label: 'Account', go: onNavigateProfile },
    { key: 'guide', label: 'Guide', go: openGuide },
  ];

  return (
    <header className="tv-app__bar">
      <button type="button" onClick={onNavigateHome} aria-label="Pathfinder home">
        <LogoLockup size={21} />
      </button>

      <nav className="tv-app__nav tv-hide-mobile" aria-label="Application">
        {links.map((l) =>
          l.go && l.key !== current ? (
            <button key={l.key} type="button" className="tv-app__navlink" onClick={l.go}>
              {l.label}
            </button>
          ) : null,
        )}
      </nav>

      <span className="tv-app__bar-spacer" />

      {current === 'trips' && onNavigateProfile ? (
        <button type="button" className="tv-app__mobile-link" onClick={onNavigateProfile}>Account</button>
      ) : onNavigateTrips ? (
        <button type="button" className="tv-app__mobile-link" onClick={onNavigateTrips}>My trips</button>
      ) : null}

      {user && (
        <span className="tv-meta tv-hide-mobile" title={user.email ?? undefined}>
          {user.email}
        </span>
      )}

      {onStartPlanning && current !== 'create' && (
        <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" onClick={onStartPlanning}>
          <span>Plan a trip</span>
          <ArrowUpRightIcon width={14} height={14} />
        </button>
      )}
    </header>
  );
}
