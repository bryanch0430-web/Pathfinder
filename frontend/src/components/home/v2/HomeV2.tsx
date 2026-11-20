/*
 * HomeV2 — the redesigned marketing surface.
 *
 * The hero direction is settled: the Bento grid carrying the Horizon headline.
 * The five-variant switcher from the review pass has been removed.
 *
 * Scrolling is smoothed by Lenis and driven through GSAP's ticker so the
 * pinned capabilities section stays locked to the smoothed position.
 */
import { useEffect, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import { useSmoothScroll } from '../../../hooks/useSmoothScroll';
import { NavbarV2 } from './NavbarV2';
import { SidebarV2 } from './SidebarV2';
import { FooterV2 } from './FooterV2';
import {
  AgentPreview,
  Capabilities,
  CtaBand,
  Destinations,
  HowItWorks,
  Marquee,
  Questions,
} from './SectionsV2';
import { HeroBento } from './heroes/HeroBento';
import { TravelJournals } from './journal/TravelJournals';

gsap.registerPlugin(useGSAP, ScrollTrigger);

export interface HomeV2Props {
  onStartPlanning: () => void;
  onNavigateExplore?: () => void;
  onNavigateLogin?: () => void;
  onNavigateSignup?: () => void;
  onNavigateProfile?: () => void;
}

export function HomeV2({
  onStartPlanning,
  onNavigateExplore,
  onNavigateLogin,
  onNavigateSignup,
  onNavigateProfile,
}: HomeV2Props) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const lenisRef = useSmoothScroll(true);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  // Freeze the smoothed scroll behind the drawer rather than toggling
  // body overflow, which would fight Lenis and jump the position.
  useEffect(() => {
    const lenis = lenisRef.current;
    if (drawerOpen) {
      lenis ? lenis.stop() : (document.body.style.overflow = 'hidden');
    } else {
      lenis ? lenis.start() : (document.body.style.overflow = '');
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [drawerOpen, lenisRef]);

  // Late-loading photography changes every pin and trigger measurement.
  useEffect(() => {
    const onLoad = () => ScrollTrigger.refresh();
    window.addEventListener('load', onLoad);
    const t = window.setTimeout(onLoad, 1200);
    return () => {
      window.removeEventListener('load', onLoad);
      window.clearTimeout(t);
    };
  }, []);

  const scrollTop = () => {
    const lenis = lenisRef.current;
    if (lenis) lenis.scrollTo(0, { duration: 1.1 });
    else window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleGetStarted = () => {
    setDrawerOpen(false);
    (onNavigateSignup ?? onStartPlanning)();
  };

  const handleLogin = () => {
    setDrawerOpen(false);
    onNavigateLogin?.();
  };

  return (
    <div className="tv2 tv2--bone">
      <NavbarV2
        scrolled={scrolled}
        tone="solid"
        onMenuOpen={() => setDrawerOpen(true)}
        onGetStarted={handleGetStarted}
        onStartPlanning={onStartPlanning}
        onNavigateExplore={onNavigateExplore}
        onNavigateHome={scrollTop}
        onNavigateProfile={onNavigateProfile}
      />

      <SidebarV2
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onGetStarted={handleGetStarted}
        onLogin={handleLogin}
        onStartPlanning={onStartPlanning}
        onNavigateExplore={onNavigateExplore}
        onNavigateHome={scrollTop}
        onNavigateProfile={onNavigateProfile}
      />

      <main>
        <HeroBento onStartPlanning={onStartPlanning} onNavigateExplore={onNavigateExplore} />
        <HowItWorks />
        <Capabilities />
        <Marquee />
        <AgentPreview />
        <Destinations onNavigateExplore={onNavigateExplore} />
        <TravelJournals />
        <Questions />
        <CtaBand onStartPlanning={onStartPlanning} />
      </main>

      <FooterV2 onStartPlanning={onStartPlanning} onNavigateExplore={onNavigateExplore} />
    </div>
  );
}
