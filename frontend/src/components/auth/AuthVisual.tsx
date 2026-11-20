/*
 * AuthVisual — the photographic pane beside the auth form.
 *
 * Rotates slowly through the curated collection with a cross-fade, so the
 * page has a heartbeat without anything moving under the reader's eye. The
 * caption is set in the marketing system's display serif.
 */
import { useEffect, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { AUTH_VISUALS, type AuthVisualItem } from '../../constants/authVisuals';
import { prefersReducedMotion, EASE } from '../home/v2/motion';

interface AuthVisualProps {
  visual?: AuthVisualItem;
  imageSrc?: string;
  destination?: string;
  location?: string;
  experience?: string;
  objectPosition?: string;
  /** Rotate through the whole collection instead of holding one frame. */
  rotate?: boolean;
}

const ROTATE_MS = 6500;

export function AuthVisual({
  visual,
  imageSrc,
  destination,
  location,
  experience,
  objectPosition,
  rotate = true,
}: AuthVisualProps) {
  // Start the rotation on whichever frame the caller pinned, so the first
  // paint matches what the page asked for.
  const startIndex = Math.max(
    0,
    AUTH_VISUALS.findIndex((v) => v.id === visual?.id),
  );

  const [index, setIndex] = useState(startIndex);
  const root = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const metaRef = useRef<HTMLDivElement>(null);

  const frames = rotate ? AUTH_VISUALS : [visual ?? AUTH_VISUALS[startIndex]];
  const current = frames[index % frames.length] ?? AUTH_VISUALS[0];

  const src = imageSrc ?? current.imageSrc;
  const pos = objectPosition ?? current.objectPosition;
  const dest = destination ?? current.destination;
  const loc = location ?? current.location;
  const exp = experience ?? current.experience;

  useEffect(() => {
    if (!rotate || frames.length < 2 || prefersReducedMotion()) return;
    const t = window.setInterval(() => setIndex((i) => (i + 1) % frames.length), ROTATE_MS);
    return () => window.clearInterval(t);
  }, [rotate, frames.length]);

  // Cross-fade the photograph and lift the caption on every change.
  useGSAP(
    () => {
      if (prefersReducedMotion()) return;
      gsap.fromTo(imgRef.current, { opacity: 0, scale: 1.06 }, { opacity: 1, scale: 1, duration: 1.5, ease: EASE });
      gsap.fromTo(
        metaRef.current?.children ?? [],
        { y: 18, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.9, ease: EASE, stagger: 0.07 },
      );
    },
    { scope: root, dependencies: [index] },
  );

  return (
    <div className="tv-auth__visual" ref={root}>
      <img
        ref={imgRef}
        key={src}
        src={src}
        alt={`${dest} — ${loc}`}
        style={{ objectPosition: pos }}
        className="tv-img"
      />
      <span className="tv-auth__visual-wash" aria-hidden="true" />

      <div className="tv-auth__visual-meta tv-invert" ref={metaRef}>
        <span className="tv-label">{current.expeditionTag}</span>
        <h2 className="tv-display tv-auth__visual-title">{dest}</h2>
        <p className="tv-meta">{loc}</p>
        <p className="tv-meta" style={{ maxWidth: '38ch' }}>
          {exp}
        </p>

        {rotate && frames.length > 1 && (
          <div className="tv-auth__dots" role="tablist" aria-label="Featured destinations">
            {frames.map((f, i) => (
              <button
                key={f.id}
                type="button"
                role="tab"
                aria-selected={i === index}
                aria-label={f.destination}
                className={`tv-auth__dot ${i === index ? 'is-on' : ''}`}
                onClick={() => setIndex(i)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
