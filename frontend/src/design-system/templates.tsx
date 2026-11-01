import type { ReactNode } from "react";

export function PlannerTemplate({
  header,
  left,
  centre,
  right,
}: {
  header: ReactNode;
  left: ReactNode;
  centre: ReactNode;
  right: ReactNode;
}) {
  return (
    <div className="pf-app">
      {header}
      <div className="pf-planner">
        <div className="pf-planner__left">{left}</div>
        <div className="pf-planner__centre">{centre}</div>
        <div className="pf-planner__right">{right}</div>
      </div>
    </div>
  );
}
