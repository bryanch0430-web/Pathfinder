/*
 * ChatWelcome — the planner's opening, shown until the traveler says something.
 *
 * A greeting by name and time of day (the traveler's own clock, which only the browser knows),
 * what happens next in one honest paragraph, and four starter trips that send themselves.
 */
import React, { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { AssistantAvatar } from './AssistantAvatar';
import { ArrowUpRightIcon } from '../home/v2/IconsV2';
import { useAuth } from '../../context/AuthContext';
import { EASE, prefersReducedMotion, splitLines } from '../home/v2/motion';

interface ChatWelcomeProps {
  onSelectPrompt: (promptText: string) => void;
}

const STARTER_PROMPTS = [
  {
    title: 'Cultural odyssey in Japan',
    meta: '10 days · Tokyo, Kyoto, Nara, Osaka',
    prompt:
      'Plan a 10-day cultural journey to Japan focusing on traditional temples, tea ceremonies, vibrant street food in Osaka, and modern art in Tokyo with a budget of $3,500.',
  },
  {
    title: 'Alpine scenic route',
    meta: '7 days · Zurich, Interlaken, Zermatt',
    prompt:
      'Create a 7-day scenic road and rail trip across Switzerland covering Zurich, Interlaken, and Zermatt with scenic hiking and alpine vistas.',
  },
  {
    title: 'Italian coast and kitchen',
    meta: '8 days · Rome, Florence, Amalfi',
    prompt:
      'Design an 8-day culinary and architectural escape to Italy visiting Rome, Florence, and the Amalfi Coast for 2 travelers.',
  },
  {
    title: 'Nordic aurora and fjords',
    meta: '6 days · Tromsø, Lofoten',
    prompt:
      'Plan a 6-day expedition to Norway for winter Northern Lights photography, fjord cruises, and authentic cabin stays.',
  },
];

function greeting(hour = new Date().getHours()): string {
  return hour >= 4 && hour < 12 ? 'Good morning' : hour >= 12 && hour < 17 ? 'Good afternoon' : 'Good evening';
}

export const ChatWelcome: React.FC<ChatWelcomeProps> = ({ onSelectPrompt }) => {
  const { user } = useAuth();
  // First name when signed in; a guest just gets the greeting.
  const name = String(user?.user_metadata?.full_name || '').trim().split(/\s+/)[0];
  const root = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;

      const split = titleRef.current ? splitLines(titleRef.current) : null;
      const tl = gsap.timeline({ defaults: { ease: EASE } });

      tl.from('.tv-welcome__mark', { scale: 0.85, opacity: 0, duration: 0.8 });

      if (split) tl.from(split.lines, { yPercent: 115, duration: 1, stagger: 0.08 }, '-=0.4');

      // The lead paragraph is the page's largest text (its LCP), so it rises early
      // instead of waiting ~1.3 s for the headline; Chrome doesn't count it while at opacity 0.
      tl.from('.tv-welcome__lead', { y: 14, opacity: 0, duration: 0.7 }, 0.25)
        .from('.tv-welcome__label', { y: 10, opacity: 0, duration: 0.5 }, '-=0.45')
        .from('.tv-welcome__card', {
          y: 22,
          opacity: 0,
          clipPath: 'inset(0% 0% 100% 0%)',
          duration: 0.85,
          stagger: 0.07,
        }, '-=0.35');

      return () => split?.revert();
    },
    { scope: root },
  );

  return (
    <div className="tv-welcome" ref={root}>
      <div className="tv-welcome__mark">
        <AssistantAvatar size={72} />
      </div>

      <h2 className="tv-display tv-welcome__title" ref={titleRef}>
        {greeting()}{name ? `, ${name}` : ''}. <em>Where are we going?</em>
      </h2>

      <p className="tv-lead tv-welcome__lead">
        Tell me the place, or just the kind of trip you’re in the mood for. I’ll ask three quick things
        (where from, where to, how many days), then plan it day by day: all at once, or one day at a time with you.
      </p>

      <span className="tv-label tv-welcome__label">Or start from one of these</span>

      <div className="tv-welcome__grid">
        {STARTER_PROMPTS.map((item) => (
          <button
            key={item.title}
            type="button"
            className="tv-card tv-card--hover tv-welcome__card"
            onClick={() => onSelectPrompt(item.prompt)}
          >
            <span className="tv-welcome__card-top">
              <span className="tv-welcome__card-title">{item.title}</span>
              <ArrowUpRightIcon className="tv-welcome__card-go" width={15} height={15} />
            </span>
            <span className="tv-meta">{item.meta}</span>
          </button>
        ))}
      </div>
    </div>
  );
};
