import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  acceptBudgetEstimates, addBudgetItem, deleteBudgetItem, estimateBudget, getTripBudget, lockTripBudget, unlockTripBudget, updateBudgetItem,
  updateBudgetSettings,
  type BudgetCategory, type BudgetCurrency, type BudgetItem, type BudgetItemInput, type TripBudget,
} from '../../services/tripService';
import type { ItineraryGraph } from './itineraryGraph';
import '../../styles/budget-workspace.css';

const categories: { value: BudgetCategory; label: string }[] = [
  { value: 'travel', label: 'Travel' }, { value: 'stay', label: 'Stay' },
  { value: 'food', label: 'Food' }, { value: 'activities', label: 'Activities' },
  { value: 'other', label: 'Other' },
];
const currencies: BudgetCurrency[] = ['INR', 'JPY', 'USD', 'EUR', 'GBP', 'HKD', 'AUD', 'CAD'];

function money(amount: string | number, currency: BudgetCurrency): string {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount));
}

function itemTotal(item: BudgetItem): number | null {
  return item.unit_amount === null ? null : Number(item.unit_amount) * Number(item.quantity);
}

/** Stay rows count nights and per-place food/transport rows count days, so show the unit. */
function quantityLabel(item: BudgetItem): string {
  const count = Number(item.quantity);
  const unit = item.category === 'stay' && item.scope === 'stop' ? 'night'
    : item.scope === 'stop' && (item.category === 'food' || item.category === 'travel') ? 'day' : '';
  return unit ? `${count} ${unit}${count === 1 ? '' : 's'}` : String(count);
}

function ItemEditor({ item, onSave, onCancel, onDelete, saving }: {
  item?: BudgetItem;
  onSave: (value: BudgetItemInput) => Promise<boolean>;
  onCancel: () => void;
  onDelete?: () => void;
  saving: boolean;
}) {
  const [label, setLabel] = useState(item?.label || '');
  const [category, setCategory] = useState<BudgetCategory>(item?.category || 'other');
  const [place, setPlace] = useState(item?.place_name || '');
  const [quantity, setQuantity] = useState(item?.quantity || '1');
  const [amount, setAmount] = useState(item?.unit_amount || '');
  const [included, setIncluded] = useState(item?.is_included ?? true);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (await onSave({ label: label.trim(), category, place_name: place.trim() || null,
      quantity, unit_amount: amount.trim() || null, is_included: included })) onCancel();
  };
  return <form className="tv-budget__editor" onSubmit={(event) => { void submit(event); }}>
    <label>Expense<input required maxLength={255} value={label} onChange={(event) => setLabel(event.target.value)} placeholder="e.g. Museum tickets" /></label>
    <div className="tv-budget__editor-grid">
      <label>Category<select value={category} onChange={(event) => setCategory(event.target.value as BudgetCategory)}>{categories.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}</select></label>
      <label>Quantity<input required type="number" min="0.01" max="1000" step="0.01" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
      <label>Amount each<input type="number" min="0" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="Unknown" /></label>
    </div>
    {!item?.source_key && <label>Place or route, optional<input maxLength={255} value={place} onChange={(event) => setPlace(event.target.value)} placeholder="e.g. Kyoto" /></label>}
    <label className="tv-budget__check"><input type="checkbox" checked={included} onChange={(event) => setIncluded(event.target.checked)} /> Include in total</label>
    <div className="tv-budget__editor-actions">
      <button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save cost'}</button>
      <button type="button" disabled={saving} onClick={onCancel}>Cancel</button>
      {onDelete && <button type="button" className="is-danger" disabled={saving} onClick={onDelete}>Delete</button>}
    </div>
  </form>;
}

