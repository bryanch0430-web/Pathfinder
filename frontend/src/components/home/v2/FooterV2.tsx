/*
 * FooterV2 — document-style colophon.
 *
 * An oversized wordmark rule, a three-column link index, and a meta strip.
 * Separated by hairlines only; no boxed cards, no dark slab.
 */
import { openCredits, openPath } from '../../common/AppBar';
import { FOOTER_NAV } from './content';
import { LogoMark, ArrowUpRightIcon } from './IconsV2';
import { FeedbackForm } from './FeedbackForm';

export interface FooterV2Props {
  onStartPlanning?: () => void;
  onNavigateExplore?: () => void;
}

/** Scroll to a home-page section, waiting for the home page to render after a route change. */
function scrollToSection(id: string) {
  const started = Date.now();
  const tryScroll = () => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else if (Date.now() - started < 4000) window.setTimeout(tryScroll, 120);
  };
  tryScroll();
}

/** Rendered on the home page and, via App, under every other page except the planner. */
export function FooterV2({ onStartPlanning, onNavigateExplore }: FooterV2Props) {
  const year = new Date().getFullYear();
  const startPlanning = onStartPlanning ?? (() => openPath('/create'));

  const handle = (e: React.MouseEvent<HTMLAnchorElement>, key?: string) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return; // let "open in new tab" through
    const href = e.currentTarget.getAttribute('href') || '';
    e.preventDefault();
    if (key === 'explore') return onNavigateExplore ? onNavigateExplore() : openPath('/explore');
    if (key === 'plan') return startPlanning();
    if (href.startsWith('#')) {
      // A home-page section: go home first when it isn't on this page.
      const id = href.slice(1);
      if (!document.getElementById(id)) openPath('/');
      return scrollToSection(id);
    }
    openPath(href);
  };

  return (
    <footer className="tv-footer" id="contact">
      <div className="tv-container tv-container--wide">
        <div className="tv-footer__top">
          <div className="tv-footer__pitch">
            <LogoMark width={26} height={26} />
            <p className="tv-display tv-footer__line">
              Plan the whole trip, <em>not the paragraph about it.</em>
            </p>
            <button type="button" className="tv-btn tv-btn--primary" onClick={startPlanning}>
              <span>Start planning</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
          </div>

          <div className="tv-footer__nav">
            {FOOTER_NAV.map((col) => (
              <div key={col.heading} className="tv-footer__col">
                <h3 className="tv-label">{col.heading}</h3>
                <ul>
                  {col.links.map((l) => (
                    <li key={l.label}>
                      <a href={l.href} onClick={(e) => handle(e, (l as { key?: string }).key)}>
                        {l.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <FeedbackForm />
        <hr className="tv-rule" />

        <div className="tv-footer__meta">
          <span className="tv-meta">© {year} Pathfinder</span>
          <span className="tv-meta tv-hide-mobile">Built with LangGraph, React Three Fiber, and FastAPI</span>
          <div className="tv-footer__legal">
            <a
              href="/credits"
              className="tv-meta"
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey) return; // let "open in new tab" through
                e.preventDefault();
                openCredits();
              }}
            >
              Credits
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
