import { useEffect, useId, useState, type ReactNode } from "react";
import { Badge, Button, Heading, IconButton, Input, Select, Text } from "./atoms";
import {
  AgentChip,
  Delta,
  EmptyState,
  IntentBadge,
  SourceLink,
  StatusPill,
  type AgentStatus,
} from "./elements";
import { Icon, type IconName } from "./icons";
import { useI18n, type MessageKey } from "../lib/i18n";
import type {
  AgentState,
  ClarifyPayload,
  ConstraintCheck,
  CostBreakdown,
  DataGap,
  DayPlan,
  EvalMetric,
  EvalRunSummary,
  FxSnapshot,
  PlanItem,
  TripContext,
  WeatherDay,
  DataStatus,
  Intent,
} from "./types";

export function Field({
  label,
  hint,
  error,
  required,
  children,
  wide,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
  wide?: boolean;
  htmlFor?: string;
}) {
  return (
    <label className={["pf-field", wide ? "pf-field--wide" : ""].filter(Boolean).join(" ")} htmlFor={htmlFor}>
      <span className="pf-field__label">
        {label}
        {required ? <span className="pf-field__required">*</span> : null}
      </span>
      {children}
      {error ? <span className="pf-field__error">{error}</span> : hint ? <span className="pf-field__hint">{hint}</span> : null}
    </label>
  );
}

export function SearchField({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
}) {
  const { t } = useI18n();
  return (
    <div className="pf-search">
      <span className="pf-search__icon">
        <Icon name="search" size={15} />
      </span>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder ?? t("common.search")}
        aria-label={label ?? t("common.search")}
      />
    </div>
  );
}

export function ChatBubble({
  role,
  children,
  intent,
  time,
  meta,
  above,
  streaming = false,
}: {
  role: "user" | "assistant";
  children: ReactNode;
  intent?: Intent | null;
  time?: string;
  meta?: ReactNode;
  /** rendered above the bubble body, e.g. the stop a question is about */
  above?: ReactNode;
  streaming?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="pf-bubble" data-role={role}>
      {above ? <div className="pf-bubble__above">{above}</div> : null}
      <div className="pf-bubble__body">
        {children}
        {streaming ? <span className="pf-muted" aria-label={t("chat.streaming")}>▍</span> : null}
      </div>
      <div className="pf-bubble__meta">
        {intent ? <IntentBadge intent={intent} /> : null}
        {time ? <span className="pf-mono">{time}</span> : null}
        {meta}
      </div>
    </div>
  );
}

