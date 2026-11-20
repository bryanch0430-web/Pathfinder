import { LogoMarkIcon, MenuIcon } from './HomeIcons';
import { UserMenu } from '../auth/UserMenu';
import { useAuth } from '../../context/AuthContext';

const NAV_LINKS = [
  { label: 'How it works', href: '#how-it-works', key: 'how' },
  { label: 'Explore', href: '#explore', key: 'explore' },
  { label: 'Community', href: '#community', key: 'community' },
] as const;

interface NavbarProps {
  onMenuOpen: () => void;
  onGetStarted: () => void;
  scrolled: boolean;
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
  onNavigateHome?: () => void;
  onNavigateProfile?: () => void;
}

export function Navbar({
  onMenuOpen,
  onGetStarted,
  scrolled,
  onStartPlanning,
  onNavigateExplore,
  onNavigateHome,
  onNavigateProfile,
}: NavbarProps) {
  const { user } = useAuth();
  const handleLinkClick = (e: React.MouseEvent<HTMLAnchorElement>, key: string) => {
    if (key === 'explore' && onNavigateExplore) {
      e.preventDefault();
      onNavigateExplore();
    } else if (key === 'how' && onNavigateHome) {
      e.preventDefault();
      onNavigateHome();
      setTimeout(() => {
        const el = document.getElementById('how-it-works');
        el?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
    }
  };

  return (
    <header className={`home-navbar home-ui ${scrolled ? 'home-navbar--scrolled' : ''}`}>
      <div className="home-navbar__inner home-container">
        <div
          className="home-navbar__brand"
          style={{ cursor: onNavigateHome ? 'pointer' : 'default' }}
          onClick={onNavigateHome}
        >
          <button
            type="button"
            className="home-navbar__menu-btn"
            aria-label="Open navigation menu"
            onClick={(e) => {
              e.stopPropagation();
              onMenuOpen();
            }}
          >
            <MenuIcon className="home-navbar__menu-icon" />
          </button>
          <LogoMarkIcon className="home-navbar__logo-mark" />
          <span className="home-navbar__wordmark">PATHFINDER</span>
        </div>

        <nav className="home-navbar__links" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              className="home-navbar__link"
              onClick={(e) => handleLinkClick(e, link.key)}
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <UserMenu onNavigateProfile={onNavigateProfile} />
          <button
            type="button"
            className="home-navbar__cta"
            onClick={user ? (onStartPlanning || onGetStarted) : onGetStarted}
          >
            {user ? 'Plan Expedition' : 'Get started'}
          </button>
        </div>
      </div>
    </header>
  );
}

