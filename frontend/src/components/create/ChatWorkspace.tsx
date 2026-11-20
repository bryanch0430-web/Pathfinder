/*
 * ChatWorkspace — the centre column of the planner, on the v2 system.
 *
 * Header bar, scrolling transcript, and a sticky composer. Hairlines and
 * quiet surfaces replace the previous hard-shadow tactile chrome.
 *
 * The header has no entrance animation: it is on screen for every chat switch, and a staggered
 * tween over children that mount later left two of its buttons stranded 10 px high.
 */
import React, { useEffect, useRef, useState } from 'react';

import { ChatMessage, type ChatMessageItem, type PlanChange } from './ChatMessage';
import { AssistantAvatar } from './AssistantAvatar';
import { GuideContext } from '../guide/GuideContext';
import { guideById } from '../guide/guides';
import { ChatWelcome } from './ChatWelcome';
import { ChatComposer } from './ChatComposer';
import { TripOnboardingForm, type OnboardingValues } from './TripOnboardingForm';
import { TripPlanningChoice } from './TripPlanningChoice';
import { CopilotPanel, type CopilotOp, type CopilotState } from './CopilotPanel';
import type { TripBudget } from '../../services/tripService';
import { ThemeToggle } from '../common/ThemeToggle';
import { MenuIcon, LayersIcon, GraphIcon, WalletIcon } from '../home/v2/IconsV2';

interface ChatWorkspaceProps {
  messages: ChatMessageItem[];
  onSendMessage: (content: string, attachments?: File[]) => void;
  onOpenMobileSidebar: () => void;
  isSidebarOpen?: boolean;
  onToggleSidebar?: () => void;
  activeChatTitle?: string;
  /** Opens the Trip Studio (plan, map, 3D). Hidden until a plan exists. */
  onOpenStudio?: () => void;
  /** The trip card that closes the conversation and leads into the studio. */
  tripPreview?: React.ReactNode;
  /** 'drawer': the same chat inside the studio, without page-level controls. */
  variant?: 'page' | 'drawer';
  isBudgetOpen?: boolean;
  onToggleBudget?: () => void;
  onSelectPrompt: (promptText: string) => void;
  isLoading?: boolean;
  loadingStage?: string;
  onResetChat?: () => void;
  onboardingValues?: Partial<OnboardingValues> | null;
  onSubmitOnboarding?: (values: OnboardingValues) => void;
  showPlanningChoice?: boolean;
  planningBrief?: Partial<OnboardingValues> | null;
  onGenerateFull?: () => void;
  onStartBuild?: () => void;
  copilot?: CopilotState | null;
  budget?: TripBudget;
  isFinal?: boolean;
  canFinalize?: boolean;
  onRequestFinalize?: () => void;
  onCopilotOps?: (ops: CopilotOp[], label: string, day: number) => void;
  copilotDay?: number | null;
  onCopilotDay?: (day: number) => void;
  showComposer?: boolean;
  /** The trip's chosen guide (planning_preferences.guide); every assistant avatar becomes them. */
  guideId?: string | null;
}

function ResetIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
      <path d="M3.5 4.5V10H9" />
    </svg>
  );
}

