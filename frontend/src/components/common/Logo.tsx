/*
 * Logo — the single source of truth for the Pathfinder mark.
 *
 * Path traced from `frontend/media/icons/Vector.svg`: an arc horizon that
 * breaks downward into a trailing line. Every surface in the application
 * imports this component rather than redrawing the mark.
 *
 * The stroke inherits `currentColor`, so the mark takes the ink of whatever
 * it sits in (dark on paper, paper on the inverted CTA band).
 */
import type { SVGProps } from 'react';

const MARK_PATH =
  'M21.1666 29.7512C18.2854 27.7765 16.5928 27.6342 13.1423 27.6402C9.43882 27.6402 5.96519 28.679 3.125 30.7045C3.125 27.0828 3.81086 23.4966 5.14343 20.1507C6.476 16.8047 8.42917 13.7644 10.8914 11.2035C13.3537 8.64264 16.2768 6.61122 19.4939 5.22527C22.711 3.83932 26.1591 3.12598 29.6413 3.12598C33.1234 3.12598 36.5715 3.83932 39.7886 5.22527C43.0057 6.61122 45.9288 8.64264 48.3911 11.2035C50.8533 13.7644 52.8065 16.8047 54.1391 20.1507C55.4717 23.4966 56.1575 27.0828 56.1575 30.7045C53.3114 28.6698 49.8525 27.6402 46.1403 27.6402C43.2737 27.6397 40.45 28.3641 37.9126 29.7512C35.3751 31.1383 33.2001 33.1464 31.5749 35.6023C29.9497 38.0583 28.9232 40.8884 28.5838 43.8489C28.2443 46.8093 28.6022 49.8112 29.6265 52.5957';

export interface LogoProps extends SVGProps<SVGSVGElement> {
  /** Stroke weight in the source viewBox's units. */
  weight?: number;
}

export function Logo({ weight = 6.25139, ...props }: LogoProps) {
  return (
    <svg viewBox="0 0 60 54" fill="none" aria-hidden="true" {...props}>
      <path
        d={MARK_PATH}
        stroke="currentColor"
        strokeWidth={weight}
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Logo + wordmark lockup. `size` drives the mark; the wordmark scales with it.
 */
export function LogoLockup({
  size = 22,
  className = '',
  wordmarkClassName = '',
}: {
  size?: number;
  className?: string;
  wordmarkClassName?: string;
}) {
  return (
    <span className={`tv-lockup ${className}`}>
      <Logo width={size} height={(size * 54) / 60} />
      <span className={`tv-lockup__word ${wordmarkClassName}`}>Pathfinder</span>
    </span>
  );
}
