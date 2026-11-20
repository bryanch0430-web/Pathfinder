/*
 * Pathfinder v2 icon set.
 *
 * Hand-drawn on a 24x24 grid at a uniform 1.75 stroke weight — deliberately
 * thicker than a hairline icon library so glyphs hold their own next to
 * Instrument Serif display type. No third-party icon dependency.
 */
import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

export function MenuIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3.5 7h17M3.5 12h17M3.5 17h17" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function ArrowRightIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 12h15.5M13.5 6l6 6-6 6" />
    </svg>
  );
}

export function ArrowUpRightIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M7 17L17 7M8.5 7H17v8.5" />
    </svg>
  );
}

export function ArrowDownIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 4.5v15M6 13.5l6 6 6-6" />
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function MinusIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M5 12h14" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  );
}

/**
 * Brand mark. Re-exported from the shared `Logo` component so there is exactly
 * one copy of the real `Vector.svg` path in the codebase.
 */
export { Logo as LogoMark } from '../../common/Logo';

/** Spatial graph — three nodes wired into a path. */
export function GraphIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="5.5" cy="6.5" r="2.4" />
      <circle cx="18.5" cy="11" r="2.4" />
      <circle cx="8" cy="18.5" r="2.4" />
      <path d="M7.7 7.4l8.5 2.9M16.9 13l-6.9 4.2" />
    </svg>
  );
}

export function RouteIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M8.2 6H14a3.4 3.4 0 0 1 0 6.8h-4a3.4 3.4 0 0 0 0 6.8h5.8" />
    </svg>
  );
}

export function PinIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.6" />
    </svg>
  );
}

export function CompassIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M15.2 8.8l-2 4.4-4.4 2 2-4.4 4.4-2z" />
    </svg>
  );
}

export function GlobeIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.4 2.5 3.6 5.4 3.6 8.5S14.4 18 12 20.5C9.6 18 8.4 15.1 8.4 12S9.6 6 12 3.5z" />
    </svg>
  );
}

export function ClockIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.2V12l3.2 1.9" />
    </svg>
  );
}

export function WalletIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3.8 8.2A2.2 2.2 0 0 1 6 6h11.2a2.2 2.2 0 0 1 2.2 2.2v8.6a2.2 2.2 0 0 1-2.2 2.2H6a2.2 2.2 0 0 1-2.2-2.2V8.2z" />
      <path d="M15.4 12.5h3.9" />
    </svg>
  );
}

export function ChatIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M20 12.6c0 3.7-3.6 6.7-8 6.7a9.5 9.5 0 0 1-2.6-.35L4.6 20.4l1.2-3.4A6.3 6.3 0 0 1 4 12.6c0-3.7 3.6-6.7 8-6.7s8 3 8 6.7z" />
    </svg>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3.6l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 10.6l5.1-1.9L12 3.6z" />
      <path d="M18.6 16.4l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8.8-2.1z" />
    </svg>
  );
}

export function LayersIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3.6l8.4 4.3-8.4 4.3-8.4-4.3L12 3.6z" />
      <path d="M3.6 12.6L12 16.9l8.4-4.3M3.6 16.6L12 20.9l8.4-4.3" />
    </svg>
  );
}

export function PlaneIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M10.3 13.7L3.6 11.4l1.6-1.6 4.6.6 3.6-3.6a2.4 2.4 0 0 1 3.4 3.4l-3.6 3.6.6 4.6-1.6 1.6-2.3-6.7-.2-.2h.6z" />
    </svg>
  );
}

export function BedIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3.5 18.5v-11M3.5 11.8h17v6.7M3.5 15.2h17" />
      <circle cx="7.6" cy="10.2" r="1.9" />
    </svg>
  );
}

export function ForkKnifeIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M7 3.6v6.2a2.2 2.2 0 0 0 4.4 0V3.6M9.2 12v8.4M16.6 3.6c-1.5 1.1-2.2 2.9-2.2 5.2 0 1.6.7 2.6 2.2 2.9v8.7" />
    </svg>
  );
}

export function PlayIcon(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <circle cx="12" cy="12" r="10.6" stroke="currentColor" strokeWidth={1.4} />
      <path d="M9.9 8.4l6.2 3.6-6.2 3.6V8.4z" fill="currentColor" />
    </svg>
  );
}
