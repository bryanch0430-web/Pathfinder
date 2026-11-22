import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { readRouteChoices, type RouteChoice } from "./routeShift";

type Setter = (next: Record<string, RouteChoice> | ((current: Record<string, RouteChoice>) => Record<string, RouteChoice>)) => void;

type RouteChoiceState = {
  choices: Record<string, RouteChoice>;
  setChoices: Setter;
  markSave: () => void;
  cancelSave: () => void;
};

const RouteChoiceContext = createContext<RouteChoiceState | null>(null);

export function RouteChoiceProvider({
  tripId,
  version,
  children,
}: {
  tripId: string;
  version: number;
  children: ReactNode;
}) {
  const [choices, setChoices] = useState(() => readRouteChoices(tripId, version));
  const own = useRef(false);
  useEffect(() => {
    if (own.current) {
      own.current = false;
      return;
    }
    setChoices(readRouteChoices(tripId, version));
  }, [tripId, version]);
  const value = useMemo<RouteChoiceState>(
    () => ({
      choices,
      setChoices,
      markSave: () => {
        own.current = true;
      },
      cancelSave: () => {
        own.current = false;
      },
    }),
    [choices],
  );
  return <RouteChoiceContext.Provider value={value}>{children}</RouteChoiceContext.Provider>;
}

export function useRouteChoices(): RouteChoiceState | null {
  return useContext(RouteChoiceContext);
}