function BudgetLine({ item, currency, editing, saving, locked, onEdit, onCancel, onSave, onDelete }: {
  item: BudgetItem; currency: BudgetCurrency; editing: boolean; saving: boolean; locked: boolean;
  onEdit: () => void; onCancel: () => void;
  onSave: (value: BudgetItemInput) => Promise<boolean>;
  onDelete: () => void;
}) {
  const total = itemTotal(item);
  const suggestion = item.unit_amount === null && item.estimate_amount !== null ? Number(item.estimate_amount) : null;
  return <div className={`tv-budget__line ${!item.is_included ? 'is-excluded' : ''}`}>
    {editing ? <ItemEditor item={item} saving={saving} onSave={onSave} onCancel={onCancel}
      onDelete={!item.source_key ? onDelete : undefined} /> : <>
      <div className="tv-budget__line-copy">
        <strong>{item.label}</strong>
        <small>{categories.find((entry) => entry.value === item.category)?.label} · {quantityLabel(item)} × {item.unit_amount === null ? 'amount missing' : money(item.unit_amount, currency)}{!item.is_included ? ' · excluded' : ''}</small>
        {suggestion !== null && <small className="tv-budget__estimate-note">Suggested {money(suggestion, currency)} each{item.estimate_note ? ` · ${item.estimate_note}` : ''}</small>}
        {item.quote_text && <small className="tv-budget__quote">Draft mentions “{item.quote_text}” · not counted until you enter an amount</small>}
      </div>
      <span className={`tv-budget__line-total ${suggestion !== null ? 'is-estimate' : ''}`}>
        {total !== null ? money(total, currency) : suggestion !== null ? `~${money(suggestion * Number(item.quantity), currency)}` : 'No amount'}
      </span>
      {!locked && <div className="tv-budget__line-actions">
        {suggestion !== null && <button type="button" disabled={saving} aria-label={`Use suggested amount for ${item.label}`}
          onClick={() => { void onSave({ label: item.label, category: item.category, place_name: item.place_name,
            quantity: item.quantity, unit_amount: item.estimate_amount, is_included: item.is_included }); }}>Use</button>}
        <button type="button" onClick={onEdit} aria-label={`Edit ${item.label}`}>Edit</button>
      </div>}
    </>}
  </div>;
}

interface Props { tripId: string; destination?: string | null; graph?: ItineraryGraph | null; refreshVersion: number; finalTrip?: boolean;
  onClose: () => void; onChange?: (budget: TripBudget) => void }

