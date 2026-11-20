/*
 * ChatHistory — past planning sessions as a hairline index.
 */
import React from 'react';
import { ClockIcon, CloseIcon } from '../home/v2/IconsV2';

export interface ChatSessionItem {
  id: string;
  title: string;
  timestamp: string;
  preview?: string;
  messageCount?: number;
  tripContext?: {
    destination?: string;
    days?: number;
    budget?: number;
  };
}

interface ChatHistoryProps {
  sessions: ChatSessionItem[];
  activeSessionId?: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession?: (id: string, e: React.MouseEvent) => void;
  className?: string;
}

export const ChatHistory: React.FC<ChatHistoryProps> = ({
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  className = '',
}) => {
  return (
    <section className={`tv-hist ${className}`}>
      <div className="tv-hist__head">
        <span className="tv-label">
          <ClockIcon width={12} height={12} /> Past trips
        </span>
        <span className="tv-meta">{sessions.length}</span>
      </div>

      {sessions.length === 0 ? (
        <p className="tv-meta tv-hist__empty">Trips you plan will be listed here.</p>
      ) : (
        <ul className="tv-hist__list">
          {sessions.map((s) => {
            const active = s.id === activeSessionId;
            return (
              <li key={s.id}>
                {/* The row is clickable for the mouse; keyboard and screen readers get the real button inside,
                    with Delete as its sibling rather than nested in it. Its click bubbles to the row once. */}
                <div
                  className={`tv-hist__item ${active ? 'is-active' : ''}`}
                  onClick={() => onSelectSession(s.id)}
                >
                  <button type="button" className="tv-hist__text" aria-current={active ? 'true' : undefined}>
                    <span className="tv-hist__title">{s.title}</span>
                    {s.preview && <span className="tv-hist__preview">{s.preview}</span>}
                    <span className="tv-meta tv-hist__meta">
                      {s.timestamp}
                      {s.messageCount !== undefined && ` · ${s.messageCount} messages`}
                    </span>
                  </button>

                  {onDeleteSession && (
                    <button
                      type="button"
                      className="tv-hist__delete"
                      onClick={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        onDeleteSession(s.id, e);
                      }}
                      aria-label={`Delete ${s.title}`}
                      title="Delete"
                    >
                      <CloseIcon width={13} height={13} />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
