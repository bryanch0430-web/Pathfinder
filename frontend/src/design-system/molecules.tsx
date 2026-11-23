import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Badge, Button, Checkbox, Heading, IconButton, Input, Select, Text } from "./atoms";
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
import { isPendingChoice } from "./pendingChoice";
import {
  MAX_TRIP_DAYS,
  amountIn,
  budgetShown,
  budgetStored,
  dayCount,
  endBounds,
  endOpensOnStart,
  keepsPlanAnyway,
} from "../lib/clarifyInput";
import { useI18n, type MessageKey } from "../lib/i18n";
import { TRIP_FIELD_MAX_LENGTH, TRIP_LINE_MAX_LENGTH, TRIP_LINES_MAX, TRIP_STOPS_MAX } from "../lib/limits";
import { currencyCodes, currencyOptionLabel } from "../lib/currency";
import { profileOf } from "../lib/profile";
import { useRouteChoices } from "../lib/routeChoices";
import { choiceInto } from "../lib/routeShift";
import { imageSrc, safeHref } from "../lib/safeHref";
import { addTags, isImeKey, limitList, splitList } from "../lib/typing";
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
  IntakePayload,
  MealSlot,
  PlanAlternative,
  PlanAssessment,
  PlanItem,
  TripContext,
  WeatherDay,
  DataStatus,
  ImageRef,
  Intent,
  Link,
  TravellerProfile,
} from "./types";

export function TravelOptions({
  data,
  loading = false,
  error = null,
  selected = null,
  onSelect,
}: {
  data: import("../lib/api").LegOptions | null;
  loading?: boolean;
  error?: string | null;
  selected?: string | null;
  onSelect?: (option: import("../lib/api").LegOption) => void;
}) {
  const { t } = useI18n();
  const labels: Record<string, MessageKey> = {
    walk: "map.optionWalk",
    cycle: "map.optionCycle",
    transit: "map.optionTransit",
    drive: "map.optionTaxi",
  };
  const icons = { walk: "walk", cycle: "bike", transit: "train", drive: "car" } as const;
  if (loading) return <Text size="sm" tone="tertiary">{t("map.optionsLoading")}</Text>;
  if (error) return <Text size="sm" tone="danger">{error}</Text>;
  if (!data) return null;
  return (
    <div className="pf-travel-options">
      <Text size="sm" tone="secondary">
        {t("map.optionsFor", { from: data.from_name, to: data.to_name })}
      </Text>
      {data.options.map((option) => (
        <button
          type="button"
          className="pf-travel-option"
          data-mode={option.mode}
          data-active={selected === option.mode}
          key={option.mode}
          onClick={() => onSelect?.(option)}
        >
          <span className="pf-travel-option__icon">
            <Icon name={icons[option.mode]} size={16} />
          </span>
          <div className="pf-stack pf-grow" style={{ gap: 1 }}>
            <Text size="sm" weight="semibold">
              {option.mode === "walk" && option.ferry
                ? t("map.optionWalkFerry")
                : t(labels[option.mode])}
            </Text>
            <Text size="xs" tone="tertiary">
              {option.km !== null ? `${option.km} km · ` : ""}
              {option.routed
                ? t("map.optionRouted")
                : option.mode === "walk"
                  ? t("map.optionNoWalk")
                  : t("map.optionEstimate")}
            </Text>
          </div>
          <Text size="sm" weight="semibold">
            {option.minutes !== null ? t("map.minutes", { minutes: option.minutes }) : "—"}
          </Text>
        </button>
      ))}
      <Text size="xs" tone="tertiary">{t("map.optionsNote")}</Text>
    </div>
  );
}

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

const INLINE_MD = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]]+\]\([^)]+\))/g;

function inlineMd(text: string, key: string): ReactNode[] {
  return text
    .split(INLINE_MD)
    .filter((part) => part !== "")
    .map((part, index) => {
      const token = `${key}-${index}`;
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return <strong key={token}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return <code key={token}>{part.slice(1, -1)}</code>;
      }
      if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
        return <em key={token}>{part.slice(1, -1)}</em>;
      }
      const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (link) {
        const href = safeHref(link[2]);
        return href ? (
          <a key={token} href={href} target="_blank" rel="noreferrer noopener">
            {link[1]}
          </a>
        ) : (
          part
        );
      }
      return part;
    });
}

type MdBlock = { kind: "p" | "ul" | "ol"; items: string[] };

/**
 * Small, safe Markdown for assistant replies: bold/italic/code, links, and bullet or
 * numbered lists. It builds React nodes and never injects HTML, so model text cannot
 * become markup.
 */
export function MarkdownText({ text }: { text: string }) {
  const blocks: MdBlock[] = [];
  let current: MdBlock | null = null;
  const flush = () => {
    if (current) {
      blocks.push(current);
      current = null;
    }
  };
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flush();
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      if (!current || current.kind !== "ul") {
        flush();
        current = { kind: "ul", items: [] };
      }
      current.items.push(bullet[1]);
      continue;
    }
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ordered) {
      if (!current || current.kind !== "ol") {
        flush();
        current = { kind: "ol", items: [] };
      }
      current.items.push(ordered[1]);
      continue;
    }
    const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (heading) {
      flush();
      blocks.push({ kind: "p", items: [`**${heading[1]}**`] });
      continue;
    }
    if (!current || current.kind !== "p") {
      flush();
      current = { kind: "p", items: [] };
    }
    current.items.push(line);
  }
  flush();
  return (
    <div className="pf-md">
      {blocks.map((block, blockIndex) => {
        if (block.kind === "p") {
          return (
            <p key={blockIndex}>
              {block.items.map((line, lineIndex) => (
                <Fragment key={lineIndex}>
                  {lineIndex ? <br /> : null}
                  {inlineMd(line, `${blockIndex}-${lineIndex}`)}
                </Fragment>
              ))}
            </p>
          );
        }
        const List = block.kind === "ul" ? "ul" : "ol";
        return (
          <List key={blockIndex}>
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>{inlineMd(item, `${blockIndex}-${itemIndex}`)}</li>
            ))}
          </List>
        );
      })}
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

/**
 * Tells the traveller that a list of the trip form is full. The form keeps no more than TRIP_LINES_MAX lines,
 * and without this note a line typed after the last one is dropped without a word. The region is always in the
 * page and only its text comes and goes, which is what makes a screen reader announce it; the words, not a
 * colour, carry the message.
 */
function LimitNote({ full }: { full: boolean }) {
  const { t } = useI18n();
  return (
    <span className="pf-field__hint pf-field__limit" role="status">
      {full ? t("form.linesLimit", { max: TRIP_LINES_MAX }) : null}
    </span>
  );
}

/** One line of the city list: the text typed and the nights typed, as the inputs hold them. */
type CityRow = { city: string; nights: string };

function rowsFrom(values: Partial<TripContext>): CityRow[] {
  const stops = values.stops ?? [];
  const destination = (values.destination ?? "").trim();
  const same = (city: string) => city.trim().toLowerCase() === destination.toLowerCase();
  if (stops.length > 1) {
    return stops.map((stop) => ({ city: stop.city, nights: stop.nights == null ? "" : String(stop.nights) }));
  }
  // One leftover stop that is not the place on the form (Tokyo kept, Chiba shown) is still part of the trip.
  if (stops.length === 1 && destination && !same(stops[0].city)) {
    const stop = stops[0];
    return [
      { city: stop.city, nights: stop.nights == null ? "" : String(stop.nights) },
      { city: destination, nights: "" },
    ];
  }
  return [{ city: values.destination ?? "", nights: "" }];
}

