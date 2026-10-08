/** Small presentation helpers. Pure functions, no API access. */
import type { Money } from '@/api'

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

/** Parse "YYYY-MM-DD" as a UTC date so the calendar day never shifts with the viewer's timezone. */
function parseDateOnly(value: string): Date | null {
  const match = DATE_ONLY.exec(value)
  if (!match) return null
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
}

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

const shortDateFormatter = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** "Sun, 12 Apr 2026" for an ISO date; the input is returned unchanged if unparseable. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = parseDateOnly(value)
  return date ? dateFormatter.format(date) : value
}

/** "12 Apr" for an ISO date. */
export function formatShortDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = parseDateOnly(value)
  return date ? shortDateFormatter.format(date) : value
}

/** Local date and time for an ISO date-time. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateTimeFormatter.format(date)
}

/** "09:30" for "09:30:00". */
export function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  return value.length >= 5 ? value.slice(0, 5) : value
}

/** "09:30 - 11:00", "09:30", or "" depending on which times exist. */
export function formatTimeRange(start: string | null | undefined, end: string | null | undefined): string {
  const from = formatTime(start)
  const to = formatTime(end)
  if (from && to) return `${from} - ${to}`
  return from || to
}

/** Money with the currency's own symbol and precision; falls back for unknown currency codes. */
export function formatMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount)
  } catch {
    return `${amount.toLocaleString()} ${currency}`
  }
}

export function formatMoneyValue(money: Money | null | undefined): string {
  return money ? formatMoney(money.amount, money.currency) : ''
}

/** Whole nights between two ISO dates (never negative). */
export function nightsBetween(checkIn: string, checkOut: string): number {
  const from = parseDateOnly(checkIn)
  const to = parseDateOnly(checkOut)
  if (!from || !to) return 0
  return Math.max(0, Math.round((to.getTime() - from.getTime()) / 86_400_000))
}

/** 0.35 -> "35%". */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

/**
 * Only http(s) links from tool results are rendered as links; anything else (javascript:, data:)
 * is dropped. Returns null when the value should not become an href.
 */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}
