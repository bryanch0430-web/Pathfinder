import type { CSSProperties, ReactNode } from "react";

export type IconName =
  | "compass"
  | "map"
  | "route"
  | "calendar"
  | "users"
  | "wallet"
  | "sparkles"
  | "plane"
  | "train"
  | "walk"
  | "car"
  | "hotel"
  | "ticket"
  | "meal"
  | "sun"
  | "cloud"
  | "rain"
  | "search"
  | "send"
  | "check"
  | "alert"
  | "clock"
  | "refresh"
  | "lock"
  | "chevronDown"
  | "chevronRight"
  | "close"
  | "plus"
  | "minus"
  | "play"
  | "pause"
  | "star"
  | "arrowRight"
  | "globe"
  | "shield"
  | "memory"
  | "edit"
  | "trash"
  | "external"
  | "layers"
  | "zoomIn"
  | "zoomOut"
  | "info"
  | "book"
  | "download"
  | "user"
  | "logout"
  | "spark";

const PATHS: Record<IconName, ReactNode> = {
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2.1 5-5 2.1 2.1-5z" />
    </>
  ),
  map: (
    <>
      <path d="m9 4 6 2 5-2v14l-5 2-6-2-5 2V6z" />
      <path d="M9 4v14M15 6v14" />
    </>
  ),
  route: (
    <>
      <circle cx="6" cy="19" r="2.4" />
      <circle cx="18" cy="5" r="2.4" />
      <path d="M8.4 19h4.1a4 4 0 0 0 4-4V9.4" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <path d="M16 5.5a3 3 0 0 1 0 5.8M17 20a5.5 5.5 0 0 0-2-4.3" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </>
  ),
  logout: (
    <>
      <path d="M10 5H5v14h5" />
      <path d="M14 8l4 4-4 4M18 12H9" />
    </>
  ),
  wallet: (
    <>
      <rect x="3" y="6" width="18" height="14" rx="2" />
      <path d="M3 10h18M16 15h2" />
    </>
  ),
  sparkles: (
    <>
      <path d="m12 3 1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7z" />
      <path d="m19 15 .7 1.8L21.5 17.5l-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z" />
    </>
  ),
  plane: <path d="M21 12 3 6l3.5 6L3 18l18-6z" />,
  train: (
    <>
      <rect x="5" y="3" width="14" height="14" rx="3" />
      <path d="M8 21l2-4M16 21l-2-4M5 10h14M9 6h6" />
    </>
  ),
  walk: (
    <>
      <circle cx="13" cy="4.5" r="2" />
      <path d="m10.5 21 2.2-5.4 2.1 2.1V21M9 11.5l3-2.4 3 3 2.5.8M12 9.1 9.8 14" />
    </>
  ),
  car: (
    <>
      <path d="M4 16.5 6 10h12l2 6.5v3H4z" />
      <path d="M6 10l1.4-3.5h9.2L18 10M7 19.5v-2M17 19.5v-2" />
      <circle cx="8" cy="14.5" r="1" />
      <circle cx="16" cy="14.5" r="1" />
    </>
  ),
  hotel: (
    <>
      <path d="M3 21V8l9-5 9 5v13" />
      <path d="M9 21v-6h6v6M3 21h18" />
    </>
  ),
  ticket: (
    <>
      <path d="M3 9a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2H5v-2a2 2 0 0 0 0-4z" />
      <path d="M9 7v10" />
    </>
  ),
  meal: (
    <>
      <path d="M6 3v7a2 2 0 0 0 4 0V3M8 10v11M17 3c-1.7 1.6-2.5 3.7-2.5 6.3 0 1.8.8 2.7 2.5 2.7V21M17 3v18" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  cloud: <path d="M6.5 18a4.5 4.5 0 0 1-.4-9 6 6 0 0 1 11.6 1.7A3.6 3.6 0 0 1 17.5 18z" />,
  rain: (
    <>
      <path d="M6.5 15a4.5 4.5 0 0 1-.4-9 6 6 0 0 1 11.6 1.7A3.6 3.6 0 0 1 17.5 15z" />
      <path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  send: <path d="M21 3 10.8 13.2M21 3l-6.5 18-3.7-7.8L3 9.5z" />,
  check: <path d="m4.5 12.5 5 5 10-11" />,
  alert: (
    <>
      <path d="M12 3 2.8 20h18.4z" />
      <path d="M12 9v5M12 17.5v.2" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 11a8 8 0 1 0-2.3 6.1" />
      <path d="M20 4v7h-7" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  play: <path d="M8 5v14l11-7z" />,
  pause: <path d="M8 5v14M16 5v14" />,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z" />,
  arrowRight: <path d="M4 12h16M14 6l6 6-6 6" />,
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.4 2.4 3.6 5.4 3.6 9s-1.2 6.6-3.6 9c-2.4-2.4-3.6-5.4-3.6-9S9.6 5.4 12 3z" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 4.5 6v6c0 4.4 3 7.6 7.5 9 4.5-1.4 7.5-4.6 7.5-9V6z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  memory: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M9 9h6v6H9zM4 10h-2M4 14H2M22 10h-2M22 14h-2M10 4V2M14 4V2M10 22v-2M14 22v-2" />
    </>
  ),
  edit: (
    <>
      <path d="M4 20h4L20 8l-4-4L4 16z" />
      <path d="m14 6 4 4" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
      <path d="M10 11v5M14 11v5" />
    </>
  ),
  external: (
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 14v6H4V6h6" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5z" />
      <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
    </>
  ),
  zoomIn: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5M11 8v6M8 11h6" />
    </>
  ),
  zoomOut: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5M8 11h6" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.2" />
    </>
  ),
  book: (
    <>
      <path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z" />
      <path d="M4 17h15" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12M7 10l5 5 5-5" />
      <path d="M4 21h16" />
    </>
  ),
  spark: <path d="M12 2v6M12 16v6M2 12h6M16 12h6M5 5l4 4M15 15l4 4M19 5l-4 4M9 15l-4 4" />,
};

export interface IconProps {
  name: IconName;
  size?: number;
  label?: string;
  className?: string;
  style?: CSSProperties;
}

export function Icon({ name, size = 18, label, className, style }: IconProps) {
  return (
    <svg
      className={["pf-icon", className].filter(Boolean).join(" ")}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={style}
    >
      {PATHS[name]}
    </svg>
  );
}