export function BudgetWorkspace({ tripId, destination, graph, refreshVersion, finalTrip = false, onClose, onChange }: Props) {
  const [budget, setBudget] = useState<TripBudget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [target, setTarget] = useState('');
  const [currency, setCurrency] = useState<BudgetCurrency>('INR');
  const [manualFormVersion, setManualFormVersion] = useState(0);
  const [estimating, setEstimating] = useState(false);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    void getTripBudget(tripId).then((data) => {
      if (alive) { setBudget(data); setTarget(data.target_amount || ''); setCurrency(data.currency); setError(null); }
    }).catch((cause: Error) => { if (alive) setError(cause.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tripId, refreshVersion]);

  const mutate = async (operation: () => Promise<TripBudget>): Promise<boolean> => {
    setSaving(true);
    setError(null);
    try { const updated = await operation(); setBudget(updated); onChange?.(updated); return true; }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Budget update failed. Try again.'); return false; }
    finally { setSaving(false); }
  };
  const active = budget?.items.filter((item) => item.is_current) || [];
  const readOnly = finalTrip || Boolean(budget?.is_locked);
  const stops = useMemo(() => {
    const groups: Record<string, BudgetItem[]> = {};
    active.filter((item) => item.scope === 'stop').forEach((item) => {
      const name = item.place_name || 'Destination';
      (groups[name] ||= []).push(item);
    });
    const order = new Map(graph?.nodes.map((node, index) => [node.name, index]) || []);
    return Object.entries(groups).sort((a, b) => (order.get(a[0]) ?? 999) - (order.get(b[0]) ?? 999));
  }, [budget, graph]);
  const legOrder = graph?.edges.map((edge) => `Travel ${graph.nodes.find((node) => node.id === edge.source)?.name} → ${graph.nodes.find((node) => node.id === edge.target)?.name}`) || [];
  const legs = active.filter((item) => item.scope === 'leg').sort((a, b) => legOrder.indexOf(a.label) - legOrder.indexOf(b.label));
  const manual = active.filter((item) => item.scope === 'manual');
  const review = budget?.items.filter((item) => !item.is_current) || [];
  const hasEnteredAmounts = budget?.items.some((item) => item.unit_amount !== null) || false;
  const total = Number(budget?.priced_total || 0);
  const projected = Number(budget?.projected_total || 0);
  const pendingSuggestions = active.filter((item) => item.unit_amount === null && item.estimate_amount !== null).length;
  const canEstimate = active.some((item) => item.scope !== 'manual' && item.unit_amount === null && item.estimate_amount === null);
  const suggest = async () => {
    setEstimating(true);
    await mutate(() => estimateBudget(tripId));
    setEstimating(false);
  };
  const targetNumber = Number(budget?.target_amount || 0);
  const progress = targetNumber > 0 ? Math.min(100, total / targetNumber * 100) : 0;
  const distribution = categories.map((entry) => ({ ...entry, amount: Number(budget?.category_totals[entry.value] || 0) }))
    .filter((entry) => entry.amount > 0);
  const largestCategory = [...distribution].sort((a, b) => b.amount - a.amount)[0]?.value;
  const budgetSuggestion = largestCategory === 'travel' ? 'Travel is your largest entered cost. Review extra transfers and local transport before adding more stops.'
    : largestCategory === 'stay' ? 'Stays are your largest entered cost. Review nights and room choices in each destination.'
      : largestCategory === 'activities' ? 'Activities are your largest entered cost. Mark optional paid visits so you can choose which matter most.'
        : largestCategory === 'food' ? 'Food is your largest entered cost. Check whether daily meal assumptions fit your plans.'
          : 'Review your largest entered costs first; every edit updates the total immediately.';
  const renderLine = (item: BudgetItem) => <BudgetLine key={item.id} item={item} currency={budget!.currency}
    editing={!readOnly && editingId === item.id} saving={saving} locked={readOnly}
    onEdit={() => setEditingId(item.id)} onCancel={() => setEditingId(null)}
    onSave={(value) => mutate(() => updateBudgetItem(tripId, item.id, value))}
    onDelete={() => { if (window.confirm(`Delete “${item.label}” from this budget?`)) {
      void mutate(() => deleteBudgetItem(tripId, item.id)).then((done) => { if (done) setEditingId(null); });
    } }} />;

  return <>
    <button type="button" className="tv-budget__scrim" aria-label="Close budget" onClick={onClose} />
    <aside className="tv-budget" aria-label="Trip budget">
      <header className="tv-budget__bar"><span>TRIP BUDGET</span><button type="button" onClick={onClose} aria-label="Close budget">Close ×</button></header>
      <div className="tv-budget__body">
        <div className="tv-budget__heading"><h2>{destination ? `${destination} budget` : 'Your trip budget'}</h2><p>Plan what you expect to spend, place by place. Numbers you enter are saved with this trip.</p></div>
        <ol className="tv-budget__steps" aria-label="How the budget works">
          <li><strong>Set a target</strong><span>Your total for the whole trip, below. You can also tell the chat: “my budget is 60,000 INR”.</span></li>
          <li><strong>Fill in the costs</strong><span>Every place gets rows for stay, food, activities and travel. Tap <em>Suggest amounts</em> for typical costs, or type your own.</span></li>
          <li><strong>Watch what’s left</strong><span>Amounts you enter or <em>Use</em> count toward the total. Suggestions stay out until you use them.</span></li>
        </ol>
        {error && <div className="tv-budget__error" role="alert">{error}<button type="button" onClick={() => { setError(null); setLoading(true); void getTripBudget(tripId).then((data) => { setBudget(data); setTarget(data.target_amount || ''); setCurrency(data.currency); }).catch((cause: Error) => setError(cause.message)).finally(() => setLoading(false)); }}>Retry</button></div>}
        {loading ? <p className="tv-budget__loading" role="status">Loading budget…</p> : budget && <>
          {readOnly && <div className="tv-budget__locked" role="status"><div><strong>{finalTrip && !budget.is_locked ? 'Budget estimate in final trip' : 'Final budget'} · {money(finalTrip && !budget.is_locked ? budget.projected_total : budget.locked_total || budget.priced_total, budget.currency)}</strong><span>{finalTrip ? 'This trip is finalized, so its costs are read-only.' : 'Costs are read-only. Unlock the budget before changing costs or the trip route.'}</span></div>{!finalTrip && <button type="button" disabled={saving} onClick={() => { void mutate(() => unlockTripBudget(tripId)); }}>Unlock budget</button>}</div>}
          <section className="tv-budget__summary" aria-label="Budget summary">
            {pendingSuggestions > 0 && <div><span>PROJECTED TRIP COST</span><strong>~{money(projected, budget.currency)}</strong><small>Entered amounts plus {pendingSuggestions} suggested{budget.unestimated_count ? ` · ${budget.unestimated_count} still unknown` : ''}</small></div>}
            <div><span>{budget.is_locked ? 'FINAL BUDGET' : 'ENTERED SO FAR'}</span><strong>{money(budget.is_locked ? budget.locked_total || budget.priced_total : budget.priced_total, budget.currency)}</strong><small>{budget.unpriced_count ? `${budget.unpriced_count} costs still missing · total is incomplete` : active.length ? 'All included costs have amounts' : 'No costs entered yet'}</small></div>
            <div><span>{budget.target_amount === null ? 'TARGET' : Number(budget.remaining) < 0 ? 'ABOVE TARGET' : 'LEFT TO TARGET'}</span><strong>{budget.target_amount === null ? 'Set one below' : money(Math.abs(Number(budget.remaining)), budget.currency)}</strong><small>{budget.target_amount === null ? 'A target helps you compare options' : budget.unpriced_count ? 'Provisional while costs are missing' : active.length ? 'Based on entered costs' : 'No costs entered yet'}</small></div>
          </section>
          {budget.target_amount !== null && <div className="tv-budget__meter" role="progressbar" aria-label="Budget used" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${progress}%` }} /></div>}
          {!readOnly && <form className="tv-budget__settings" onSubmit={(event) => { event.preventDefault(); void mutate(() => updateBudgetSettings(tripId, { currency, target_amount: target.trim() || null })); }}>
            <label>Budget target<input type="number" min="0" step="0.01" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="Optional" /></label>
            <label>Currency<select value={currency} disabled={hasEnteredAmounts} onChange={(event) => setCurrency(event.target.value as BudgetCurrency)}>{currencies.map((code) => <option key={code} value={code}>{code}</option>)}</select></label>
            <button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save target'}</button>
          </form>}
          {!readOnly && (canEstimate || pendingSuggestions > 0) && <section className="tv-budget__estimate" aria-label="Suggested amounts">
            <p>{pendingSuggestions > 0
              ? `${pendingSuggestions} suggested ${pendingSuggestions === 1 ? 'amount is' : 'amounts are'} ready. They add about ${money(projected - total, budget.currency)} and stay out of your total until you use them.`
              : 'Not sure what things cost? Pathfinder can suggest typical amounts for every row from traveler reports and your plan.'}</p>
            <div>
              {pendingSuggestions > 0 && <button type="button" className="is-primary" disabled={saving}
                onClick={() => { void mutate(() => acceptBudgetEstimates(tripId)); }}>Use all suggestions</button>}
              {canEstimate && <button type="button" className={pendingSuggestions ? '' : 'is-primary'} disabled={saving}
                onClick={() => { void suggest(); }}>{estimating ? 'Checking traveler reports…' : pendingSuggestions ? 'Suggest the rest' : 'Suggest amounts'}</button>}
            </div>
          </section>}
          {!readOnly && graph && <section className="tv-budget__lock-action" aria-label="Finalize budget"><div><strong>Ready to keep this budget?</strong><span>All included rows need an amount. You can unlock it later while the trip is still being planned.</span></div><div><button type="button" disabled={saving || !active.some((item) => item.is_included)} onClick={() => { void mutate(() => lockTripBudget(tripId)); }}>Lock this budget</button>{pendingSuggestions > 0 && <button type="button" disabled={saving} onClick={() => { void mutate(() => lockTripBudget(tripId, true)); }}>Use suggestions & lock</button>}</div></section>}
          {hasEnteredAmounts && <p className="tv-budget__note">Currency is locked while amounts are entered. No exchange conversion happens behind the scenes.</p>}
          {distribution.length > 0 && <section className="tv-budget__distribution"><h3>Where the entered money goes</h3>{distribution.map((entry) => <div key={entry.value} className="tv-budget__distribution-row"><span>{entry.label}</span><div><i style={{ width: `${Math.max(2, entry.amount / total * 100)}%` }} /></div><strong>{money(entry.amount, budget.currency)}</strong></div>)}</section>}
          {total > targetNumber && targetNumber > 0 && <p className="tv-budget__suggestion">Entered costs are above your target. {budgetSuggestion}</p>}
          {total <= targetNumber && projected > targetNumber && targetNumber > 0 && <p className="tv-budget__suggestion">With the suggested amounts, this trip lands about {money(projected - targetNumber, budget.currency)} above your target. Review stays and activities before using them all.</p>}
          <div className="tv-budget__section-head"><h3>Costs by place</h3><span>Enter amounts as you confirm them</span></div>
          {stops.length ? stops.map(([place, items], index) => <details key={place} className="tv-budget__group" open={index === 0}>
            <summary><strong>{place}</strong><span>{items.filter((item) => item.unit_amount !== null).length}/{items.length} priced</span></summary>
            {items.map(renderLine)}
          </details>) : <p className="tv-budget__empty">Destination cost rows will appear when an itinerary is generated. You can set a target or add an expense now.</p>}
          {!!legs.length && <details className="tv-budget__group" open><summary><strong>Travel between places</strong><span>{legs.length} connections</span></summary>{legs.map(renderLine)}</details>}
          {!!manual.length && <details className="tv-budget__group" open><summary><strong>Other expenses</strong><span>{manual.length} items</span></summary>{manual.map(renderLine)}</details>}
          {!readOnly && <><div className="tv-budget__section-head"><h3>Add a cost</h3><span>Tickets, insurance, or anything else</span></div>
          <ItemEditor key={manualFormVersion} saving={saving} onCancel={() => setManualFormVersion((value) => value + 1)} onSave={(value) => mutate(() => addBudgetItem(tripId, value))} /></>}
          {!!review.length && <details className="tv-budget__group tv-budget__review"><summary><strong>Needs review after route change</strong><span>{review.length} no longer counted</span></summary>
            <p>These rows belonged to places or travel legs removed from the latest itinerary. Entered values are kept here for reference and excluded from the total.</p>
            {review.map((item) => <div className="tv-budget__line" key={item.id}><div className="tv-budget__line-copy"><strong>{item.label}</strong><small>{item.unit_amount === null ? 'No amount entered' : `${item.quantity} × ${money(item.unit_amount, budget.currency)}`}</small></div><span className="tv-budget__line-total">{itemTotal(item) === null ? 'Not priced' : money(itemTotal(item)!, budget.currency)}</span></div>)}
          </details>}
          <p className="tv-budget__footnote">This is a planning ledger, not a live price quote. Suggested amounts come from traveler reports and your plan, and are excluded until you use them or enter your own. Every entry uses {budget.currency}; taxes, exchange rates, and booking prices are not fetched.</p>
        </>}
      </div>
    </aside>
  </>;
}
