import type { KeyboardEvent } from "react";

/**
 * Key handling for a tab list, as the ARIA tabs pattern asks: the arrow keys, Home and End move focus between its
 * tabs (the ends wrap round). Nothing opens until a tab is chosen with Enter or Space, which the buttons do themselves.
 */
export function onTablistKeyDown(event: KeyboardEvent<HTMLElement>): void {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'));
  const at = tabs.findIndex((tab) => tab === document.activeElement);
  if (at < 0) return;
  const last = tabs.length - 1;
  let next: number;
  if (event.key === "ArrowRight") next = at === last ? 0 : at + 1;
  else if (event.key === "ArrowLeft") next = at === 0 ? last : at - 1;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = last;
  else return;
  event.preventDefault();
  tabs[next]?.focus();
}
