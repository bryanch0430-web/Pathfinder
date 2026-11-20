import React from 'react';

interface BotMarkProps {
  className?: string;
  size?: number;
}

/**
 * Replaceable geometric mark representing Pathfinder AI.
 * Follows sharp editorial geometry with high-contrast monochrome design.
 */
export const BotMark: React.FC<BotMarkProps> = ({ className = '', size = 36 }) => {
  return (
    <div
      className={`inline-flex items-center justify-center bg-[#1F1E1E] text-white select-none shrink-0 ${className}`}
      style={{ width: size, height: size }}
      aria-label="Pathfinder AI Intelligence Mark"
    >
      <svg
        width={size * 0.6}
        height={size * 0.6}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="square"
        strokeLinejoin="miter"
      >
        {/* Sharp geometric diamond compass / AI nexus */}
        <polygon points="12,2 22,12 12,22 2,12" />
        <line x1="12" y1="6" x2="12" y2="18" />
        <line x1="6" y1="12" x2="18" y2="12" />
        <rect x="10" y="10" width="4" height="4" fill="currentColor" />
      </svg>
    </div>
  );
};
