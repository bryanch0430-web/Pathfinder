import {
  ChatIcon,
  CloseIcon,
  CommunityIcon,
  ExploreIcon,
  HelpIcon,
  LogoMarkIcon,
  ResourcesIcon,
} from './HomeIcons';
import { useAuth } from '../../context/AuthContext';
import { User } from 'lucide-react';

const SIDEBAR_LINKS = [
  { label: 'Start Chatting', icon: ChatIcon, key: 'chat' },
  { label: 'Explore', icon: ExploreIcon, key: 'explore' },
  { label: 'Community', icon: CommunityIcon, key: 'community' },
  { label: 'Help', icon: HelpIcon, key: 'help' },
  { label: 'Resources', icon: ResourcesIcon, key: 'resources' },
] as const;

interface MobileSidebarProps {
  open: boolean;
  onClose: () => void;
  onGetStarted: () => void;
  onLogin: () => void;
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
  onNavigateHome?: () => void;
  onNavigateProfile?: () => void;
}

export function MobileSidebar({
  open,
  onClose,
  onGetStarted,
  onLogin,
  onStartPlanning,
  onNavigateExplore,
  onNavigateHome,
  onNavigateProfile,
}: MobileSidebarProps) {
  const { user } = useAuth();

  const handleItemClick = (key: string) => {
    onClose();
    if (key === 'explore' && onNavigateExplore) {
      onNavigateExplore();
    }
  };

  return (
    <>
      <div
        className={`home-sidebar__backdrop ${open ? 'home-sidebar__backdrop--visible' : ''}`}
        aria-hidden={!open}
        onClick={onClose}
      />
      <aside
        className={`home-sidebar home-ui ${open ? 'home-sidebar--open' : ''}`}
        aria-hidden={!open}
        aria-label="Navigation menu"
      >
        <div
          className="home-sidebar__header"
          style={{ cursor: onNavigateHome ? 'pointer' : 'default' }}
          onClick={() => {
            onClose();
            onNavigateHome?.();
          }}
        >
          <LogoMarkIcon className="home-sidebar__logo-mark" />
          <span className="home-sidebar__wordmark">PATHFINDER</span>
          <button
            type="button"
            className="home-sidebar__close"
            aria-label="Close menu"
            onClick={(e) => {
              e.stopPropagation();
              onClose();
            }}
          >
            <CloseIcon />
          </button>
        </div>

        <nav className="home-sidebar__nav">
          {SIDEBAR_LINKS.map(({ label, icon: Icon, key }) => (
            <button
              key={label}
              type="button"
              className="home-sidebar__item"
              onClick={() => handleItemClick(key)}
            >
              <Icon className="home-sidebar__item-icon" />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div className="home-sidebar__actions flex flex-col gap-3 pt-4 border-t border-[#D9D9D9]">
          {user ? (
            <>
              <button
                type="button"
                className="w-full py-3 px-4 bg-[#1F1E1E] text-white font-extrabold text-xs uppercase tracking-widest rounded-none flex items-center justify-center gap-2 hover:bg-black transition-colors"
                onClick={() => {
                  onClose();
                  onStartPlanning?.();
                }}
              >
                <span>Plan Expedition</span>
              </button>
              <button
                type="button"
                className="w-full py-3 px-4 bg-transparent text-[#1F1E1E] border-2 border-[#1F1E1E] font-extrabold text-xs uppercase tracking-widest rounded-none flex items-center justify-center gap-2 hover:bg-[#D9D9D9]/40 transition-colors"
                onClick={() => {
                  onClose();
                  onNavigateProfile?.();
                }}
              >
                <User className="w-4 h-4" />
                <span>My Profile</span>
              </button>
              <button
                type="button"
                className="w-full py-3 px-4 bg-transparent text-[#1F1E1E] border-2 border-[#1F1E1E] font-extrabold text-xs uppercase tracking-widest rounded-none flex items-center justify-center gap-2 hover:bg-[#D9D9D9]/40 transition-colors"
                onClick={() => {
                  onClose();
                  onLogin();
                }}
              >
                <span>Switch / Log In</span>
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="w-full py-3 px-4 bg-[#1F1E1E] text-white font-extrabold text-xs uppercase tracking-widest rounded-none hover:bg-black transition-colors shadow-none"
                onClick={() => {
                  onClose();
                  onGetStarted();
                }}
              >
                Get started
              </button>
              <button
                type="button"
                className="w-full py-3 px-4 bg-white text-[#1F1E1E] border-2 border-[#1F1E1E] font-extrabold text-xs uppercase tracking-widest rounded-none hover:bg-[#D9D9D9]/40 transition-colors shadow-none"
                onClick={() => {
                  onClose();
                  onLogin();
                }}
              >
                Log In
              </button>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
