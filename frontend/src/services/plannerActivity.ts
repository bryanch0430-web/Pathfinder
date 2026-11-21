import { useEffect, useState } from 'react';

let busy = false;
const listeners = new Set<(value: boolean) => void>();

/** True while a planner reply is still streaming. Survives leaving the planner page. */
export function setPlannerBusy(value: boolean) {
  if (busy === value) return;
  busy = value;
  listeners.forEach((listener) => listener(value));
}

export function usePlannerBusy() {
  const [value, setValue] = useState(busy);
  useEffect(() => {
    listeners.add(setValue);
    setValue(busy);
    return () => { listeners.delete(setValue); };
  }, []);
  return value;
}
