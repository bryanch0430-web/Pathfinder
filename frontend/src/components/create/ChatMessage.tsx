/*
 * ChatMessage — transcript entry on the v2 system.
 *
 * Editorial transcript rather than chat bubbles: a mono header line
 * (who • when) above a hairline-bordered body. Metadata renders as real
 * tagged chips with icons — the previous emoji badges are out; the system
 * bans emoji.
 */
import { UserAvatar } from '../guide/UserAvatar';
import React, { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { AssistantAvatar } from './AssistantAvatar';
import { useGuide } from '../guide/GuideContext';
import { MarkdownMessage } from './MarkdownMessage';
import { useAuth } from '../../context/AuthContext';
import { CheckIcon, ClockIcon, LayersIcon, PinIcon, PlaneIcon, WalletIcon } from '../home/v2/IconsV2';
import { EASE, prefersReducedMotion } from '../home/v2/motion';
import type { ItineraryGraph } from './itineraryGraph';
import type { CopilotOp, CopilotState } from './CopilotPanel';
import type { RouteWarning } from '../../services/tripService';
import { maskForDisplay } from '../../lib/maskPii';

/** One line of a turn's receipt: what the planner actually changed (the server's record, not the reply's words). */
export interface PlanChange {
  status: 'done' | 'skipped';
  text: string;
  /** A held-back add the traveler can push through ("Add anyway"). */
  retry?: CopilotOp & { day?: number; name?: string };
}

export interface ChatMessageItem {
  id: string;
  sender: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  stage?: string;
  payload?: {
    action?: string; values?: Record<string, unknown>; kind?: string; graph?: ItineraryGraph;
    copilot?: CopilotState; changes?: PlanChange[]; offer_lock_budget?: boolean; offer_finalize_trip?: boolean;
    route_reality?: { warnings?: RouteWarning[] };
    route?: {
      intent?: string; confidence?: number; source?: string; reason?: string;
      affected_parts?: string[]; minimal?: boolean; need_clarification?: boolean;
      missing_slots?: string[];
    };
    previous?: { graph?: ItineraryGraph; day_plan?: unknown };
  } | null;
  metadata?: {
    destination?: string;
    duration?: string;
    origin?: string;
    budget?: string;
    interests?: string[];
  };
}

interface ChatMessageProps {
  message: ChatMessageItem;
  /** Shown on the receipt; left out inside the studio, where the change is already on screen. */
  onOpenStudio?: () => void;
  /** "Add anyway" on a held-back add; only the latest reply gets it. */
  onRetry?: (change: PlanChange) => void;
  onLockBudget?: () => void;
  onFinalizeTrip?: () => void;
  onClarify?: (text: string) => void;
  onUndo?: () => void;
}

function changeSummary(content: string) {
  const kept: string[] = [];
  for (const line of content.split(/\r?\n/)) {
    const stripped = line.trim();
    if (/^(#{1,3}\s*)?(\*\*)?Day \d+\b/i.test(stripped) || stripped.startsWith('**Route:')) break;
    kept.push(line);
  }
  return kept.join('\n').trim() || 'Updated the plan. The full days are in the studio.';
}

function PlanReceipt({ changes, onOpenStudio, onRetry }: { changes: PlanChange[] } & Omit<ChatMessageProps, 'message'>) {
  const changed = changes.some((change) => change.status === 'done');
  return (
    <section className={`tv-receipt ${changed ? '' : 'is-unchanged'}`} aria-label="What changed in your trip">
      <header>
        <span className="tv-label">{changed ? (onOpenStudio ? 'Saved to your studio' : 'Studio updated') : 'Nothing changed'}</span>
        {changed && onOpenStudio && (
          <button type="button" className="tv-receipt__open" onClick={onOpenStudio}>
            <LayersIcon width={13} height={13} />Open studio
          </button>
        )}
      </header>
      <ul>
        {changes.map((change, index) => {
          // The engine appends strain to the change itself: "Moved X to day 2 (heads-up: day 2 is now 9 h, …)".
          const [text, headsUp] = change.text.split(' (heads-up: ');
          return (
            <li key={index} className={`is-${change.status}`}>
              {change.status === 'done' ? <CheckIcon width={13} height={13} /> : <span className="tv-receipt__dash" aria-hidden="true">–</span>}
              <span>
                {change.status === 'skipped' && <span className="sr-only">Not changed: </span>}
                {text}
                {headsUp && <small>Heads-up: {headsUp.replace(/\)$/, '')}</small>}
              </span>
              {change.retry && onRetry && (
                <button type="button" onClick={() => onRetry(change)}>Add anyway</button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function routeLine(route: NonNullable<ChatMessageItem['payload']>['route']): string {
  if (!route) return '';
  const names: Record<string, string> = { plan: 'New plan', modify: 'Change', ask: 'Question', unclear: 'Clarifying' };
  const intent = route.reason === 'clear_plan' ? 'Plan deleted' : route.minimal ? 'Minimal change' : names[route.intent || ''] || 'Routed';
  const parts = (route.affected_parts || []).map((part) => part.replace(/^day_/, 'day ').replace(/_/g, ' ')).join(', ');
  const via = route.source === 'jev' ? 'Jev' : route.source === 'rules' ? 'rules' : route.source === 'writer' ? 'writer' : route.source || '';
  const score = typeof route.confidence === 'number' ? `${Math.round(route.confidence * 100)}%` : '';
  return [intent, parts, [via, score].filter(Boolean).join(' ')].filter(Boolean).join(' · ');
}

const SLOT_LABELS: Record<string, string> = {
  origin: 'Travelling from',
  destination: 'Travelling to',
  duration: 'How many days',
};

function ClarifyCard({ slots, onClarify }: { slots: string[]; onClarify: (text: string) => void }) {
  const [values, setValues] = React.useState<Record<string, string>>({});
  const submit = () => {
    const parts = slots.flatMap((slot) => {
      const value = (values[slot] || '').trim();
      if (!value) return [];
      if (slot === 'origin') return [`I'm travelling from ${value}`];
      if (slot === 'destination') return [`to ${value}`];
      if (slot === 'duration') return [`for ${value} days`];
      return [value];
    });
    if (parts.length) onClarify(parts.join(', '));
  };
  return (
    <form className="tv-receipt" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <header><span className="tv-label">A few details first</span></header>
      {slots.map((slot) => (
        <label key={slot} className="tv-label" style={{ display: 'block', marginTop: '0.6rem' }}>
          {SLOT_LABELS[slot] || slot}
          <input className="tv-input" required inputMode={slot === 'duration' ? 'numeric' : 'text'}
            value={values[slot] || ''} onChange={(event) => setValues((current) => ({ ...current, [slot]: event.target.value }))} />
        </label>
      ))}
      <button type="submit" className="tv-receipt__open" style={{ marginTop: '0.8rem' }}>Send these details</button>
    </form>
  );
}

export const ChatMessage: React.FC<ChatMessageProps> = ({ message, onOpenStudio, onRetry, onLockBudget, onFinalizeTrip, onClarify, onUndo }) => {
  const { user } = useAuth();
  const root = useRef<HTMLDivElement>(null);
  const guide = useGuide();

  const avatarUrl = user?.user_metadata?.avatar_url;
  const isUser = message.sender === 'user';
  const isSystem = message.sender === 'system';

  // Each entry rises in once as it mounts.
  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      gsap.from(root.current, { y: 16, opacity: 0, duration: 0.6, ease: EASE });
    },
    { scope: root },
  );

  if (isSystem) {
    return (
      <div className="tv-msg2 tv-msg2--system" ref={root}>
        <CheckIcon width={15} height={15} />
        <span className="tv-label">{message.stage || 'Checkpoint'}</span>
        <span className="tv-msg2__system-text">{message.content}</span>
        <span className="tv-meta">{message.timestamp}</span>
      </div>
    );
  }

  const meta = message.metadata;
  const chips = [
    meta?.destination && { Icon: PinIcon, value: meta.destination },
    meta?.duration && { Icon: ClockIcon, value: meta.duration },
    meta?.origin && { Icon: PlaneIcon, value: meta.origin },
    meta?.budget && { Icon: WalletIcon, value: meta.budget },
  ].filter(Boolean) as Array<{ Icon: typeof PinIcon; value: string }>;

  return (
    <article className={`tv-msg2 ${isUser ? 'tv-msg2--user' : 'tv-msg2--agent'}`} ref={root}>
      <div className="tv-msg2__avatar">
        {isUser ? (
          <span className="tv-msg2__you">
            <UserAvatar avatarUrl={avatarUrl} seed={user?.id ?? user?.email} size={34} />
          </span>
        ) : (
          <AssistantAvatar size={34} alt="" />
        )}
      </div>

      <div className="tv-msg2__col">
        <div className="tv-msg2__head">
          <span className="tv-label">{isUser ? 'You' : guide.name}</span>
          <span className="tv-meta">{message.timestamp}</span>
        </div>
        {!isUser && message.payload?.route && (
          <p className="tv-route" title={message.payload.route.reason || undefined}>{routeLine(message.payload.route)}</p>
        )}

        <div className="tv-msg2__body">
          {isUser ? (
            <p className="tv-msg2__text">{maskForDisplay(message.content || '')}</p>
          ) : message.content ? (
            <MarkdownMessage content={message.payload?.change_only ? changeSummary(message.content) : message.content} />
          ) : (
            <span className="tv-dots" aria-label="Writing">
              <i /> <i /> <i />
            </span>
          )}

          {chips.length > 0 && (
            <div className="tv-msg2__chips">
              {chips.map(({ Icon, value }) => (
                <span key={value} className="tv-tag">
                  <Icon width={12} height={12} />
                  {value}
                </span>
              ))}
            </div>
          )}
        </div>

        {!isUser && message.payload?.changes?.length ? (
          <PlanReceipt changes={message.payload.changes} onOpenStudio={onOpenStudio} onRetry={onRetry} />
        ) : null}
        {!isUser && Boolean(message.payload?.route_reality?.warnings?.length) && (
          <section className="tv-receipt" aria-label="Travel check">
            <header><span className="tv-label">Travel check · estimates</span></header>
            <ul>{message.payload!.route_reality!.warnings!.map((warning, index) => (
              <li key={`${warning.day}-${index}`} className={warning.severity === 'impossible' ? 'is-skipped' : ''}>
                <span className="tv-receipt__dash" aria-hidden="true">–</span>
                <span>Day {warning.day}: {warning.message}<small>
                  {warning.distance_km > 0 ? `${warning.distance_km} km straight line · ` : ''}
                  {warning.basis === 'road' ? 'road time' : 'distance estimate'}
                </small></span>
              </li>
            ))}</ul>
          </section>
        )}
        {!isUser && onClarify && (message.payload?.route?.missing_slots?.length || 0) > 0 && message.payload?.route?.need_clarification && (
          <ClarifyCard slots={message.payload.route.missing_slots || []} onClarify={onClarify} />
        )}
        {!isUser && onUndo && message.payload?.kind === 'ITINERARY_GRAPH' && message.payload.previous?.graph && (
          <div className="tv-receipt">
            <button type="button" className="tv-receipt__open" onClick={onUndo}>Restore previous plan</button>
          </div>
        )}
        {!isUser && message.payload?.offer_lock_budget && onLockBudget && <button type="button" className="tv-receipt__open" onClick={onLockBudget}>Lock this budget</button>}
        {!isUser && message.payload?.offer_finalize_trip && onFinalizeTrip && <button type="button" className="tv-receipt__open" onClick={onFinalizeTrip}>Review & finalize trip</button>}
      </div>
    </article>
  );
};