export const ChatWorkspace: React.FC<ChatWorkspaceProps> = ({
  messages,
  onSendMessage,
  onOpenMobileSidebar,
  isSidebarOpen = true,
  onToggleSidebar,
  activeChatTitle,
  onOpenStudio,
  tripPreview,
  variant = 'page',
  isBudgetOpen = false,
  onToggleBudget,
  onSelectPrompt,
  isLoading = false,
  loadingStage = 'Putting your trip together',
  onResetChat,
  onboardingValues,
  onSubmitOnboarding,
  showPlanningChoice,
  planningBrief,
  onGenerateFull,
  onStartBuild,
  copilot,
  budget,
  isFinal = false,
  canFinalize = false,
  onRequestFinalize,
  onCopilotOps,
  copilotDay,
  onCopilotDay,
  showComposer = true,
  guideId,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [editingBrief, setEditingBrief] = useState(false);
  // A place picked on Explore arrives typed into the box, ready to send (read once).
  const [draft] = useState(() => {
    try {
      return sessionStorage.getItem('tripverse-draft') || '';
    } catch {
      return '';
    }
  });
  // Cleared after mount, not in the initializer: StrictMode runs initializers twice.
  useEffect(() => {
    try {
      sessionStorage.removeItem('tripverse-draft');
    } catch {
      // nothing to clear
    }
  }, []);
  const hasMessages = messages.length > 0;
  // Until the traveler says something, the opening screen stands in for the lone server greeting.
  const fresh = !isLoading && messages.length <= 1 && !messages.some((msg) => msg.sender !== 'assistant');
  const latestReplyId = [...messages].reverse().find((msg) => msg.sender === 'assistant')?.id;
  // The trip card belongs where the plan was made, not under every later reply. Until that message
  // is saved (the first draft is still streaming) it closes the thread.
  const planIndex = messages.findIndex((msg) => msg.payload?.kind === 'ITINERARY_GRAPH');
  const planned = Boolean(tripPreview) || variant === 'drawer'; // the studio only opens on a planned trip
  // "Add anyway" on a held-back add: the same add, forced, on the day it was meant for.
  const retry = (change: PlanChange) => {
    const op = change.retry;
    if (op && onCopilotOps) onCopilotOps([op], `Add ${op.name} to day ${op.day} anyway`, op.day ?? copilot?.current_day ?? 1);
  };

  // Follow the transcript down as it grows, but only while the reader is already at
  // the bottom — a streamed itinerary must not yank someone reading further up.
  const pinned = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (fresh) { el.scrollTop = 0; pinned.current = true; return; }
    if (pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages, isLoading, fresh]);

  return (
    <GuideContext.Provider value={guideById(guideId)}>
    {/* The chat page's main landmark; in the studio drawer it isn't one, since the studio has its own <main>. */}
    <div className={`tv-chat ${variant === 'drawer' ? 'tv-chat--drawer' : ''}`} role={variant === 'page' ? 'main' : undefined}>
      <header className="tv-chat__bar">
        {variant === 'page' && <button
          type="button"
          className="tv-iconbtn"
          onClick={onToggleSidebar || onOpenMobileSidebar}
          aria-label={isSidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
          title={isSidebarOpen ? 'Collapse sidebar' : 'Open sidebar'}
        >
          <MenuIcon width={19} height={19} />
        </button>}

        <div className="tv-chat__title">
          <GraphIcon width={15} height={15} />
          <h1>{activeChatTitle || 'New trip'}</h1>
          {variant === 'page' && planned && <span className="tv-meta tv-chat__saved">{isFinal ? 'Final trip · read-only' : 'Changes save to your studio'}</span>}
        </div>

        <span className="tv-app__bar-spacer" />

        {variant === 'page' && !fresh && hasMessages && onResetChat && (
          <button type="button" className="tv-iconbtn" onClick={onResetChat} title="Start over" aria-label="Start over">
            <ResetIcon width={17} height={17} />
          </button>
        )}

        {variant === 'page' && <ThemeToggle />}

        {variant === 'page' && onToggleBudget && <button type="button"
          className={`tv-btn tv-btn--sm ${isBudgetOpen ? 'tv-btn--primary' : 'tv-btn--ghost'}`}
          onClick={onToggleBudget} aria-pressed={isBudgetOpen} aria-label="Trip budget">
          <WalletIcon width={14} height={14} /><span className="tv-hide-mobile">Budget</span>
        </button>}

        {variant === 'page' && canFinalize && onRequestFinalize && <button type="button" className="tv-btn tv-btn--sm tv-btn--ghost" onClick={onRequestFinalize}>Finalize trip</button>}

        {variant === 'page' && onOpenStudio && (
          <button type="button" className="tv-btn tv-btn--sm tv-btn--primary" onClick={onOpenStudio} aria-label="Open studio">
            <LayersIcon width={14} height={14} />
            <span className="tv-hide-mobile">Open studio</span>
          </button>
        )}
      </header>

      <div className="tv-chat__scroll" ref={scrollRef} onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}>
        {fresh ? (
          <div className="tv-chat__welcome-wrap">
            <ChatWelcome onSelectPrompt={onSelectPrompt} />
          </div>
        ) : (
          <div className="tv-chat__thread">
            {messages.map((msg, index) => (
              <React.Fragment key={msg.id}>
                <ChatMessage message={msg} onOpenStudio={variant === 'page' ? onOpenStudio : undefined}
                  onRetry={msg.id === latestReplyId && !isLoading && copilot && onCopilotOps ? retry : undefined}
                  onLockBudget={msg.id === latestReplyId && !isLoading && !isFinal ? () => onSendMessage('Lock this budget') : undefined}
                  onFinalizeTrip={msg.id === latestReplyId && !isLoading && canFinalize ? onRequestFinalize : undefined} />
                {index === planIndex && tripPreview}
              </React.Fragment>
            ))}

            {/* Forms belong to the turn before a stream; the trip status only updates when
                the stream finishes, so hide them while a response is being written. */}
            {!isLoading && onboardingValues && onSubmitOnboarding && (
              <TripOnboardingForm initial={onboardingValues} onSubmit={onSubmitOnboarding} disabled={isLoading} />
            )}

            {!isLoading && showPlanningChoice && onGenerateFull && (
              editingBrief && planningBrief && onSubmitOnboarding
                ? <TripOnboardingForm allAtOnce initial={planningBrief} disabled={isLoading} onSubmit={(values) => { setEditingBrief(false); onSubmitOnboarding(values); }} />
                : <TripPlanningChoice disabled={isLoading} onGenerateFull={onGenerateFull} onStartBuild={onStartBuild} brief={planningBrief || undefined} onEditDetails={() => setEditingBrief(true)} />
            )}

            {planIndex < 0 && tripPreview}

            {isLoading && (
              <div className="tv-chat__thinking">
                <AssistantAvatar size={34} isThinking />
                <div className="tv-chat__thinking-body">
                  <span className="tv-label">{loadingStage}</span>
                  <span className="tv-dots" aria-hidden="true">
                    <i /> <i /> <i />
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {copilot && onCopilotOps && onCopilotDay && <CopilotPanel copilot={copilot} budget={budget} disabled={isLoading || budget?.is_locked || isFinal}
        viewDay={copilotDay || copilot.current_day} onViewDay={onCopilotDay} onOps={onCopilotOps} />}

      {showComposer && !isFinal && <div className="tv-chat__composer">
        <ChatComposer
          onSendMessage={onSendMessage}
          initialValue={draft}
          isLoading={isLoading}
          placeholder={
            planned ? 'Change the plan, or ask a question…'
              : hasMessages ? 'Tell me where you want to go, or ask a travel question…'
                : 'Where are you thinking of going?'
          }
          sendLabel={planned ? 'Send' : 'Plan'}
          hint={planned ? (copilot
            ? 'Tap a suggestion, or say “move it to day 2” or “finish”'
            : 'Try “move a place to day 1” or “my budget is 60,000”') : undefined}
        />
      </div>}
    </div>
    </GuideContext.Provider>
  );
};