/** What the rows write into the form: the first city as the destination, and the stops when there are several. */
function writeRows(rows: CityRow[]): Pick<TripContext, "destination" | "stops"> {
  const filled = rows.filter((row) => row.city.trim());
  return {
    destination: filled[0]?.city ?? rows[0]?.city ?? "",
    stops:
      filled.length > 1
        ? filled.map((row) => ({ city: row.city.trim(), nights: row.nights ? Number(row.nights) : null }))
        : [],
  };
}

const LINK_LABEL: Record<Link["kind"], MessageKey> = {
  booking: "link.book",
  official: "link.website",
  map: "link.map",
  tickets: "link.tickets",
  menu: "link.menu",
};

/** Book / Website / Map for a stop, stay or fare: only the links the server built, only http(s), opened in a new tab. */
export function LinkRow({ links }: { links?: Link[] }) {
  const { t } = useI18n();
  const shown = (links ?? []).map((link) => ({ link, href: safeHref(link.url) })).filter((entry) => entry.href);
  if (!shown.length) return null;
  return (
    <span className="pf-link-row">
      {shown.map(({ link, href }) => (
        <a
          key={`${link.kind}-${href}`}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
        >
          {link.label || t(LINK_LABEL[link.kind])}
        </a>
      ))}
    </span>
  );
}

/** A picture a source returned, with its credit; nothing at all when there is none or the address is not safe to load. */
export function PlaceImage({ image, compact = false }: { image?: ImageRef | null; compact?: boolean }) {
  const { t } = useI18n();
  const src = imageSrc(image?.url);
  if (!image || !src) return null;
  const credit = image.attribution ? t("image.credit", { credit: image.attribution }) : "";
  return (
    <figure className="pf-image" data-compact={compact}>
      <img src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" />
      {credit ? <figcaption className="pf-image__credit" title={credit}>{credit}</figcaption> : null}
    </figure>
  );
}

/** The reason phrases the server writes (fixed English), by the message that shows each in the reader's language. */
const REASON_MESSAGES: Record<string, MessageKey> = {
  "within budget": "reason.budget",
  "lowest price": "reason.lowestPrice",
  "cheapest": "reason.cheapest",
  "best rated": "reason.bestRated",
  "closest to your stops": "reason.closestStops",
  "closest to the centre": "reason.closestCentre",
  "closest": "reason.closest",
  "matches what you asked of the room": "reason.matchesRoom",
  "fastest door to door": "reason.fastest",
};