export function TripContextForm({
  values,
  onChange,
  onSubmit,
  onSave,
  onSuggestDestination,
  submitting = false,
  saving = false,
  compact = false,
}: {
  values: Partial<TripContext>;
  onChange: (patch: Partial<TripContext>) => void;
  onSubmit: () => void;
  onSave?: () => void;
  onSuggestDestination?: (query: string) => Promise<{ label: string }[]>;
  submitting?: boolean;
  saving?: boolean;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const [destinationOptions, setDestinationOptions] = useState<{ label: string }[]>([]);
  const [preferenceTags, setPreferenceTags] = useState<string[]>(values.preferences ?? []);
  const [prefInput, setPrefInput] = useState("");
  const [constraintText, setConstraintText] = useState((values.hard_constraints ?? []).join(", "));

  const parseList = (text: string) =>
    text
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);

  useEffect(() => {
    setPreferenceTags(values.preferences ?? []);
  }, [values.preferences]);

  useEffect(() => {
    const query = (values.destination ?? "").trim();
    if (!onSuggestDestination || query.length < 3) {
      setDestinationOptions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      onSuggestDestination(query)
        .then((items) => {
          if (!cancelled) setDestinationOptions(items.slice(0, 6));
        })
        .catch(() => {
          if (!cancelled) setDestinationOptions([]);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [values.destination, onSuggestDestination]);

  const addPreference = () => {
    const next = prefInput.trim().replace(/,+$/, "");
    if (!next) return;
    const merged = preferenceTags.includes(next) ? preferenceTags : [...preferenceTags, next];
    setPreferenceTags(merged);
    onChange({ preferences: merged });
    setPrefInput("");
  };
  const removePreference = (value: string) => {
    const next = preferenceTags.filter((entry) => entry !== value);
    setPreferenceTags(next);
    onChange({ preferences: next });
  };

  // Sync only when the parent value actually differs from what is typed.
  // A naive join-on-every-update effect deletes the trailing space as soon as
  // the user types it, which also breaks multi-word constraints.

  useEffect(() => {
    const external = values.hard_constraints ?? [];
    const local = parseList(constraintText);
    if (external.join("\u0000") !== local.join("\u0000")) {
      setConstraintText(external.join(", "));
    }
  }, [values.hard_constraints, constraintText]);
  return (
    <form
      className="pf-context-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="pf-form-grid">
        <Field label={t("form.destination")} required htmlFor={`${id}-destination`}>
          <Input
            id={`${id}-destination`}
            value={values.destination ?? ""}
            onChange={(event) => onChange({ destination: event.target.value })}
            placeholder="Kyoto"
            autoComplete="off"
            list={`${id}-destination-options`}
          />
          <datalist id={`${id}-destination-options`}>
            {destinationOptions.map((option) => (
              <option key={option.label} value={option.label} />
            ))}
          </datalist>
        </Field>
        <Field label={t("form.from")} required htmlFor={`${id}-origin`}>
          <Input
            id={`${id}-origin`}
            value={values.origin ?? ""}
            onChange={(event) => onChange({ origin: event.target.value })}
            placeholder="Hong Kong"
            autoComplete="off"
          />
        </Field>
        <Field label={t("form.start")} required htmlFor={`${id}-start`}>
          <Input
            id={`${id}-start`}
            type="date"
            value={values.start_date ?? ""}
            onChange={(event) => onChange({ start_date: event.target.value || null })}
          />
        </Field>
        <Field label={t("form.end")} required htmlFor={`${id}-end`}>
          <Input
            id={`${id}-end`}
            type="date"
            value={values.end_date ?? ""}
            onChange={(event) => onChange({ end_date: event.target.value || null })}
          />
        </Field>
        <Field label={t("form.travellers")} required htmlFor={`${id}-party`}>
          <Input
            id={`${id}-party`}
            type="number"
            min={1}
            max={20}
            value={values.party_size ?? ""}
            onChange={(event) => onChange({ party_size: event.target.value ? Number(event.target.value) : null })}
          />
        </Field>
        <Field label={t("form.budget")} required wide htmlFor={`${id}-budget`}>
          <div className="pf-budget-input">
            <Input
              id={`${id}-budget`}
              type="number"
              min={0}
              step={100}
              value={values.budget ?? ""}
              onChange={(event) => onChange({ budget: event.target.value ? Number(event.target.value) : null })}
              placeholder="400000"
            />
            <Select
              aria-label={t("form.currency")}
              value={values.currency ?? "HKD"}
              onChange={(event) => onChange({ currency: event.target.value })}
            >
              {["USD", "JPY", "HKD", "CNY", "EUR", "GBP", "SGD", "THB", "TWD", "KRW", "AUD"].map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </Select>
          </div>
        </Field>
        <Field label={t("form.hotelStyle")} htmlFor={`${id}-style`}>
          <Select
            id={`${id}-style`}
            value={values.hotel_style ?? ""}
            onChange={(event) => onChange({ hotel_style: event.target.value })}
          >
            <option value="">{t("form.style.none")}</option>
            <option value="budget">{t("form.style.budget")}</option>
            <option value="midrange">{t("form.style.midrange")}</option>
            <option value="boutique">{t("form.style.boutique")}</option>
            <option value="luxury">{t("form.style.luxury")}</option>
            <option value="hostel">{t("form.style.hostel")}</option>
          </Select>
        </Field>
        {!compact ? (
          <Field label={t("form.preferences")} hint={t("form.preferencesHint")} wide htmlFor={`${id}-prefs`}>
            <div className="pf-preference-tags">
              {preferenceTags.map((preference) => (
                <span className="pf-preference-tag" key={preference}>
                  {preference}
                  <button
                    type="button"
                    aria-label={t("common.remove", { value: preference })}
                    onClick={() => removePreference(preference)}
                  >
                    <Icon name="close" size={11} />
                  </button>
                </span>
              ))}
              <input
                id={`${id}-prefs`}
                className="pf-preference-tags__input"
                value={prefInput}
                aria-label={t("settings.addPreference")}
                placeholder={preferenceTags.length ? t("settings.addAnother") : t("settings.preferencesExample")}
                onChange={(event) => setPrefInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === ",") {
                    event.preventDefault();
                    addPreference();
                  }
                  if (event.key === "Backspace" && !prefInput && preferenceTags.length) {
                    removePreference(preferenceTags[preferenceTags.length - 1]);
                  }
                }}
              />
            </div>
          </Field>
        ) : null}
        {!compact ? (
          <Field label={t("form.constraints")} hint={t("form.constraintsHint")} wide htmlFor={`${id}-constraints`}>
            <Input
              id={`${id}-constraints`}
              value={constraintText}
              onChange={(event) => {
                setConstraintText(event.target.value);
                onChange({
                  hard_constraints: event.target.value
                    .split(",")
                    .map((value) => value.trim())
                    .filter(Boolean),
                });
              }}
              // The planner reads "must include …" in English, so the example stays English.
              placeholder="must include Fushimi Inari"
            />
          </Field>
        ) : null}
      </div>
      <div className="pf-row pf-row--between">
        {onSave ? (
          <Button variant="ghost" size="sm" loading={saving} onClick={onSave} type="button">
            {t("form.save")}
          </Button>
        ) : (
          <span />
        )}
        <Button type="submit" variant="primary" loading={submitting} iconRight="arrowRight">
          {t("form.generate")}
        </Button>
      </div>
    </form>
  );
}

export function ClarifyCard({
  payload,
  values,
  onChange,
  onSubmit,
  submitting,
}: {
  payload: ClarifyPayload;
  values: Record<string, string>;
  onChange: (slot: string, value: string) => void;
  onSubmit: (message?: string) => void;
  submitting?: boolean;
}) {
  const { t } = useI18n();
  const choices = payload.choices ?? [];
  const slotsOnly = payload.slots.length > 0;
  return (
    <div className="pf-clarify">
      <div className="pf-row">
        <Icon name="sparkles" size={16} />
        <Text size="sm" weight="semibold">
          {payload.question}
        </Text>
      </div>
      <div className="pf-form-grid">
        {payload.slots.map((slot) => {
          const label =
            payload.labels[payload.slots.indexOf(slot)] ??
            slot.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
          const options = payload.options[slot] ?? [];
          const isDate = slot.includes("date");
          return (
            <Field key={slot} label={label} wide={slot === "destination"}>
              <Input
                type={isDate ? "date" : slot === "party_size" || slot === "budget" ? "number" : "text"}
                value={values[slot] ?? ""}
                onChange={(event) => onChange(slot, event.target.value)}
                placeholder={slot === "budget" ? "400000" : undefined}
              />
              {options.length ? (
                <span className="pf-clarify__options">
                  {options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className="pf-chip"
                      data-active={values[slot] === option}
                      onClick={() => onChange(slot, option)}
                    >
                      {option}
                    </button>
                  ))}
                </span>
              ) : null}
            </Field>
          );
        })}
      </div>
      {!slotsOnly && choices.length ? (
        <div className="pf-clarify__options">
          {choices.map((choice) => (
            <button
              key={choice.label}
              type="button"
              className="pf-chip"
              disabled={submitting}
              onClick={() => onSubmit(choice.message)}
            >
              {choice.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="pf-row pf-row--between">
        <Text size="xs" tone="tertiary">
          {slotsOnly
            ? t("clarify.slots")
            : choices.length
              ? t("clarify.choices")
              : t("clarify.free")}
        </Text>
        {slotsOnly ? (
          <Button variant="primary" size="sm" onClick={() => onSubmit()} loading={submitting} iconRight="send">
            {t("common.continue")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function DayStrip({
  days,
  active,
  onSelect,
}: {
  days: DayPlan[];
  active: string;
  onSelect: (date: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="pf-day-strip" role="tablist" aria-label={t("day.strip")}>
      {days.map((day, index) => {
        const date = new Date(`${day.date}T12:00:00`);
        return (
          <button
            key={day.date}
            type="button"
            role="tab"
            className="pf-day-pill"
            data-active={day.date === active}
            aria-selected={day.date === active}
            onClick={() => onSelect(day.date)}
          >
            <span>{t("day.number", { n: index + 1 })}</span>
            <b>{date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })}</b>
          </button>
        );
      })}
    </div>
  );
}

const ITEM_ICON: Record<string, IconName> = {
  attraction: "map",
  hotel: "hotel",
  meal: "meal",
  transit: "route",
  ticket: "ticket",
};

export function ItineraryItem({
  item,
  selected = false,
  updated = false,
  onSelect,
  number,
  showSource = true,
}: {
  item: PlanItem;
  selected?: boolean;
  updated?: boolean;
  onSelect?: () => void;
  number?: number;
  showSource?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div
      className="pf-item"
      data-type={item.type}
      data-status={item.data_status}
      data-selected={selected}
      data-updated={updated}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.();
        }
      }}
    >
      <div className="pf-item__time">
        {item.start}
        <br />
        {item.end}
      </div>
      <span className="pf-item__icon">
        <Icon name={ITEM_ICON[item.type] ?? "map"} size={15} />
      </span>
      <div className="pf-item__main">
        <span className="pf-item__name">
          {number ? <span className="pf-mono pf-muted">{number}. </span> : null}
          {item.name}
        </span>
        {item.notes ? <span className="pf-item__notes">{item.notes}</span> : null}
        {item.hours_status ? (
          <span className="pf-item__notes" data-hours={item.hours_status}>
            {item.hours_status === "stated" && item.opening_hours?.length
              ? t("item.hours", { hours: item.opening_hours.join("; ") })
              : t("item.hoursUnknown")}
          </span>
        ) : null}
        <span className="pf-item__meta">
          <StatusPill status={item.data_status as DataStatus} />
          {updated ? (
            <Badge tone="success" dot>
              {t("common.updated")}
            </Badge>
          ) : null}
          {item.price ? (
            <Badge tone="neutral">
              {item.price.currency} {item.price.amount.toLocaleString()}
            </Badge>
          ) : null}
          {item.locked ? (
            <Badge tone="warning" dot>
              {t("item.locked")}
            </Badge>
          ) : null}
          {showSource && item.source_refs.length ? (
            <SourceLink
              title={item.source_refs[0].title}
              url={item.source_refs[0].url}
              source={item.source_refs[0].source}
            />
          ) : null}
        </span>
      </div>
      <div className="pf-item__actions">
        {item.locked ? <Icon name="lock" size={13} /> : null}
        <Icon name="chevronRight" size={14} />
      </div>
    </div>
  );
}

export function WeatherChip({ weather }: { weather: WeatherDay | null }) {
  const { t } = useI18n();
  if (!weather) {
    return (
      <span className="pf-weather">
        <Icon name="cloud" size={13} />
        {t("weather.none")}
      </span>
    );
  }
  const hasData = weather.status !== "unavailable" && weather.high_c !== null;
  return (
    <span className="pf-weather" data-status={weather.status} title={weather.note || undefined}>
      <Icon name={hasData ? "sun" : "cloud"} size={13} />
      {hasData ? (
        <>
          {weather.status === "stale" ? t("weather.seasonal") : weather.summary} ·{" "}
          {Math.round(weather.high_c ?? 0)}° / {Math.round(weather.low_c ?? 0)}°
        </>
      ) : (
        t("weather.none")
      )}
    </span>
  );
}

export function DaySection({
  day,
  index,
  selectedItemId,
  updatedIds,
  onSelectItem,
  innerRef,
}: {
  day: DayPlan;
  index: number;
  selectedItemId?: string | null;
  updatedIds?: Set<string>;
  onSelectItem?: (item: PlanItem) => void;
  innerRef?: (node: HTMLElement | null) => void;
}) {
  const { t } = useI18n();
  const dayLabel = t("day.number", { n: index + 1 });
  return (
    <section className="pf-day" ref={innerRef} aria-label={dayLabel}>
      <div className="pf-day__head">
        <div className="pf-stack" style={{ gap: 2 }}>
          <span className="pf-day__date">{dayLabel} · {day.date}</span>
          <span className="pf-day__title">{day.title || dayLabel}</span>
          {typeof day.elevation_gain_m === "number" && day.elevation_gain_m > 0 ? (
            <span className="pf-muted">↑ {t("day.climb", { m: Math.round(day.elevation_gain_m) })}</span>
          ) : null}
        </div>
        <WeatherChip weather={day.weather} />
      </div>
      {day.items.map((item, itemIndex) => (
        <ItineraryItem
          key={item.id}
          item={item}
          number={itemIndex + 1}
          selected={selectedItemId === item.id}
          updated={updatedIds?.has(item.id) ?? false}
          onSelect={() => onSelectItem?.(item)}
        />
      ))}
      {!day.items.length ? (
        <div className="pf-gap" style={{ margin: "0 8px" }}>
          <Icon name="alert" size={13} />
          {day.note || t("day.noStops")}
        </div>
      ) : null}
    </section>
  );
}

export function AgentActivity({
  agents,
  progress,
  message,
}: {
  agents: Record<string, AgentState>;
  progress?: number;
  message?: string;
}) {
  const { t } = useI18n();
  const list = Object.values(agents);
  return (
    <div className="pf-live">
      <div className="pf-live__head">
        <span className="pf-spinner" style={{ width: 12, height: 12 }} />
        {message ?? t("chat.working")}
      </div>
      <div className="pf-agents">
        {list.map((agent) => (
          <AgentChip key={agent.agent} agent={agent.agent} status={agent.status as AgentStatus} detail={agent.summary} />
        ))}
      </div>
      {progress !== undefined ? <div className="pf-progress__track"><div className="pf-progress__bar" style={{ width: `${progress}%` }} /></div> : null}
    </div>
  );
}

export function BudgetPanel({
  cost,
  fx,
  onOptimize,
}: {
  cost: CostBreakdown;
  fx?: FxSnapshot | null;
  onOptimize?: () => void;
}) {
  const { t } = useI18n();
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const ratio = cost.budget && cost.budget > 0 ? Math.min(100, Math.round((cost.total / cost.budget) * 100)) : 42;
  const rates = fx?.rates ?? {};
  const fromRate = rates[cost.currency] ?? 1;
  const toRate = rates[displayCurrency] ?? 1;
  const converted = fromRate > 0 ? (cost.total * toRate) / fromRate : cost.total;
  const conversionCurrencies = ["USD", "HKD", "JPY", "CNY", "EUR", "GBP", "SGD", "TWD", "THB", "KRW"];
  return (
    <div className="pf-budget">
      <div className="pf-budget__row">
        <div className="pf-donut" style={{ ["--pf-donut-value" as string]: ratio }}>
          <span className="pf-donut__label">
            {cost.currency} {Math.round(cost.total).toLocaleString()}
            <span>{cost.budget ? t("budget.ofBudget") : t("budget.known")}</span>
          </span>
        </div>
        <div className="pf-legend">
          {cost.lines.length ? (
            cost.lines.map((line) => (
              <span className="pf-legend__row" key={line.label}>
                <span className="pf-legend__swatch" />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{line.label}</span>
                <span className="pf-legend__amount">{Math.round(line.amount).toLocaleString()}</span>
              </span>
            ))
          ) : (
            <span className="pf-small pf-muted">{t("budget.noCosts")}</span>
          )}
          <span className="pf-legend__row" style={{ opacity: 0.6 }}>
            <span className="pf-legend__swatch" style={{ background: "var(--p30-tone-neutral-gray300)" }} />
            {t("budget.untracked")} <span className="pf-legend__amount">{t("budget.notTracked")}</span>
          </span>
        </div>
      </div>
      {cost.budget !== null ? (
        <div className="pf-row pf-row--between">
          <span className="pf-small pf-muted">
            {cost.remaining !== null && cost.remaining >= 0
              ? t("budget.left", { amount: `${cost.currency} ${Math.round(cost.remaining).toLocaleString()}` })
              : t("budget.over", { amount: `${cost.currency} ${Math.round(Math.abs(cost.remaining ?? 0)).toLocaleString()}` })}
          </span>
          <Badge tone={cost.status === "within" ? "success" : cost.status === "over" ? "danger" : "neutral"} dot>
            {cost.status === "within" ? t("budget.within") : cost.status === "over" ? t("budget.overBudget") : t("budget.unknown")}
          </Badge>
        </div>
      ) : null}
      {fx ? (
        <div className="pf-fx">
          <div className="pf-row pf-row--between">
            <span className="pf-fx__title"><Icon name="globe" size={12} /> {t("budget.fx")}</span>
            <Badge tone={fx.source === "open.er-api" ? "success" : "neutral"} dot>
              {fx.source === "open.er-api" ? t("status.ok") : t("budget.reference")}
            </Badge>
          </div>
          <div className="pf-fx__convert">
            <span className="pf-small pf-muted">
              {cost.currency} {Math.round(cost.total).toLocaleString()} ≈
            </span>
            <Select
              aria-label={t("budget.displayCurrency")}
              value={displayCurrency}
              onChange={(event) => setDisplayCurrency(event.target.value)}
            >
              {conversionCurrencies.map((code) => (
                <option key={code} value={code}>{code}</option>
              ))}
            </Select>
            <strong>
              {displayCurrency} {Math.round(converted).toLocaleString()}
            </strong>
          </div>
          <span className="pf-fx__note">
            {t("budget.base", { base: fx.base })} · {fx.as_of} · {fx.note}
          </span>
        </div>
      ) : null}
      {onOptimize ? (
        <Button variant="ink" size="sm" block onClick={onOptimize} iconLeft="sparkles">
          {t("budget.optimise")}
        </Button>
      ) : null}
    </div>
  );
}

export function PlaceCard({
  item,
  weather,
  onClose,
}: {
  item: PlanItem | null;
  weather?: WeatherDay | null;
  onClose?: () => void;
}) {
  const { t } = useI18n();
  if (!item) {
    return (
      <div className="pf-place-card">
        <Text size="sm" tone="tertiary">
          {t("place.empty")}
        </Text>
      </div>
    );
  }
  const isStay = item.type === "hotel";
  return (
    <div className="pf-place-card">
      <div className="pf-place-card__head">
        <div className="pf-stack" style={{ gap: 2 }}>
          <Text size="sm" weight="semibold">
            {item.name}
          </Text>
          <Text size="xs" tone="tertiary">
            {isStay
              ? `${item.start} → ${item.end} · ${item.address || t("workspace.noArea")}`
              : `${item.start}–${item.end} · ${item.address || t("place.noAddress")}`}
          </Text>
        </div>
        {onClose ? <IconButton icon="close" label={t("place.clear")} onClick={onClose} /> : null}
      </div>
      <div className="pf-row pf-row--wrap">
        <StatusPill status={item.data_status} />
        {isStay ? (
          <Text size="xs" tone="tertiary">
            {item.price
              ? `${t("workspace.perNight", { price: `${item.price.currency} ${item.price.amount.toLocaleString()}` })} · ${item.price_kind}`
              : t("workspace.noRate")}
          </Text>
        ) : null}
        {weather ? <WeatherChip weather={weather} /> : null}
      </div>
      {item.notes ? (
        <Text size="xs" tone="secondary">
          {item.notes}
        </Text>
      ) : null}
      <div className="pf-stack">
        {item.source_refs.slice(0, 3).map((source) => (
          <SourceLink key={source.url + source.title} title={source.title} url={source.url} source={source.source} />
        ))}
      </div>
    </div>
  );
}

export function PlanFeedback({
  verdict,
  stars,
  added = [],
  onUseful,
  onNotUseful,
}: {
  verdict: "useful" | "not_useful" | null;
  stars?: number | null;
  added?: string[];
  onUseful: (stars: number) => void;
  onNotUseful: () => void;
}) {
  const { t } = useI18n();
  const [ratingOpen, setRatingOpen] = useState(false);
  return (
    <div className="pf-plan-feedback">
      {verdict === "useful" ? (
        <button type="button" className="pf-plan-feedback__saved" data-saved="true">
          <Icon name="check" size={13} />
          {t("feedback.markedUseful")}
        </button>
      ) : verdict === "not_useful" ? (
        <span className="pf-plan-feedback__not">{t("feedback.markedNotUseful")}</span>
      ) : (
        <>
          <button
            type="button"
            className="pf-plan-feedback__button"
            onClick={() => setRatingOpen((value) => !value)}
          >
            <Icon name="star" size={12} /> {t("saved.useful")}
          </button>
          <button type="button" className="pf-plan-feedback__button" onClick={onNotUseful}>
            {t("feedback.notUseful")}
          </button>
        </>
      )}
      {ratingOpen && verdict === null ? (
        <div className="pf-plan-feedback__rating" role="group" aria-label={t("feedback.rate")}>
          <span>{t("feedback.question")}</span>
          <div className="pf-row" style={{ gap: 2 }}>
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                key={value}
                type="button"
                className="pf-plan-feedback__star"
                aria-label={t(value > 1 ? "feedback.stars" : "feedback.star", { count: value })}
                onClick={() => {
                  setRatingOpen(false);
                  onUseful(value);
                }}
              >
                <Icon name="star" size={16} />
              </button>
            ))}
          </div>
          {stars ? <small>{t("feedback.saved", { stars })}</small> : null}
        </div>
      ) : null}
      {verdict === "useful" && added.length ? (
        <span className="pf-plan-feedback__added">+ {added.slice(0, 3).join(", ")}</span>
      ) : null}
    </div>
  );
}
export function MetricCard({ metric }: { metric: EvalMetric }) {
  const { t } = useI18n();
  return (
    <div className="pf-metric">
      <span className="pf-metric__label">{metric.label}</span>
      <span className="pf-metric__value">
        {metric.value}
        <span style={{ fontSize: 14, marginLeft: 2, color: "var(--pf-ink-tertiary)" }}>{metric.unit}</span>
      </span>
      <div className="pf-metric__foot">
        <span>{t("metric.baseline", { value: metric.baseline !== null ? `${metric.baseline}${metric.unit}` : "—" })}</span>
        <Delta value={metric.delta} unit={metric.unit === "%" ? "%" : ""} />
      </div>
    </div>
  );
}

export function ExperimentTable({
  runs,
  onSelect,
  activeId,
}: {
  runs: EvalRunSummary[];
  onSelect?: (runId: string) => void;
  activeId?: string;
}) {
  const { t } = useI18n();
  return (
    <table className="pf-exp-table">
      <thead>
        <tr>
          <th>{t("table.run")}</th>
          <th>{t("table.cases")}</th>
          <th>{t("table.latency")}</th>
          <th>{t("table.tokens")}</th>
          <th>{t("table.created")}</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr
            key={run.id}
            onClick={() => onSelect?.(run.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") onSelect?.(run.id);
            }}
            tabIndex={onSelect ? 0 : undefined}
            role={onSelect ? "button" : undefined}
            aria-label={onSelect ? t("table.open", { tag: run.experiment_tag }) : undefined}
            style={{ cursor: onSelect ? "pointer" : undefined }}
            data-active={activeId === run.id || undefined}
          >
            <td>
              <div className="pf-stack" style={{ gap: 1 }}>
                <span>{run.experiment_tag}</span>
                <span className="pf-small pf-muted">{run.dataset_id}</span>
              </div>
            </td>
            <td>
              {run.passed}/{run.case_count}
            </td>
            <td>{run.latency_ms} ms</td>
            <td>{run.token_usage.toLocaleString()}</td>
            <td className="pf-nowrap">{new Date(run.created_at).toLocaleDateString("en-GB")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TrendChart({ runs, metricKey }: { runs: EvalRunSummary[]; metricKey: string }) {
  const { t } = useI18n();
  const points = runs
    .slice()
    .reverse()
    .map((run) => ({ label: run.experiment_tag, value: run.metrics.find((metric) => metric.key === metricKey)?.value ?? 0 }))
    .filter((point) => Number.isFinite(point.value));
  if (points.length < 2) {
    return <EmptyState icon="route" title={t("trend.empty")} body={t("trend.emptyBody")} />;
  }
  const width = 600;
  const height = 150;
  const padding = 16;
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coords = points.map((point, index) => ({
    x: padding + (index * (width - padding * 2)) / (points.length - 1),
    y: height - padding - ((point.value - min) / span) * (height - padding * 2),
    ...point,
  }));
  const path = coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`).join(" ");
  return (
    <div className="pf-trend">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("trend.label", { metric: metricKey })}>
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line key={fraction} className="pf-trend__grid" x1={padding} x2={width - padding} y1={padding + fraction * (height - padding * 2)} y2={padding + fraction * (height - padding * 2)} />
        ))}
        <path className="pf-trend__line" d={path} />
        {coords.map((point) => (
          <circle key={point.label} className="pf-trend__dot" cx={point.x} cy={point.y} r={4} />
        ))}
      </svg>
      <div className="pf-row pf-row--between pf-small pf-muted">
        {points.map((point) => (
          <span key={point.label}>{point.label}</span>
        ))}
      </div>
    </div>
  );
}

export function GapList({ gaps }: { gaps: DataGap[] }) {
  const { t } = useI18n();
  if (!gaps.length) {
    return <Text size="xs" tone="tertiary">{t("gaps.empty")}</Text>;
  }
  return (
    <div className="pf-gap-list">
      {gaps.map((gap, index) => (
        <div className="pf-gap" key={`${gap.section}-${index}`}>
          <Icon name={gap.kind === "no_data" ? "info" : "alert"} size={13} />
          <span>
            <b style={{ textTransform: "capitalize" }}>{gap.section}</b> · {gap.message}
          </span>
        </div>
      ))}
    </div>
  );
}

const CHECK_LABEL: Record<ConstraintCheck["status"], MessageKey> = {
  pass: "constraint.pass",
  fail: "constraint.fail",
  unknown: "constraint.unknown",
};

export function ConstraintList({ checks }: { checks: ConstraintCheck[] }) {
  const { t } = useI18n();
  if (!checks.length) return <Text size="xs" tone="tertiary">{t("constraint.empty")}</Text>;
  return (
    <div className="pf-stack">
      {checks.map((check) => (
        <div className="pf-row" key={check.id} style={{ alignItems: "flex-start" }}>
          <Badge tone={check.status === "pass" ? "success" : check.status === "fail" ? "danger" : "warning"} dot>
            {CHECK_LABEL[check.status] ? t(CHECK_LABEL[check.status]) : check.status}
          </Badge>
          <div className="pf-stack" style={{ gap: 1 }}>
            <Text size="sm">{check.text}</Text>
            <Text size="xs" tone="tertiary">{check.evidence}</Text>
          </div>
        </div>
      ))}
    </div>
  );
}
export function MiniStat({ icon, label, value }: { icon: IconName; label: string; value: string }) {
  return (
    <span className="pf-row" style={{ gap: 6 }}>
      <Icon name={icon} size={14} />
      <span className="pf-small pf-muted">{label}</span>
      <span className="pf-small" style={{ fontWeight: 600 }}>{value}</span>
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="pf-eval__head">
      <div className="pf-stack" style={{ gap: 4 }}>
        {eyebrow ? <span className="pf-small pf-muted" style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>{eyebrow}</span> : null}
        <Heading level={2}>{title}</Heading>
        {description ? <Text size="sm" tone="secondary">{description}</Text> : null}
      </div>
      {action}
    </header>
  );
}
export function StatusSummary({ plan }: { plan: { constraints: ConstraintCheck[]; data_quality: { gaps: DataGap[] } } }) {
  const { t } = useI18n();
  const failing = plan.constraints.filter((check) => check.status === "fail").length;
  const gaps = plan.data_quality.gaps.length;
  return (
    <div className="pf-row pf-row--wrap">
      <Badge tone={failing ? "danger" : "success"} dot>
        {failing
          ? t(failing > 1 ? "summary.failing" : "summary.failingOne", { count: failing })
          : t("summary.hold")}
      </Badge>
      <Badge tone={gaps ? "warning" : "neutral"} dot>
        {gaps ? t(gaps > 1 ? "summary.gaps" : "summary.gapsOne", { count: gaps }) : t("summary.noGaps")}
      </Badge>
    </div>
  );
}
