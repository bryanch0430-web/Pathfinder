/*
 * NavbarV2 — tone-aware marketing header.
 *
 * `tone="over"` lets the bar sit transparently on top of a full-bleed
 * photographic hero and invert its ink; on scroll it resolves into the solid
 * bone bar used by the document-style heroes.
 */
import { UserAvatar } from '../../guide/UserAvatar';
import { useAuth } from '../../../context/AuthContext';
import { LogoMark, MenuIcon, ArrowUpRightIcon } from './IconsV2';

const NAV_LINKS = [
  { label: 'How it works', href: '#how-it-works', key: 'how' },
  { label: 'Destinations', href: '#destinations', key: 'destinations' },
  { label: 'Explore', href: '#explore', key: 'explore' },
  { label: 'Questions', href: '#questions', key: 'questions' },
] as const;

export interface NavbarV2Props {
  scrolled: boolean;
  /** `over` = transparent on a dark hero, `solid` = bone bar with a hairline. */
  tone?: 'solid' | 'over';
  onMenuOpen: () => void;
  onGetStarted: () => void;
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
  onNavigateHome?: () => void;
  onNavigateProfile?: () => void;
}

export function NavbarV2({
  scrolled,
  tone = 'solid',
  onMenuOpen,
  onGetStarted,
  onStartPlanning,
  onNavigateExplore,
  onNavigateHome,
  onNavigateProfile,
}: NavbarV2Props) {
  const { user } = useAuth();

  // Transparent only while the hero is still under the bar.
  const floating = tone === 'over' && !scrolled;

  const handleLink = (e: React.MouseEvent<HTMLAnchorElement>, key: string) => {
    if (key === 'explore' && onNavigateExplore) {
      e.preventDefault();
      onNavigateExplore();
      return;
    }
    const id = key === 'how' ? 'how-it-works' : key;
    const el = document.getElementById(id);
    if (el) {
      e.preventDefault();
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  return (
    <header
      className={floating ? 'tv-nav tv-nav--over tv-invert' : 'tv-nav tv-nav--solid'}
      data-scrolled={scrolled ? 'true' : 'false'}
    >
      <div className="tv-nav__inner tv-container tv-container--wide">
        <div className="tv-nav__left">
          <button
            type="button"
            className="tv-nav__burger"
            aria-label="Open navigation menu"
            onClick={onMenuOpen}
          >
            <MenuIcon width={20} height={20} />
          </button>

          <button type="button" className="tv-nav__brand" onClick={onNavigateHome}>
            <LogoMark className="tv-nav__logo" width={22} height={22} />
            <span className="tv-nav__wordmark">Pathfinder</span>
          </button>
        </div>

        <nav className="tv-nav__links tv-hide-mobile" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a
              key={link.key}
              href={link.href}
              className="tv-nav__link"
              onClick={(e) => handleLink(e, link.key)}
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="tv-nav__right">
          {user ? (
            <>
              {user.user_metadata?.is_admin && (
                <button type="button" className="tv-nav__link tv-hide-mobile" onClick={() => {
                  window.history.pushState({}, '', '/desk');
                  window.dispatchEvent(new PopStateEvent('popstate'));
                }}>Desk</button>
              )}
              <button
                type="button"
                className="tv-nav__account tv-hide-mobile"
                onClick={onNavigateProfile}
                title={user.email ?? 'Account'}
              >
              <span className="tv-nav__avatar" aria-hidden="true">
                <UserAvatar avatarUrl={user.user_metadata?.avatar_url} seed={user.id ?? user.email} size={26} />
              </span>
              <span className="tv-nav__account-label">Account</span>
            </button>
            </>
          ) : (
            <button type="button" className="tv-nav__signin tv-hide-mobile" onClick={onGetStarted}>
              Sign in
            </button>
          )}

          <button
            type="button"
            className="tv-btn tv-btn--primary tv-btn--sm"
            onClick={user ? onStartPlanning ?? onGetStarted : onGetStarted}
          >
            <span>{user ? 'Plan a trip' : 'Start planning'}</span>
            <ArrowUpRightIcon width={15} height={15} />
          </button>
        </div>
      </div>
    </header>
  );
}