/** Rank and the reasons behind it for an option of a stay, a stop or a leg; nothing for a lone option with no reason. */
export function RankNote({ rank, reasons }: { rank?: number | null; reasons?: string[] }) {
  const { t } = useI18n();
  const shown = (reasons ?? []).map((phrase) => (REASON_MESSAGES[phrase] ? t(REASON_MESSAGES[phrase]) : phrase));
  if (rank == null || (rank <= 1 && !shown.length)) return null;
  return (
    <span className="pf-rank-note">
      <span className="pf-rank-note__badge" data-top={rank === 1 ? "true" : "false"} aria-label={t("rank.label", { rank })}>
        {t("rank.badge", { rank })}
      </span>
      {shown.length ? <span className="pf-rank-note__reasons">{shown.join(" · ")}</span> : null}
    </span>
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
  fx = null,
}: {
  values: Partial<TripContext>;
  onChange: (patch: Partial<TripContext>) => void;
  onSubmit: () => void;
  onSave?: () => void;
  onSuggestDestination?: (query: string) => Promise<{ label: string }[]>;
  submitting?: boolean;
  saving?: boolean;
  compact?: boolean;
  fx?: FxSnapshot | null;
}) {
  const { t } = useI18n();
  const id = useId();
  const [destinationOptions, setDestinationOptions] = useState<{ label: string }[]>([]);
  // The cities as typed. A line the traveller has added but not filled stays on the page and is not sent on.
  const profile = profileOf({ pace: values.pace ?? "balanced", profile: values.profile });
  const [rows, setRows] = useState<CityRow[]>(() => rowsFrom(values));
  const [activeRow, setActiveRow] = useState(0);
  const [preferenceTags, setPreferenceTags] = useState<string[]>(values.preferences ?? []);
  const [prefInput, setPrefInput] = useState("");
  const [constraintText, setConstraintText] = useState((values.hard_constraints ?? []).join(", "));
  const tripStart = values.start_date ?? "";
  const tripEnd = values.end_date ?? "";
  const tripDays = dayCount(tripStart, tripEnd);
  const tripDateError =
    tripStart && tripEnd && tripDays !== null && tripDays < 1
      ? t("clarify.dateOrder")
      : tripDays !== null && tripDays > MAX_TRIP_DAYS
        ? t("clarify.dateLong", { count: tripDays })
        : "";
  const tripEndLimit = endBounds(tripStart);

  useEffect(() => {
    setPreferenceTags(values.preferences ?? []);
  }, [values.preferences]);

  // The page hands in a new function on every render, and it renders on every keystroke in any field of the
  // form. Asking the provider depends on the destination text alone, so the latest function is read from a ref:
  // with the function in the dependency list, typing a budget or a note asked for the same destination again,
  // and a few minutes of that used up the server's per-minute budget for place suggestions.
  const suggestRef = useRef(onSuggestDestination);
  suggestRef.current = onSuggestDestination;
  const canSuggest = Boolean(onSuggestDestination);

  useEffect(() => {
    // The form changed from outside (another trip opened, a message filled the cities): show what it holds.
    if (JSON.stringify(writeRows(rows)) !== JSON.stringify(writeRows(rowsFrom(values)))) setRows(rowsFrom(values));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values.destination, values.stops]);

  const commitRows = (next: CityRow[]) => {
    setRows(next);
    onChange(writeRows(next));
  };
  const setRow = (index: number, patch: Partial<CityRow>) =>
    commitRows(rows.map((row, at) => (at === index ? { ...row, ...patch } : row)));
  const moveRow = (index: number, by: -1 | 1) => {
    const next = [...rows];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    commitRows(next);
  };

  const typedCity = rows[activeRow]?.city ?? "";
  useEffect(() => {
    const query = typedCity.trim();
    if (!canSuggest || query.length < 3) {
      setDestinationOptions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      suggestRef.current?.(query)
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
  }, [typedCity, canSuggest]);

  const addPreference = () => {
    const merged = addTags(preferenceTags, prefInput, TRIP_LINES_MAX, TRIP_LINE_MAX_LENGTH);
    setPreferenceTags(merged);
    onChange({ preferences: merged });
    setPrefInput("");
  };
  // Text still in the tag field is a tag the traveller meant to add: saving or planning takes it along.
  const addPendingPreference = () => {
    if (prefInput.trim()) addPreference();
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
    const local = splitList(constraintText);
    if (external.join("\u0000") !== local.join("\u0000")) {
      setConstraintText(external.join(", "));
    }
  }, [values.hard_constraints, constraintText]);
  return (
    <form
      className="pf-context-form"
      // The Enter that confirms an input-method candidate is not a submit. WebKit sends it after the composition
      // has ended, when the browser would otherwise submit the form (and plan a trip from a half-filled form).
      onKeyDown={(event) => {
        if (event.key === "Enter" && isImeKey(event)) event.preventDefault();
      }}
      onSubmit={(event) => {
        event.preventDefault();
        addPendingPreference();
        onSubmit();
      }}
    >
      <div className="pf-form-grid">
        <div className={rows.length > 1 ? "pf-field pf-field--wide" : "pf-field"} role="group" aria-labelledby={`${id}-cities-label`}>
          <span className="pf-field__label" id={`${id}-cities-label`}>
            {rows.length > 1 ? t("form.cities") : t("form.destination")}
            <span className="pf-field__required">*</span>
          </span>
          <div className="pf-city-rows">
            {rows.map((row, index) => {
              const label = rows.length > 1 ? t("form.cityN", { n: index + 1 }) : t("form.destination");
              const name = row.city.trim() || label;
              return (
                <div className="pf-city-row" key={index}>
                  <Input
                    id={index === 0 ? `${id}-destination` : `${id}-destination-${index}`}
                    aria-label={label}
                    value={row.city}
                    onChange={(event) => setRow(index, { city: event.target.value })}
                    onFocus={() => setActiveRow(index)}
                    placeholder=""
                    autoComplete="off"
                    maxLength={TRIP_FIELD_MAX_LENGTH}
                    list={`${id}-destination-options`}
                  />
                  {rows.length > 1 ? (
                    <>
                      <Input
                        className="pf-city-row__nights"
                        type="number"
                        min={1}
                        max={60}
                        aria-label={t("form.cityNights", { value: name })}
                        placeholder={t("form.nightsShort")}
                        value={row.nights}
                        onChange={(event) => setRow(index, { nights: event.target.value })}
                      />
                      <IconButton
                        icon="chevronDown"
                        className="pf-city-row__up"
                        label={t("form.moveUp", { value: name })}
                        disabled={index === 0}
                        onClick={() => moveRow(index, -1)}
                      />
                      <IconButton
                        icon="chevronDown"
                        label={t("form.moveDown", { value: name })}
                        disabled={index === rows.length - 1}
                        onClick={() => moveRow(index, 1)}
                      />
                      <IconButton
                        icon="close"
                        label={t("common.remove", { value: name })}
                        onClick={() => commitRows(rows.filter((_, at) => at !== index))}
                      />
                    </>
                  ) : null}
                </div>
              );
            })}
            <datalist id={`${id}-destination-options`}>
              {destinationOptions.map((option) => (
                <option key={option.label} value={option.label} />
              ))}
            </datalist>
          </div>
          <div className="pf-city-actions">
            <Button
              size="sm"
              variant="ghost"
              iconLeft="plus"
              type="button"
              disabled={rows.length >= TRIP_STOPS_MAX}
              onClick={() => {
                setActiveRow(rows.length);
                commitRows([...rows, { city: "", nights: "" }]);
              }}
            >
              {t("form.addCity")}
            </Button>
            {rows.length >= TRIP_STOPS_MAX ? (
              <span className="pf-field__hint" role="status">
                {t("form.citiesLimit", { max: TRIP_STOPS_MAX })}
              </span>
            ) : null}
          </div>
        </div>
        <Field label={t("form.from")} required htmlFor={`${id}-origin`}>
          <Input
            id={`${id}-origin`}
            value={values.origin ?? ""}
            onChange={(event) => onChange({ origin: event.target.value })}
            placeholder=""
            autoComplete="off"
            maxLength={TRIP_FIELD_MAX_LENGTH}
          />
          {rows.length > 1 ? (
            <Checkbox
              label={t("form.oneWay")}
              checked={Boolean(values.one_way)}
              onChange={(event) => onChange({ one_way: event.target.checked })}
            />
          ) : null}
        </Field>
        <Field label={t("form.start")} required htmlFor={`${id}-start`}>
          <Input
            id={`${id}-start`}
            type="date"
            value={values.start_date ?? ""}
            onChange={(event) => onChange({ start_date: event.target.value || null })}
          />
        </Field>
        <Field label={t("form.end")} required htmlFor={`${id}-end`} error={tripDateError || undefined}>
          <Input
            id={`${id}-end`}
            type="date"
            min={tripEndLimit?.min}
            max={tripEndLimit?.max}
            value={tripEnd}
            invalid={Boolean(tripDateError)}
            onPointerDown={(event) => {
              if (tripEndLimit && endOpensOnStart(tripStart, event.currentTarget.value)) event.currentTarget.value = tripEndLimit.min;
            }}
            onBlur={(event) => {
              if (event.currentTarget.value !== tripEnd) event.currentTarget.value = tripEnd;
            }}
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
              step="any"
              value={values.budget ?? ""}
              onChange={(event) => onChange({ budget: event.target.value ? Number(event.target.value) : null })}
              placeholder=""
            />
            <Input
              id={`${id}-currency`}
              aria-label={t("form.currency")}
              list={`${id}-currency-list`}
              value={values.currency ?? "HKD"}
              placeholder={t("form.currencySearch")}
              onChange={(event) => onChange({ currency: event.target.value.toUpperCase().slice(0, 3) })}
            />
            <datalist id={`${id}-currency-list`}>
              {currencyCodes(fx, values.currency).map((code) => (
                <option key={code} value={code} label={currencyOptionLabel(code)} />
              ))}
            </datalist>
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
        <Field label={t("form.pace")} htmlFor={`${id}-pace`}>
          <Select
            id={`${id}-pace`}
            value={profile.pace}
            onChange={(event) => onChange({ profile: { ...profile, pace: event.target.value as TravellerProfile["pace"] } })}
          >
            <option value="relaxed">{t("form.pace.relaxed")}</option>
            <option value="balanced">{t("form.pace.balanced")}</option>
            <option value="packed">{t("form.pace.packed")}</option>
          </Select>
        </Field>
        <Field label={t("form.who")}>
          <Checkbox
            label={t("form.withChildren")}
            checked={profile.with_children}
            onChange={(event) => onChange({ profile: { ...profile, with_children: event.target.checked } })}
          />
          <Checkbox
            label={t("form.business")}
            checked={profile.purpose === "business"}
            onChange={(event) => onChange({ profile: { ...profile, purpose: event.target.checked ? "business" : "leisure" } })}
          />
          <Checkbox
            label={t("form.stepFree")}
            checked={profile.step_free}
            onChange={(event) => onChange({ profile: { ...profile, step_free: event.target.checked } })}
          />
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
                placeholder={preferenceTags.length ? t("settings.addAnother") : ""}
                onChange={(event) => setPrefInput(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.key === "Enter" || event.key === ",") && !isImeKey(event)) {
                    event.preventDefault();
                    addPreference();
                  }
                  if (event.key === "Backspace" && !prefInput && preferenceTags.length) {
                    removePreference(preferenceTags[preferenceTags.length - 1]);
                  }
                }}
              />
            </div>
            <LimitNote full={preferenceTags.length >= TRIP_LINES_MAX} />
          </Field>
        ) : null}
        {!compact ? (
          <Field label={t("form.constraints")} hint={t("form.constraintsHint")} wide htmlFor={`${id}-constraints`}>
            <Input
              id={`${id}-constraints`}
              value={constraintText}
              onChange={(event) => {
                setConstraintText(event.target.value);
                onChange({ hard_constraints: limitList(splitList(event.target.value), TRIP_LINES_MAX, TRIP_LINE_MAX_LENGTH) });
              }}
              // The planner reads "must include …" in English, so the example stays English.
              placeholder=""
            />
            <LimitNote full={splitList(constraintText).length >= TRIP_LINES_MAX} />
          </Field>
        ) : null}
      </div>
      <div className="pf-row pf-row--between">
        <div className="pf-row">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={() => {
              setRows([{ city: "", nights: "" }]);
              setPreferenceTags([]);
              setPrefInput("");
              setConstraintText("");
              onChange({
                destination: "",
                origin: "",
                start_date: null,
                end_date: null,
                days: null,
                party_size: null,
                budget: null,
                hotel_style: "",
                pace: "balanced",
                profile: { pace: "balanced", with_children: false, purpose: "leisure", step_free: false },
                preferences: [],
                hard_constraints: [],
                stops: [],
                one_way: false,
              });
              onSave?.();
            }}
          >
            {t("form.clear")}
          </Button>
          {onSave ? (
            <Button
              variant="ghost"
              size="sm"
              loading={saving}
              onClick={() => {
                addPendingPreference();
                onSave();
              }}
              type="button"
            >
              {t("form.save")}
            </Button>
          ) : null}
        </div>
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
  const id = useId();
  const choices = payload.choices ?? [];
  // Older stored clarify cards were made before the backend added the currency
  // field. If the question itself asks about a currency, show the field anyway.
  const asksCurrency = /currency|(HKD|TWD|JPY|USD|EUR|GBP)/i.test(payload.question);
  const slots = asksCurrency && !payload.slots.includes("currency") ? [...payload.slots, "currency"] : payload.slots;
  const slotsOnly = slots.length > 0;
  // A number the trip could not take (a party of -2) flags its field and holds Continue;
  // an empty field is just unanswered and the backend still re-asks anything missing.
  const badNumber = (slot: string): boolean => {
    const raw = (values[slot] ?? "").trim();
    // A required count is not "answered" by leaving the field blank; the card's
    // Continue button stays disabled until a whole number is chosen or typed.
    if (!raw) return slot === "party_size" || slot === "days";
    if (slot === "budget") {
      if (keepsPlanAnyway(raw)) return false;
      const amount = amountIn(raw);
      return amount === null || amount < 0;
    }
    const amount = Number(raw);
    if (slot === "party_size") return !Number.isInteger(amount) || amount < 1 || amount > 20;
    if (slot === "days") return !Number.isInteger(amount) || amount < 1 || amount > 60;
    return false;
  };
  const dateProblem = (slot: string): boolean => {
    if (slot !== "dates") return false;
    const span = dateSpan(values[slot] ?? "");
    const count = dayCount(span.start, span.end);
    return !span.start || !span.end || count === null || count < 1 || count > MAX_TRIP_DAYS;
  };
  const badCurrency = (slot: string): boolean => {
    if (slot !== "currency") return false;
    return !/^[A-Z]{3}$/.test((values[slot] ?? "").trim().toUpperCase());
  };
  const blocked = slots.some(badNumber) || slots.some(dateProblem) || slots.some(badCurrency);
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
          // Travellers are whole people: never offer a range ("3–4") or "5+" in a
          // number field, because clicking it makes the field invalid. The free
          // field still accepts a larger group.
          const options = slot === "party_size" ? ["1", "2", "3", "4", "5", "6"] : payload.options[slot] ?? [];
          const suggested = payload.suggestions?.find((item) => item.slot === slot)?.name ?? "";
          if (slot === "currency") {
            const code = (values[slot] ?? "").toUpperCase().slice(0, 3);
            return (
              <Field key={slot} label={label} wide>
                <Input
                  id={`${id}-currency-${slot}`}
                  aria-label={label}
                  list={`${id}-currency-list-${slot}`}
                  value={code}
                  placeholder={t("form.currencySearch")}
                  invalid={badCurrency(slot)}
                  onChange={(event) => onChange(slot, event.target.value.toUpperCase().slice(0, 3))}
                />
                <datalist id={`${id}-currency-list-${slot}`}>
                  {currencyCodes(null, code).map((choice) => (
                    <option key={choice} value={choice} label={currencyOptionLabel(choice)} />
                  ))}
                </datalist>
              </Field>
            );
          }
          if (slot === "dates") {
            return (
              <DateRangeField
                key={slot}
                value={values[slot] ?? ""}
                options={payload.options[slot] ?? []}
                onChange={(next) => onChange(slot, next)}
              />
            );
          }
          const isDate = slot === "start_date" || slot === "end_date" || slot === "date";
          return (
            <Field key={slot} label={label} wide={slot === "destination"}>
              <Input
                type={isDate ? "date" : slot === "party_size" || slot === "budget" || slot === "days" ? "number" : "text"}
                min={slot === "party_size" || slot === "days" ? 1 : slot === "budget" ? 0 : undefined}
                max={slot === "party_size" ? 20 : slot === "days" ? 60 : undefined}
                step={slot === "party_size" || slot === "days" ? 1 : undefined}
                inputMode={slot === "party_size" || slot === "days" ? "numeric" : undefined}
                invalid={badNumber(slot)}
                value={slot === "budget" ? budgetShown(values[slot] ?? "") : values[slot] ?? ""}
                onChange={(event) => {
                  const next = event.target.value;
                  // Keep the leading whole-number part. "2.5" becomes "2", not "25";
                  // "-2" becomes empty. The backend would otherwise truncate it.
                  const whole = next.match(/^\d*/)?.[0] ?? "";
                  onChange(slot, slot === "party_size" || slot === "days" ? whole : next);
                }}
                placeholder={slot === "budget" ? "400000" : undefined}
                title={suggested || undefined}
              />
              {suggested || options.length ? (
                <span className="pf-clarify__options">
                  {suggested ? (
                    <button
                      type="button"
                      className="pf-chip"
                      data-active={(values[slot] ?? "").trim() === suggested}
                      onClick={() => onChange(slot, slot === "budget" ? budgetStored(suggested) : suggested)}
                    >
                      {t("clarify.usePlace")}
                    </button>
                  ) : null}
                  {options.map((option) => {
                    const stored = slot === "budget" ? budgetStored(option) : option;
                    return (
                      <button
                        key={option}
                        type="button"
                        className="pf-chip"
                        data-active={values[slot] === stored}
                        onClick={() => onChange(slot, stored)}
                      >
                        {option}
                      </button>
                    );
                  })}
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
              onClick={() => onSubmit(choice.message || choice.label)}
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
          <Button variant="primary" size="sm" onClick={() => onSubmit()} loading={submitting} disabled={blocked} iconRight="send">
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
  cityOf,
}: {
  days: DayPlan[];
  active: string;
  onSelect: (date: string) => void;
  /** The city a day is in; the strip then names each city above its days. */
  cityOf?: (day: DayPlan) => string;
}) {
  const { t } = useI18n();
  return (
    <div className="pf-day-strip" role="tablist" aria-label={t("day.strip")}>
      {days.map((day, index) => {
        const date = new Date(`${day.date}T12:00:00`);
        const city = cityOf?.(day);
        const startsCity = Boolean(city) && (index === 0 || cityOf?.(days[index - 1]) !== city);
        return (
          <Fragment key={day.date}>
            {startsCity ? (
              <span className="pf-day-strip__city" role="presentation">
                {city}
              </span>
            ) : null}
          <button
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
          </Fragment>
        );
      })}
    </div>
  );
}

function dateSpan(value: string): { start: string; end: string } {
  const found = value.match(/\d{4}-\d{2}-\d{2}/g) ?? [];
  return { start: found[0] ?? "", end: found[1] ?? "" };
}

function DateRangeField({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const span = dateSpan(value);
  const [start, setStart] = useState(span.start);
  const [end, setEnd] = useState(span.end);
  const count = dayCount(start, end);
  const endLimit = endBounds(start);
  const error =
    start && !end
      ? t("clarify.dateNeedBoth")
      : count !== null && count < 1
        ? t("clarify.dateOrder")
        : count !== null && count > MAX_TRIP_DAYS
          ? t("clarify.dateLong", { count })
          : "";
  const write = (nextStart: string, nextEnd: string) => {
    setStart(nextStart);
    setEnd(nextEnd);
    onChange(nextStart && nextEnd ? `${nextStart} to ${nextEnd}` : nextStart);
  };
  const hint = count !== null && count >= 1 && count <= MAX_TRIP_DAYS ? t("clarify.dateHint", { count }) : undefined;
  return (
    <div className="pf-field pf-field--wide">
      <span className="pf-dates">
        <label>
          <span className="pf-field__label">{t("clarify.dateStart")}</span>
          <Input type="date" value={start} invalid={Boolean(error)} onChange={(event) => write(event.target.value, end)} />
        </label>
        <label>
          <span className="pf-field__label">{t("clarify.dateEnd")}</span>
          <Input
            type="date"
            value={end}
            min={endLimit?.min}
            max={endLimit?.max}
            invalid={Boolean(error)}
            onPointerDown={(event) => {
              if (endLimit && endOpensOnStart(start, event.currentTarget.value)) event.currentTarget.value = endLimit.min;
            }}
            onBlur={(event) => {
              if (event.currentTarget.value !== end) event.currentTarget.value = end;
            }}
            onChange={(event) => write(start, event.target.value)}
          />
        </label>
      </span>
      {options.length ? (
        <span className="pf-clarify__options">
          {options.map((option) => (
            <button key={option} type="button" className="pf-chip" data-active={value === option} onClick={() => { const next = dateSpan(option); write(next.start, next.end); }}>
              {option}
            </button>
          ))}
        </span>
      ) : null}
      {error ? <span className="pf-field__error">{error}</span> : hint ? <span className="pf-field__hint">{hint}</span> : null}
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

const MEAL_SLOT_KEY: Record<MealSlot, MessageKey> = {
  breakfast: "options.slot.breakfast",
  lunch: "options.slot.lunch",
  dinner: "options.slot.dinner",
  snack: "options.slot.snack",
};

type Translator = ReturnType<typeof useI18n>["t"];

/** A distance computed by the app: metres under 1 km, else km with one decimal. */
export function formatDistance(metres: number, t: Translator): string {
  return metres < 1000
    ? t("options.metres", { m: Math.round(metres) })
    : t("options.km", { km: (metres / 1000).toFixed(1) });
}

/** The app's own leg from the previous stop; nothing when the plan has none. */
function transferLine(item: PlanItem, t: Translator, picked?: { mode: string; minutes: number; km: number | null }): string {
  if (picked && picked.minutes > 0) {
    const walking = picked.mode === "walk";
    const time = walking
      ? t("options.walk", { minutes: Math.round(picked.minutes) })
      : t("options.travel", { minutes: Math.round(picked.minutes), mode: picked.mode });
    const distance = typeof picked.km === "number" && picked.km > 0 ? ` · ${formatDistance(picked.km * 1000, t)}` : "";
    return `${time}${distance} ${t("options.fromPrevious")}`;
  }
  const leg = item.transfer;
  if (!leg || !Number.isFinite(leg.minutes) || leg.minutes <= 0) return "";
  const walking = leg.mode === "walk" || leg.mode === "walking";
  const time = walking
    ? t("options.walk", { minutes: Math.round(leg.minutes) })
    : t("options.travel", { minutes: Math.round(leg.minutes), mode: leg.mode });
  const distance =
    typeof leg.distance_km === "number" && leg.distance_km > 0 ? ` · ${formatDistance(leg.distance_km * 1000, t)}` : "";
  return `${time}${distance} ${t("options.fromPrevious")}`;
}

type PlaceFactFields = Pick<PlanAlternative, "rating" | "review_count" | "price_level" | "cuisine" | "reservable">;

/** The source's facts about a place: rating, reviews, price, cuisine, reservations. Each is hidden when the source gave none. */
export function PlaceFacts({ place }: { place: Partial<PlaceFactFields> }) {
  const { t } = useI18n();
  const parts: string[] = [];
  if (typeof place.rating === "number") {
    const reviews =
      typeof place.review_count === "number"
        ? ` ${t("options.reviews", { count: place.review_count.toLocaleString() })}`
        : "";
    parts.push(`\u2605 ${place.rating.toFixed(1)}${reviews}`);
  }
  if (place.price_level) parts.push(place.price_level);
  if (place.cuisine) parts.push(place.cuisine);
  // Reservations are shown only when the source said yes; unknown and "no" show nothing.
  if (place.reservable === true) parts.push(t("options.reservable"));
  if (!parts.length) return null;
  return <span className="pf-item__facts">{parts.join(" · ")}</span>;
}

export interface StopOffer {
  messageId: string;
  changeId: string;
  kind: "add" | "drop" | "time";
  /** 1-based day of an add or a retimed stop. */
  day: number;
  itemId: string;
  item?: PlanItem;
}

export function ItineraryItem({
  item,
  selected = false,
  updated = false,
  clash = false,
  onSelect,
  number,
  showSource = true,
  proposal = null,
  proposedStart,
  proposedEnd,
  decision,
}: {
  item: PlanItem;
  selected?: boolean;
  updated?: boolean;
  /** Shares its time with another stop. The plan is still shown. */
  clash?: boolean;
  onSelect?: () => void;
  number?: number;
  showSource?: boolean;
  proposal?: "add" | "drop" | "time" | null;
  proposedStart?: string;
  proposedEnd?: string;
  decision?: ReactNode;
}) {
  const { t } = useI18n();
  const picked = choiceInto(useRouteChoices()?.choices ?? {}, item.id);
  const transferText = transferLine(item, t, picked);
  const pending = isPendingChoice(item.notes);
  const start = proposedStart || item.start;
  const end = proposedEnd || item.end;
  return (
    <div
      className="pf-item"
      data-type={item.type}
      data-status={item.data_status}
      data-selected={selected}
      data-updated={updated}
      data-clash={clash}
      data-proposal={proposal || undefined}
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
      {decision}
      <div className="pf-item__time">
        {start}
        <br />
        {end}
      </div>
      <span className="pf-item__icon">
        <Icon name={ITEM_ICON[item.type] ?? "map"} size={15} />
      </span>
      <div className="pf-item__main">
        {item.meal_slot ? <span className="pf-item__slot">{t(MEAL_SLOT_KEY[item.meal_slot])}</span> : null}
        <span className="pf-item__name">
          {number ? <span className="pf-mono pf-muted">{number}. </span> : null}
          {item.name}
        </span>
        {proposal === "time" && proposedStart ? (
          <span className="pf-item__notes">was {item.start}–{item.end}</span>
        ) : null}
        <PlaceImage image={item.image} />
        <PlaceFacts place={item} />
        {transferText ? <span className="pf-item__facts">{transferText}</span> : null}
        {item.notes && !pending ? <span className="pf-item__notes">{item.notes}</span> : null}
        {item.hours_status ? (
          <span className="pf-item__notes" data-hours={item.hours_status}>
            {item.hours_status === "stated" && item.opening_hours?.length
              ? t("item.hours", { hours: item.opening_hours.join("; ") })
              : t("item.hoursUnknown")}
          </span>
        ) : null}
        <span className="pf-item__meta">
          {/* The pill labels the place record: with no stated hours it must not read as complete. */}
          <StatusPill status={item.data_status as DataStatus} label={placeStatusLabel(item, t)} />
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
        <LinkRow links={item.links} />
      </div>
      <div className="pf-item__actions">
        {item.locked ? <Icon name="lock" size={13} /> : null}
        <Icon name="chevronRight" size={14} />
      </div>
    </div>
  );
}

/** The reason a failed request gives, for showing under the button that sent it. */
export function failureReason(cause: unknown): string {
  return cause instanceof Error && cause.message ? cause.message : String(cause);
}

/**
 * The other source-backed choices for one stop, behind an "N other options" disclosure. They are backups for when the
 * default is closed or full; "Use this" swaps the stop through the plan's choose endpoint (no model call).
 */
export function ItemAlternatives({
  item,
  onChoose,
  defaultOpen = false,
}: {
  item: PlanItem;
  onChoose?: (alternativeId: string) => Promise<void>;
  defaultOpen?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; reason: string } | null>(null);
  const listId = useId();
  const alternatives = item.alternatives ?? [];
  if (!alternatives.length) return null;

  const choose = async (alternativeId: string) => {
    if (!onChoose) return;
    setPending(alternativeId);
    setError(null);
    try {
      await onChoose(alternativeId);
    } catch (cause) {
      setError({ id: alternativeId, reason: failureReason(cause) });
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="pf-alts">
      <button
        type="button"
        className="pf-alts__toggle"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen(!open)}
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {alternatives.length === 1 ? t("options.otherOne") : t("options.other", { count: alternatives.length })}
      </button>
      {open ? (
        <div className="pf-alts__body" id={listId}>
          <span className="pf-muted pf-small">{t("options.backupHint")}</span>
          {alternatives.map((alternative) => (
            <div className="pf-alt-card" key={alternative.id}>
              <div className="pf-stack pf-grow" style={{ gap: 2 }}>
                <Text size="sm" weight="semibold">{alternative.name}</Text>
                <PlaceImage image={alternative.image} />
                <RankNote rank={alternative.rank} reasons={alternative.reasons} />
                <PlaceFacts place={alternative} />
                <LinkRow links={alternative.links} />
                {typeof alternative.distance_m === "number" ? (
                  <span className="pf-item__facts">
                    {t("options.fromDefault", { distance: formatDistance(alternative.distance_m, t) })}
                  </span>
                ) : null}
                {error?.id === alternative.id ? (
                  <span className="pf-field__error" role="alert">{t("options.failed", { reason: error.reason })}</span>
                ) : null}
              </div>
              {onChoose ? (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={pending === alternative.id}
                  disabled={pending !== null && pending !== alternative.id}
                  onClick={() => void choose(alternative.id)}
                >
                  {t("options.useThis")}
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
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

function spansOverlap(start: string, end: string, otherStart: string, otherEnd: string): boolean {
  return start < otherEnd && otherStart < end;
}

function spanMinutes(start: string, end: string): number {
  const to = (value: string) => {
    const [hour, minute] = value.split(":");
    return Number(hour) * 60 + Number(minute);
  };
  return to(end) - to(start);
}

export function DaySection({
  day,
  index,
  selectedItemId,
  updatedIds,
  clashIds,
  onSelectItem,
  innerRef,
  onChooseAlternative,
  offers = [],
  onDecide,
}: {
  day: DayPlan;
  index: number;
  selectedItemId?: string | null;
  updatedIds?: Set<string>;
  clashIds?: Set<string>;
  onSelectItem?: (item: PlanItem) => void;
  innerRef?: (node: HTMLElement | null) => void;
  /** Swaps a stop for one of its alternatives; resolves when the updated plan is shown, rejects with the reason. */
  onChooseAlternative?: (item: PlanItem, alternativeId: string) => Promise<void>;
  /** Changes waiting for a tick. Adds are drawn in; removals and new clocks tint the stop. */
  offers?: StopOffer[];
  onDecide?: (messageId: string, changeId: string, accept: boolean) => void;
}) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState<string | null>(null);
  const dayLabel = t("day.number", { n: index + 1 });
  const dayNumber = index + 1;
  const choose = (messageId: string, changeId: string, accept: boolean) => {
    if (!onDecide || waiting) return;
    setWaiting(changeId);
    Promise.resolve(onDecide(messageId, changeId, accept)).finally(() => setWaiting(null));
  };
  const rows: { key: string; item: PlanItem; proposal: "add" | "drop" | "time" | null; offer?: StopOffer; start: string; end: string }[] = day.items.map((item) => {
    const drop = offers.find((offer) => offer.kind === "drop" && offer.itemId === item.id);
    const time = offers.find((offer) => offer.kind === "time" && offer.itemId === item.id);
    const offer = drop ?? time;
    return {
      key: item.id,
      item,
      proposal: offer?.kind ?? null,
      offer,
      start: time?.item?.start || item.start,
      end: time?.item?.end || item.end,
    };
  });
  for (const offer of offers) {
    if (offer.kind !== "add" || offer.day !== dayNumber || !offer.item) continue;
    if (rows.some((row) => row.item.id === offer.itemId)) continue;
    rows.push({
      key: offer.changeId + offer.itemId,
      item: offer.item,
      proposal: "add",
      offer,
      start: offer.item.start,
      end: offer.item.end,
    });
  }
  rows.sort((left, right) => left.start.localeCompare(right.start) || left.end.localeCompare(right.end));
  const lines: { main: (typeof rows)[number]; aside: (typeof rows)[number][] }[] = [];
  const rank = (row: (typeof rows)[number]) =>
    spanMinutes(row.start, row.end) + (row.proposal === "add" || row.proposal === "time" ? 1 : 0);
  for (const row of rows) {
    const line = lines.find((entry) =>
      [entry.main, ...entry.aside].some((other) => spansOverlap(other.start, other.end, row.start, row.end)),
    );
    if (!line) {
      lines.push({ main: row, aside: [] });
      continue;
    }
    const members = [...[line.main, ...line.aside, row]].sort((left, right) => rank(right) - rank(left));
    line.main = members[0];
    line.aside = members.slice(1);
  }
  const card = (row: (typeof rows)[number], number?: number) => (
    <div key={row.key} className="pf-item-group">
      <ItineraryItem
        item={row.item}
        number={number}
        selected={selectedItemId === row.item.id}
        updated={updatedIds?.has(row.item.id) ?? false}
        clash={clashIds?.has(row.item.id) ?? false}
        proposal={row.proposal}
        proposedStart={row.proposal === "time" ? row.start : undefined}
        proposedEnd={row.proposal === "time" ? row.end : undefined}
        onSelect={row.proposal === "add" ? undefined : () => onSelectItem?.(row.item)}
        decision={decide(row)}
      />
      {row.proposal !== "add" && row.item.alternatives?.length ? (
        <ItemAlternatives
          item={row.item}
          onChoose={onChooseAlternative ? (alternativeId) => onChooseAlternative(row.item, alternativeId) : undefined}
        />
      ) : null}
    </div>
  );
  const decide = (row: (typeof rows)[number]) => row.offer && onDecide ? (
    <div className="pf-item__decide" onClick={(event) => event.stopPropagation()}>
      <IconButton
        icon="check"
        label={t("chat.acceptChange")}
        disabled={waiting !== null}
        onClick={() => choose(row.offer!.messageId, row.offer!.changeId, true)}
      />
      <IconButton
        icon="close"
        label={t("chat.rejectChange")}
        disabled={waiting !== null}
        onClick={() => choose(row.offer!.messageId, row.offer!.changeId, false)}
      />
    </div>
  ) : null;
  const chip = (row: (typeof rows)[number]) => (
    <div
      key={row.key}
      className="pf-offer"
      data-proposal={row.proposal || undefined}
      data-clash={!row.proposal && clashIds?.has(row.item.id) ? "true" : undefined}
      onClick={row.proposal === "add" ? undefined : () => onSelectItem?.(row.item)}
      role="group"
    >
      <span>{row.start}–{row.end}</span>
      <b>{row.item.name}</b>
      {decide(row)}
    </div>
  );
  return (
    <section className="pf-day" ref={innerRef} aria-label={dayLabel}>
      <div className="pf-day__head">
        <div className="pf-stack" style={{ gap: 2 }}>
          <span className="pf-day__date">
            {dayLabel.toUpperCase()} · {new Date(`${day.date}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }).toUpperCase()}
          </span>
          <span className="pf-day__title">{day.title || dayLabel}</span>
          {typeof day.elevation_gain_m === "number" && day.elevation_gain_m > 0 ? (
            <span className="pf-muted">↑ {t("day.climb", { m: Math.round(day.elevation_gain_m) })}</span>
          ) : null}
        </div>
        <WeatherChip weather={day.weather} />
      </div>
      {lines.map((line) => (
        line.aside.length ? (
          <div key={line.main.key} className="pf-item-line">
            <div className="pf-item-line__main">{card(line.main)}</div>
            <div className="pf-item-line__aside">{line.aside.map((row) => chip(row))}</div>
          </div>
        ) : card(line.main, rows.indexOf(line.main) + 1)
      ))}
      {!lines.length ? (
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
  onOptimize,
}: {
  cost: CostBreakdown;
  onOptimize?: () => void;
}) {
  const { t } = useI18n();
  const ratio = cost.budget && cost.budget > 0 ? Math.min(100, Math.round((cost.total / cost.budget) * 100)) : 42;
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
      {onOptimize ? (
        <Button variant="ink" size="sm" block onClick={onOptimize} iconLeft="sparkles">
          {t("budget.optimise")}
        </Button>
      ) : null}
    </div>
  );
}

/** Turns the trip total into another currency. It does not change the plan. */
export function ExchangePanel({ cost, fx }: { cost: CostBreakdown; fx: FxSnapshot }) {
  const { t } = useI18n();
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const rates = fx.rates ?? {};
  const conversionCurrencies = currencyCodes(fx, cost.currency);
  const fromRate = rates[cost.currency] ?? 1;
  const toRate = rates[displayCurrency];
  const known = typeof toRate === "number" && toRate > 0;
  const converted = known && fromRate > 0 ? (cost.total * toRate) / fromRate : null;
  return (
    <div className="pf-exchange">
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
          <Input
            aria-label={t("budget.displayCurrency")}
            list="pf-fx-currency-list"
            value={displayCurrency}
            placeholder={t("form.currencySearch")}
            onChange={(event) => setDisplayCurrency(event.target.value.toUpperCase().slice(0, 3))}
          />
          <datalist id="pf-fx-currency-list">
            {conversionCurrencies.map((code) => (
              <option key={code} value={code} label={currencyOptionLabel(code)} />
            ))}
          </datalist>
          <strong>
            {known && converted !== null
              ? `${displayCurrency} ${Math.round(converted).toLocaleString()}`
              : t("budget.chooseCurrency")}
          </strong>
        </div>
        <span className="pf-fx__note">
          {t("budget.base", { base: fx.base })} · {fx.as_of} · {fx.note}
        </span>
      </div>
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
        <StatusPill status={item.data_status} label={placeStatusLabel(item, t)} />
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
  finalized = false,
  verdict,
  stars = null,
  added = [],
  onFinalise,
  onUseful,
  onNotUseful,
}: {
  finalized?: boolean;
  verdict: "useful" | "not_useful" | null;
  stars?: number | null;
  added?: string[];
  onFinalise?: () => void;
  onUseful: (stars?: number) => void;
  onNotUseful: () => void;
}) {
  const { t } = useI18n();
  if (!finalized) {
    return (
      <div className="pf-plan-feedback">
        <button type="button" className="pf-plan-feedback__button pf-plan-feedback__finalise" onClick={onFinalise}>
          {t("feedback.finalise")}
        </button>
      </div>
    );
  }
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
          <span className="pf-day__date">{t("feedback.question")}</span>
          <button type="button" className="pf-plan-feedback__button" onClick={() => onUseful()}>
            {t("saved.useful")}
          </button>
          <button type="button" className="pf-plan-feedback__button" onClick={onNotUseful}>
            {t("feedback.notUseful")}
          </button>
          <span className="pf-row">
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                key={value}
                type="button"
                className="pf-plan-feedback__button"
                aria-label={t("feedback.stars", { count: value })}
                onClick={() => onUseful(value)}
              >
                <Icon name="star" size={12} />
                {value}
              </button>
            ))}
          </span>
        </>
      )}
      {verdict === "useful" && stars ? (
        <span className="pf-plan-feedback__added">{t("feedback.saved", { stars })}</span>
      ) : null}
      {verdict === "useful" && added.length ? (
        <span className="pf-plan-feedback__added">+ {added.slice(0, 3).join(", ")}</span>
      ) : null}
      {verdict ? <span className="pf-muted">{t("feedback.frozen")}</span> : null}
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

const ASSESSMENT_LEVEL: Record<PlanAssessment["level"], { label: MessageKey; tone: "success" | "warning" | "danger" }> = {
  high: { label: "assessment.high", tone: "success" },
  medium: { label: "assessment.medium", tone: "warning" },
  low: { label: "assessment.low", tone: "danger" },
};

function AssessmentList({ title, lines }: { title: string; lines: string[] }) {
  if (!lines.length) return null;
  return (
    <div className="pf-stack" style={{ gap: 2 }}>
      <Text size="xs" weight="semibold">{title}</Text>
      <ul className="pf-assessment__list">
        {lines.map((line, index) => (
          <li key={`${index}-${line}`}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * How sure the planner is of the plan: the level, what further research changed, what no source confirmed and what
 * only the traveller can say. A plan it is sure of, with nothing to tell, is one line; no assessment is nothing.
 */
export function PlanAssessmentCard({
  assessment,
  onAnswer,
}: {
  assessment: PlanAssessment | null | undefined;
  /** Takes the traveller to the chat box to answer the question. */
  onAnswer?: () => void;
}) {
  const { t } = useI18n();
  if (!assessment) return null;
  const level = ASSESSMENT_LEVEL[assessment.level] ?? ASSESSMENT_LEVEL.medium;
  const percent = Math.round(Math.max(0, Math.min(1, assessment.confidence)) * 100);
  const improvements = assessment.improvements ?? [];
  const unverified = assessment.unverified ?? [];
  const missing = assessment.missing ?? [];
  const question = (assessment.question ?? "").trim();
  return (
    <div className="pf-assessment" data-level={assessment.level}>
      <div className="pf-row pf-row--wrap" style={{ gap: 7 }}>
        <Icon name="shield" size={14} />
        <Text size="sm" weight="semibold">{t("assessment.title")}</Text>
        <Badge tone={level.tone} dot>
          {t(level.label)} · {percent}%
        </Badge>
      </div>
      <AssessmentList title={t("assessment.improvements")} lines={improvements} />
      <AssessmentList title={t("assessment.unverified")} lines={unverified} />
      <AssessmentList title={t("assessment.missing")} lines={missing} />
      {question ? (
        <div className="pf-assessment__question">
          <Text size="sm">{question}</Text>
          {onAnswer ? (
            <Button size="sm" variant="primary" onClick={onAnswer}>
              {t("assessment.answer")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** What a turn read from the links and files of its message: each source, the trip details filled in and the places found. */
export function IntakeNotice({ intake }: { intake: IntakePayload }) {
  const { t } = useI18n();
  const sources = intake.sources ?? [];
  const filled = intake.filled ?? [];
  const places = intake.places ?? [];
  if (!sources.length && !filled.length && !places.length) return null;
  return (
    <div className="pf-intake" role="status">
      {sources.map((source, index) => {
        const failed = source.status === "failed";
        return (
          <div className="pf-intake__row" data-failed={failed} key={`${index}-${source.name}`}>
            <Icon name={failed ? "alert" : "check"} size={13} />
            <span>
              {failed
                ? source.note
                  ? t("intake.failedNote", { name: source.name, note: source.note })
                  : t("intake.failed", { name: source.name })
                : t("intake.read", { name: source.name })}
            </span>
          </div>
        );
      })}
      {filled.length ? (
        <div className="pf-intake__row">
          <Icon name="edit" size={13} />
          <span>{t("intake.filled", { fields: filled.map((field) => field.replaceAll("_", " ")).join(", ") })}</span>
        </div>
      ) : null}
      {places.length ? (
        <div className="pf-intake__row">
          <Icon name="map" size={13} />
          <span>{t("intake.places", { places: places.join(", ") })}</span>
        </div>
      ) : null}
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
/** True when the card shows "Hours unknown": the source gave no hours for the visit day. */
function hoursMissing(item: PlanItem): boolean {
  return Boolean(item.hours_status) && !(item.hours_status === "stated" && item.opening_hours?.length);
}

/**
 * The freshness pill labels the place record. When the record has no hours it
 * must not read as complete next to "Hours unknown", so it says what is missing.
 */
function placeStatusLabel(item: PlanItem, t: (key: MessageKey) => string): string | undefined {
  return item.data_status === "ok" && hoursMissing(item) ? t("status.okNoHours") : undefined;
}

export type PlanLanguageNoticeKind ="translating" | "fallback" | "unavailable" | "failed";

const PLAN_LANGUAGE_NOTICE: Record<PlanLanguageNoticeKind, MessageKey> = {
  translating: "planLang.translating",
  fallback: "planLang.fallback",
  unavailable: "planLang.unavailable",
  failed: "planLang.failed",
};

/**
 * Shown on the plan whenever it is viewed in a language other than English:
 * the AI-translation disclaimer, and, when the English original is shown
 * instead, a notice saying why.
 */
export function BannerClose({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  return (
    <button type="button" className="pf-banner__close" aria-label={t("banner.close")} onClick={onClose}>
      <Icon name="close" size={12} />
    </button>
  );
}

export function PlanLanguageBanner({
  disclaimer,
  notice = null,
  reason = "",
}: {
  /** The translation's own disclaimer; the fixed text is used when empty. */
  disclaimer?: string;
  notice?: PlanLanguageNoticeKind | null;
  reason?: string;
}) {
  const { t } = useI18n();
  const [hideDisclaimer, setHideDisclaimer] = useState(false);
  const [hideNotice, setHideNotice] = useState(false);
  const showDisclaimer = !hideDisclaimer;
  const showNotice = Boolean(notice) && !hideNotice;
  if (!showDisclaimer && !showNotice) return null;
  return (
    <div className="pf-plan-language" role="note" aria-label={t("planLang.label")}>
      {showDisclaimer ? (
        <div className="pf-banner" data-tone="info">
          <Icon name="globe" size={14} />
          <span>{disclaimer?.trim() || t("planLang.disclaimer")}</span>
          <BannerClose onClose={() => setHideDisclaimer(true)} />
        </div>
      ) : null}
      {showNotice && notice ? (
        <div className="pf-banner" data-tone={notice === "translating" ? "info" : "warning"} role="status">
          <Icon name={notice === "translating" ? "info" : "alert"} size={14} />
          <span>
            {t(PLAN_LANGUAGE_NOTICE[notice])}
            {reason && notice !== "translating" ? ` ${t("planLang.reason", { reason })}` : ""}
          </span>
          <BannerClose onClose={() => setHideNotice(true)} />
        </div>
      ) : null}
    </div>
  );
}

export function StatusSummary({
  plan,
  conflicts = 0,
  onOpen,
}: {
  plan: { constraints: ConstraintCheck[]; data_quality: { gaps: DataGap[] } };
  /** Shared clocks and other recorded warnings, on top of failing checks. */
  conflicts?: number;
  onOpen?: (which: "constraints" | "gaps") => void;
}) {
  const { t } = useI18n();
  const failing = plan.constraints.filter((check) => check.status === "fail").length + conflicts;
  const gaps = plan.data_quality.gaps.length;
  return (
    <div className="pf-row pf-row--wrap">
      <Badge tone={failing ? "danger" : "success"} dot onClick={onOpen ? () => onOpen("constraints") : undefined}>
        {failing
          ? t(failing > 1 ? "summary.failing" : "summary.failingOne", { count: failing })
          : t("summary.hold")}
      </Badge>
      <Badge tone={gaps ? "warning" : "neutral"} dot onClick={onOpen ? () => onOpen("gaps") : undefined}>
        {gaps ? t(gaps > 1 ? "summary.gaps" : "summary.gapsOne", { count: gaps }) : t("summary.noGaps")}
      </Badge>
    </div>
  );
}
