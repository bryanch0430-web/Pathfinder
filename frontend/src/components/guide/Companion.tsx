/*
 * Companion: a guide that rides along on every page, bottom-left.
 *
 * Click for a tip about the page you're on; the swap chip changes who it is (kept in this browser).
 * The planner has its own guide in the chat, so the companion sits out there.
 */
import { useEffect, useRef, useState } from 'react';
import { GuideCharacter } from './GuideCharacter';
import { GUIDES, companionGuide, setCompanionGuide, type Guide } from './guides';
import '../../styles/companion.css';

const TIPS: Record<string, string[]> = {
  home: ['Describe a trip in one sentence and I’ll do the rest.', 'Try “Start planning”. I’ll sketch day one while you watch.'],
  explore: ['Every trip here can be opened and edited.', 'Found one you like? I can plan it for your dates.'],
  trips: ['Your trips live here. Open one to see it on the map.', 'I keep every sketch, so pick up where you left off.'],
  profile: ['Set a picture of your own, or keep me around.'],
  guide: ['Stuck? Ask me in the planner. That’s what I’m for.'],
  credits: ['Good work deserves credit. Nice that we list it.'],
  auth: ['Sign in and I’ll remember your trips.'],
};

const tipsFor = (view: string) => TIPS[view] ?? (['login', 'signup', 'reset-password'].includes(view) ? TIPS.auth : TIPS.home);

export function Companion({ view }: { view: string }) {
  const [guide, setGuide] = useState<Guide>(companionGuide);
  const [say, setSay] = useState<string | null>(null);
  const timer = useRef<number>();

  useEffect(() => {
    const sync = () => setGuide(companionGuide());
    window.addEventListener('tripverse-companion-guide', sync);
    return () => {
      window.removeEventListener('tripverse-companion-guide', sync);
      window.clearTimeout(timer.current);
    };
  }, []);

  const speak = (text: string) => {
    setSay(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSay(null), 4200);
  };

  const poke = () => {
    const tips = tipsFor(view);
    speak(tips[Math.floor(Math.random() * tips.length)]);
  };

  const swap = () => {
    const next = GUIDES[(GUIDES.findIndex((g) => g.id === guide.id) + 1) % GUIDES.length];
    setCompanionGuide(next.id);
    speak(next.line);
  };

  if (view === 'create') return null;
  return (
    <div className="tv-companion">
      {say && <p className="tv-say tv-companion__say" role="status">{say}</p>}
      <GuideCharacter guide={guide} size={56} interactive onPoke={poke} label={`${guide.name}, your guide. Press for a tip.`} />
      <button type="button" className="tv-companion__swap" onClick={swap} aria-label={`Swap guide, currently ${guide.name}`}>
        <span aria-hidden="true">↻</span> {guide.name}
      </button>
    </div>
  );
}
