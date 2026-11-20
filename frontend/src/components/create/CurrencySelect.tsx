import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { CURRENCIES } from '../../lib/currencies';

interface Props {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  label: string;
  hint?: string;
}

/** Search by code or name, then pick a currency. The value stored is the 3-letter code. */
export const CurrencySelect: React.FC<Props> = ({ value, onChange, disabled, label, hint }) => {
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const selected = CURRENCIES.find((item) => item.code === value);
  const shown = open ? query : selected ? `${selected.code} — ${selected.name}` : value;

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return CURRENCIES;
    return CURRENCIES.filter((item) => item.code.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle));
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const choose = (code: string) => {
    onChange(code);
    setQuery('');
    setOpen(false);
  };

  return (
    <div className="tv-currency" ref={root}>
      <span>{label}</span>
      <input
        className="tv-input"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        disabled={disabled}
        value={shown}
        onFocus={() => { setOpen(true); setQuery(''); setActive(0); }}
        onClick={() => { if (!open) { setQuery(''); setActive(0); } setOpen(true); }}
        onBlur={() => setOpen(false)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0); }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
            setActive((index) => Math.min(index + 1, Math.max(matches.length - 1, 0)));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive((index) => Math.max(index - 1, 0));
          } else if (event.key === 'Enter' && open && matches[active]) {
            event.preventDefault();
            choose(matches[active].code);
          } else if (event.key === 'Escape') {
            setOpen(false);
            setQuery('');
          }
        }}
      />
      {open && !disabled && (
        <ul className="tv-currency__list" id={listId} role="listbox">
          {matches.length ? matches.map((item, index) => (
            <li key={item.code} role="option" aria-selected={item.code === value}>
              <button type="button" className={index === active ? 'is-active' : ''} onMouseDown={(event) => { event.preventDefault(); choose(item.code); }}>
                <strong>{item.code}</strong> {item.name}
              </button>
            </li>
          )) : <li className="tv-meta">No currency matches.</li>}
        </ul>
      )}
      {hint && <small>{hint}</small>}
    </div>
  );
};
