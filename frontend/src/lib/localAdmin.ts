/**
 * Whether this page runs in local admin mode. The backend says so in /api/ready, asked once per page; until it
 * answers, and when it cannot be reached, the mode is off and the page is the usual one.
 */
import { useSyncExternalStore } from "react";

import { localAdminFrom } from "./access";
import { getReady } from "./api";

let active = false;
let asked: Promise<boolean> | null = null;
const listeners = new Set<() => void>();

/** The mode as last reported; false until /api/ready has answered. */
export function isLocalAdmin(): boolean {
  return active;
}

/** Asks /api/ready once (again after a failure) and resolves to the mode. */
export function loadLocalAdmin(): Promise<boolean> {
  asked ??= getReady().then(
    (body) => {
      const next = localAdminFrom(body);
      if (next !== active) {
        active = next;
        listeners.forEach((listener) => listener());
      }
      return next;
    },
    () => {
      // Not reachable now: the next caller asks again.
      asked = null;
      return active;
    },
  );
  return asked;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  void loadLocalAdmin();
  return () => {
    listeners.delete(listener);
  };
}

/** The mode, re-rendering the component when /api/ready reports it. */
export function useLocalAdmin(): boolean {
  return useSyncExternalStore(subscribe, isLocalAdmin, () => false);
}
