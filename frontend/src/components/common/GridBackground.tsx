import React from "react";

interface GridBackgroundProps {
  className?: string;
}

/**
 * GridBackground — Architectural square drafting grid.
 * Zero gradients: pure SVG lines rendered across responsive tile sizes.
 * - Desktop: 48px square cells
 * - Mobile: 32px square cells
 * - Light: #D9D9D9 at subtle opacity
 * - Dark: #D9D9D9 at restrained opacity (approx 0.14)
 */
export const GridBackground: React.FC<GridBackgroundProps> = ({
  className = "",
}) => {
  return (
    <div
      className={`pointer-events-none absolute inset-0 overflow-hidden select-none z-0 ${className}`}
      aria-hidden="true"
    >
      <svg
        className="w-full h-full text-[#D9D9D9] opacity-75 dark:opacity-15"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          {/* Desktop Pattern: 48px x 48px */}
          <pattern
            id="pathfinder-drafting-grid-desktop"
            width="48"
            height="48"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 48 0 L 0 0 0 48"
              fill="none"
              stroke="currentColor"
              strokeWidth="1"
            />
          </pattern>

          {/* Mobile Pattern: 32px x 32px */}
          <pattern
            id="pathfinder-drafting-grid-mobile"
            width="32"
            height="32"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 32 0 L 0 0 0 32"
              fill="none"
              stroke="currentColor"
              strokeWidth="1"
            />
          </pattern>
        </defs>

        {/* Desktop Viewport Grid */}
        <rect
          width="100%"
          height="100%"
          fill="url(#pathfinder-drafting-grid-desktop)"
          className="hidden sm:block"
        />

        {/* Mobile Viewport Grid */}
        <rect
          width="100%"
          height="100%"
          fill="url(#pathfinder-drafting-grid-mobile)"
          className="block sm:hidden"
        />
      </svg>
    </div>
  );
};
