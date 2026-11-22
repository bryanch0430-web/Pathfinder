import type { ReactNode } from "react";
import { Icon, type IconName } from "./icons";
import { safeHref } from "../lib/safeHref";
import { useI18n, type MessageKey } from "../lib/i18n";

type DataStatus = "ok" | "stale" | "unavailable";
type Intent = "plan" | "modify" | "ask" | "unclear";
export type AgentStatus = "running" | "ok" | "partial" | "unavailable" | "timeout" | "failed";

const STATUS_LABEL: Record<DataStatus, MessageKey> = {
  ok: "status.ok",
  stale: "status.stale",
  unavailable: "status.unavailable",
};

export function StatusPill({ status, label }: { status: DataStatus; label?: string }) {
  const { t } = useI18n();
  return (
    <span className="pf-status" data-status={status}>
      <span className="pf-dot" aria-hidden="true" />
      {label ?? (STATUS_LABEL[status] ? t(STATUS_LABEL[status]) : status)}
    </span>
  );
}

const INTENT_LABEL: Record<Intent, MessageKey> = {
  plan: "intent.plan",
  modify: "intent.modify",
  ask: "intent.ask",
  unclear: "intent.unclear",
};

export function IntentBadge({ intent }: { intent: Intent }) {
  const { t } = useI18n();
  return (
    <span className="pf-intent" data-intent={intent}>
      {INTENT_LABEL[intent] ? t(INTENT_LABEL[intent]) : intent}
    </span>
  );
}

const AGENT_LABEL: Record<string, MessageKey> = {
  attraction: "agent.attraction",
  hotel: "agent.hotel",
  weather: "agent.weather",
  ticket: "agent.ticket",
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
  const { t } = useI18n();
  return (
    <span className="pf-agent-chip" data-status={status}>
      {status === "running" ? <span className="pf-spinner" style={{ width: 12, height: 12 }} /> : <Icon name={AGENT_ICON[agent] ?? "sparkles"} size={13} />}
      <span className="pf-agent-chip__text">
        {AGENT_LABEL[agent] ? t(AGENT_LABEL[agent]) : agent}
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
