/*
 * CreateSidebar — the planner's left rail, on the v2 system.
 *
 * Brand, a new-trip action, the active trip, past sessions, and account
 * links. Resizable by drag on desktop; an overlay drawer below 1024px.
 */
import { UserAvatar } from '../guide/UserAvatar';
import React, { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { NewChatButton } from './NewChatButton';
import { CurrentTrip, type CurrentTripContext } from './CurrentTrip';
import { ChatHistory, type ChatSessionItem } from './ChatHistory';
import { LogoLockup } from '../common/Logo';
import { useAuth } from '../../context/AuthContext';
import { ArrowRightIcon, CloseIcon, CompassIcon } from '../home/v2/IconsV2';
import { EASE, prefersReducedMotion } from '../home/v2/motion';
import { openGuide } from '../common/AppBar';

interface CreateSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  onNewChat: () => void;
  sessions: ChatSessionItem[];
  activeSessionId?: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession?: (id: string, e: React.MouseEvent) => void;
  currentTrip?: CurrentTripContext | null;
  onNavigateHome?: () => void;
  onNavigateProfile?: () => void;
  onNavigateExplore?: () => void;
  onExploreSpatial?: () => void;
  width?: number;
  onWidthChange?: (newWidth: number) => void;
}

const isNarrow = () => window.innerWidth < 1024;

export const CreateSidebar: React.FC<CreateSidebarProps> = ({
  isOpen,
  onClose,
  onNewChat,
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  currentTrip,
  onNavigateHome,
  onNavigateProfile,
  onNavigateExplore,
  onExploreSpatial,
  width = 300,
  onWidthChange,
}) => {
  const { user } = useAuth();
  const root = useRef<HTMLElement>(null);
  const avatarUrl = user?.user_metadata?.avatar_url;

  // Stagger the rail's contents in whenever it opens.
  useGSAP(
    () => {
      if (!isOpen || prefersReducedMotion()) return;
      gsap.from('.tv-side__stagger', {
        x: -16,
        opacity: 0,
        duration: 0.7,
        ease: EASE,
        stagger: 0.05,
      });
    },
    { scope: root, dependencies: [isOpen] },
  );

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;

    const onMove = (ev: MouseEvent) => {
      onWidthChange?.(Math.min(Math.max(startWidth + ev.clientX - startX, 240), 520));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <>
      <div
        className={`tv-side__scrim ${isOpen ? 'is-open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        ref={root}
        style={isOpen ? { width } : undefined}
        className={`tv-side ${isOpen ? 'is-open' : ''}`}
        aria-label="Trip planning sidebar"
        aria-hidden={!isOpen}
        // A closed drawer stays in the DOM on small screens; inert keeps its buttons out of the tab order.
        {...(isOpen ? {} : { inert: '' })}
      >
        <div className="tv-side__head tv-side__stagger">
          <button type="button" onClick={onNavigateHome} aria-label="Pathfinder home">
            <LogoLockup size={20} />
          </button>
          <button type="button" className="tv-iconbtn tv-side__close" onClick={onClose} aria-label="Close sidebar">
            <CloseIcon width={17} height={17} />
          </button>
        </div>

        <div className="tv-side__new tv-side__stagger">
          <NewChatButton
            onClick={() => {
              onNewChat();
              if (isNarrow()) onClose();
            }}
          />
        </div>

        <div className="tv-side__body">
          <div className="tv-side__stagger">
            <CurrentTrip trip={currentTrip} onExploreSpatial={onExploreSpatial} />
          </div>

          <div className="tv-side__stagger">
            <ChatHistory
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelectSession={(id) => {
                onSelectSession(id);
                if (isNarrow()) onClose();
              }}
              onDeleteSession={onDeleteSession}
            />
          </div>
        </div>

        <div className="tv-side__foot tv-side__stagger">
          {onNavigateProfile && (
            <button type="button" className="tv-side__account" onClick={onNavigateProfile}>
              <span className="tv-msg2__you">
                <UserAvatar avatarUrl={avatarUrl} seed={user?.id ?? user?.email ?? 'guest'} size={34} />
              </span>
              <span className="tv-side__account-text">
                <span className="tv-side__account-name">{user ? 'Account' : 'Guest'}</span>
                <span className="tv-meta">{user?.email ?? 'Sign in to save trips'}</span>
              </span>
              <ArrowRightIcon width={14} height={14} />
            </button>
          )}

          {onNavigateExplore && (
            <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" style={{ width: '100%' }} onClick={onNavigateExplore}>
              <CompassIcon width={14} height={14} />
              <span>Explore destinations</span>
            </button>
          )}
          <button type="button" className="tv-btn tv-btn--ghost tv-btn--sm" style={{ width: '100%' }} onClick={openGuide}>
            <span>How to use Pathfinder</span>
          </button>
        </div>

        {isOpen && (
          <div
            className="tv-side__resize"
            onMouseDown={handleResizeStart}
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize"
          />
        )}
      </aside>
    </>
  );
};
