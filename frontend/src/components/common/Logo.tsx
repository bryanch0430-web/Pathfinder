/*
 * Logo — the Pathfinder mark.
 *
 * A rune-like path mark. It is painted with currentColor, so it takes the ink
 * of whatever it sits in.
 */
import type { SVGProps } from 'react';

export interface LogoProps extends SVGProps<SVGSVGElement> {
  /** Kept so older call sites still type-check. The mark is a filled shape. */
  weight?: number;
}

export function Logo({ width = 22, height = 22, className, style }: LogoProps) {
  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        display: 'inline-block',
        width,
        height,
        backgroundColor: 'currentColor',
        WebkitMask: 'url(/brand/mark-mask.png) center / contain no-repeat',
        mask: 'url(/brand/mark-mask.png) center / contain no-repeat',
        ...style,
      }}
    />
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
      <Logo width={size} height={size} />
      <span className={`tv-lockup__word ${wordmarkClassName}`}>Pathfinder</span>
    </span>
  );
}
