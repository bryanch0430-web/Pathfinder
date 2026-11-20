/*
 * CreditsPage: every data source, font, library, photograph and character used in Pathfinder.
 * The list itself lives in lib/credits.ts; a test there fails when a photo goes uncredited.
 */
import type { ReactNode } from 'react';
import { AppBar, type AppBarProps } from '../components/common/AppBar';
import { GROUPS, type CreditRow, type PhotoSet } from '../lib/credits';
import '../styles/credits.css';

function External({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
      <span className="tv-credits__sr"> (opens in a new tab)</span>
    </a>
  );
}

function Row({ row }: { row: CreditRow }) {
  return (
    <li className="tv-credits__row">
      <span className="tv-credits__name">{row.href ? <External href={row.href}>{row.name}</External> : row.name}</span>
      <span className="tv-credits__note">{row.note}</span>
      <span className="tv-credits__licence">{row.licence}</span>
    </li>
  );
}

function Photos({ set }: { set: PhotoSet }) {
  return (
    <div className="tv-credits__photoset">
      <h3><External href={set.href}>{set.site}</External></h3>
      <p>{set.licence}</p>
      <ul className="tv-credits__photos">
        {set.credits.map((credit) => (
          <li key={credit.url}><External href={credit.url}>{credit.name}</External></li>
        ))}
      </ul>
    </div>
  );
}

export function CreditsPage(props: Omit<AppBarProps, 'current'>) {
  return (
    <div className="tv2 tv2-app tv-credits">
      <AppBar {...props} />
      <main className="tv-container tv-credits__main">
        <header className="tv-credits__intro">
          <h1 className="tv-display tv-page__title">Built on other people's <em>good work.</em></h1>
          <p className="tv-lead">
            The data, fonts, code and photographs Pathfinder uses, and the licence each one comes with. Where
            something isn't ours and isn't licensed for us, it says so.
          </p>
        </header>

        {GROUPS.map((group) => (
          <section key={group.id} id={group.id} className="tv-credits__group" aria-labelledby={`credits-${group.id}`}>
            <div className="tv-credits__lead">
              <h2 id={`credits-${group.id}`}>{group.title}</h2>
              <p>{group.intro}</p>
            </div>
            <div>
              {group.rows && <ul className="tv-credits__rows">{group.rows.map((row) => <Row key={row.name} row={row} />)}</ul>}
              {group.photos?.map((set) => <Photos key={set.site} set={set} />)}
            </div>
          </section>
        ))}
      </main>
    </div>
  );
}

export default CreditsPage;
