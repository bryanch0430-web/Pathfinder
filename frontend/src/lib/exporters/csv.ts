/* The budget ledger as CSV (RFC 4180), opening cleanly in Excel, Numbers and Google Sheets. */
import type { TripBudget } from '../../services/tripService';

/** One cell. Text that a spreadsheet would run as a formula is prefixed with ' (CSV injection). */
export function csvCell(value: string | number | null | undefined, text = true): string {
  let cell = value === null || value === undefined ? '' : String(value);
  if (text && /^[=+\-@\t\r]/.test(cell)) cell = `'${cell}`;
  return /[",\r\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell;
}

const HEADER = ['Category', 'Item', 'Place', 'Quantity', 'Unit amount', 'Total', 'Currency', 'Counted', 'Suggested unit amount', 'Note'];

export function budgetCsv(budget: TripBudget): string {
  const rows = budget.items.map((item) => {
    const total = item.unit_amount === null ? null : (Number(item.quantity) * Number(item.unit_amount)).toFixed(2);
    return [csvCell(item.category), csvCell(item.label), csvCell(item.place_name), csvCell(item.quantity, false),
      csvCell(item.unit_amount, false), csvCell(total, false), csvCell(budget.currency), csvCell(item.is_included ? 'yes' : 'no'),
      csvCell(item.estimate_amount, false), csvCell(item.estimate_note || item.quote_text)].join(',');
  });
  const totals = [['Total entered', budget.priced_total], ['Total with suggestions', budget.projected_total],
    ...(budget.target_amount ? [['Budget', budget.target_amount]] : [])]
    .map(([label, amount]) => ['', csvCell(label), '', '', '', csvCell(amount, false), csvCell(budget.currency), '', '', ''].join(','));
  // The BOM makes Excel read the file as UTF-8, so ¥, ₹ and Japanese names survive.
  return '﻿' + [HEADER.join(','), ...rows, ...totals].join('\r\n') + '\r\n';
}
