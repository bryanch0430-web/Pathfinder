import type { ReactNode } from "react";
import { Icon, type IconName } from "./icons";
import { safeHref } from "../lib/safeHref";

type DataStatus = "ok" | "stale" | "unavailable";
type Intent = "plan" | "modify" | "ask" | "unclear";
export type AgentStatus = "running" | "ok" | "partial" | "unavailable" | "timeout" | "failed";

const STATUS_LABEL: Record<DataStatus, string> = {
  ok: "Live",
  stale: "Needs refresh",
  unavailable: "Unavailable",
};

export function StatusPill({ status, label }: { status: DataStatus; label?: string }) {
  return (
    <span className="pf-status" data-status={status}>
      <span className="pf-dot" aria-hidden="true" />
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

export function IntentBadge({ intent }: { intent: Intent }) {
  return (
    <span className="pf-intent" data-intent={intent}>
      {intent === "unclear" ? "clarify" : intent}
    </span>
  );
}

const AGENT_LABEL: Record<string, string> = {
  attraction: "Attractions",
  hotel: "Hotels",
  weather: "Weather",
  ticket: "Tickets",
};

const AGENT_ICON: Record<string, IconName> = {
  attraction: "map",
  hotel: "hotel",
  weather: "cloud",
  ticket: "ticket",
};

export function AgentChip({
  agent,
  status,
  detail,
}: {
  agent: string;
  status: AgentStatus;
  detail?: string;
}) {
  return (
    <span className="pf-agent-chip" data-status={status}>
      {status === "running" ? <span className="pf-spinner" style={{ width: 12, height: 12 }} /> : <Icon name={AGENT_ICON[agent] ?? "sparkles"} size={13} />}
      <span className="pf-agent-chip__text">
        {AGENT_LABEL[agent] ?? agent}
        {detail ? <span className="pf-muted"> · {detail}</span> : null}
      </span>
    </span>
  );
}

export function SourceLink({ title, url, source }: { title: string; url?: string; source?: string }) {
  const body = (
    <>
      <Icon name="external" size={11} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{source ? `${source}: ${title}` : title}</span>
    </>
  );
  const href = safeHref(url);
  if (!href) {
    return <span className="pf-source-link">{body}</span>;
  }
  return (
    <a className="pf-source-link" href={href} target="_blank" rel="noreferrer noopener">
      {body}
    </a>
  );
}

export function EmptyState({
  icon = "compass",
  title,
  body,
  action,
}: {
  icon?: IconName;
  title: string;
  body?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="pf-empty">
      <span className="pf-empty__icon">
        <Icon name={icon} size={22} />
      </span>
      <span className="pf-empty__title">{title}</span>
      {body ? <span className="pf-empty__body">{body}</span> : null}
      {action ? <span className="pf-empty__action">{action}</span> : null}
    </div>
  );
}
export function Delta({ value, unit = "%" }: { value: number | null; unit?: string }) {
  if (value === null || Number.isNaN(value)) {
    return <span className="pf-delta" data-direction="flat">—</span>;
  }
  const direction = value > 0 ? "up" : value < 0 ? "down" : "flat";
  return (
    <span className="pf-delta" data-direction={direction}>
      {direction === "up" ? "+" : ""}
      {value}
      {unit}
    </span>
  );
}
