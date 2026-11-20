/*
 * GuideCast: the whole cast as an overlapping row. Hover lifts a face, click makes that guide
 * the one who rides along (the floating companion) and lets them introduce themselves.
 */
import { useState } from 'react';
import { GuideCharacter } from './GuideCharacter';
import { GUIDES, companionGuide, setCompanionGuide } from './guides';
import '../../styles/guide-cast.css';

export function GuideCast({ size = 44 }: { size?: number }) {
  const [current, setCurrent] = useState(() => companionGuide().id);
  const [say, setSay] = useState<string | null>(null);

  const choose = (id: string) => {
    const guide = GUIDES.find((g) => g.id === id)!;
    setCurrent(id);
    setCompanionGuide(id);
    setSay(`${guide.name}: ${guide.line}`);
  };

  return (
    <div className="tv-cast">
      <ul className="tv-cast__row" style={{ '--cast-size': `${size}px` } as React.CSSProperties} aria-label="Meet the guides">
        {GUIDES.map((guide) => (
          <li key={guide.id} className={guide.id === current ? 'is-current' : ''}>
            <GuideCharacter guide={guide} size={size} still interactive label={`${guide.name}. ${guide.line}`} onPoke={() => choose(guide.id)} />
          </li>
        ))}
      </ul>
      <p className="tv-cast__line" role="status" aria-live="polite">{say ?? 'Six guides. Pick who plans your trip with you.'}</p>
    </div>
  );
}
