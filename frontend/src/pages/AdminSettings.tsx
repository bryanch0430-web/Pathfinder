/*
 * Choices an administrator can make for the next turn: how a message is handled,
 * which personal details are hidden, and which instruction a graph reads.
 * Keys stay in the server environment file. Router-only controls are not here.
 */
import React, { useEffect, useState } from 'react';
import { apiFetch } from '../services/apiClient';

interface RouterSettings {
  router_threshold: number;
  rules_first: boolean;
  jev_configured: boolean;
  jev_enabled: boolean;
  jev_key_set: boolean;
  router_enabled: boolean;
}

interface PromptRow {
  name: string;
  description: string;
  default: string;
  override: string;
}

interface AccountRow {
  email: string;
  full_name: string | null;
  is_admin: boolean;
}

interface PrivacySettings {
  hkid: boolean;
  hk_phone: boolean;
  email: boolean;
  card: boolean;
  hkid_pattern: string;
  hk_phone_pattern: string;
  email_pattern: string;
  card_pattern: string;
}

const PRIVACY_ROWS: { key: keyof PrivacySettings; pattern: keyof PrivacySettings; title: string; hint: string }[] = [
  { key: 'hkid', pattern: 'hkid_pattern', title: 'Hong Kong identity number', hint: 'A letter and six or seven digits, with or without the check character.' },
  { key: 'hk_phone', pattern: 'hk_phone_pattern', title: 'Hong Kong phone number', hint: 'An 8-digit local number. Overseas numbers stay as written.' },
  { key: 'email', pattern: 'email_pattern', title: 'Email address', hint: 'Hidden before the message is saved and before a model reads it.' },
  { key: 'card', pattern: 'card_pattern', title: 'Payment card', hint: 'Only a number that passes its check digit. The last four stay visible.' },
];

const SAMPLE = 'Plan 4 days in Kyoto. My HKID is Y123456(7). Call 9123 4567 or a@example.com. The card is 4111111111111111.';

export const AdminSettings: React.FC = () => {
  const [settings, setSettings] = useState<RouterSettings | null>(null);
  const [threshold, setThreshold] = useState('0.8');
  const [rulesFirst, setRulesFirst] = useState(true);
  const [gateOn, setGateOn] = useState(false);
  const [jevOn, setJevOn] = useState(false);
  const [prompts, setPrompts] = useState<PromptRow[]>([]);
  const [selected, setSelected] = useState('plan');
  const [draft, setDraft] = useState('');
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [privacy, setPrivacy] = useState<PrivacySettings | null>(null);
  const [sample, setSample] = useState(SAMPLE);
  const [preview, setPreview] = useState('');
  const [patternsOpen, setPatternsOpen] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    const [settingsRes, promptsRes, usersRes, privacyRes] = await Promise.all([
      apiFetch<RouterSettings>('/api/admin/settings'),
      apiFetch<{ prompts: PromptRow[] }>('/api/admin/prompts'),
      apiFetch<{ users: AccountRow[] }>('/api/admin/users'),
      apiFetch<PrivacySettings>('/api/auth/privacy'),
    ]);
    if (!settingsRes.ok || !settingsRes.data) {
      setError(settingsRes.error || 'Admin settings are unavailable.');
      return;
    }
    const next = settingsRes.data;
    setSettings(next);
    setThreshold(String(next.router_threshold));
    setRulesFirst(next.rules_first);
    setGateOn(next.router_enabled);
    setJevOn(next.jev_enabled);
    const rows = promptsRes.data?.prompts || [];
    setPrompts(rows);
    const current = rows.find((row) => row.name === selected) || rows[0];
    if (current) {
      setSelected(current.name);
      setDraft(current.override);
    }
    setAccounts(usersRes.data?.users || []);
    if (privacyRes.ok && privacyRes.data) setPrivacy(privacyRes.data);
  };

  useEffect(() => { void load(); }, []);

  const saveGate = async () => {
    setError('');
    setNotice('');
    const value = Number(threshold);
    const res = await apiFetch<RouterSettings>('/api/admin/settings', {
      method: 'PUT',
      body: JSON.stringify({
        router_enabled: gateOn,
        decision_enabled: jevOn,
        router_threshold: value,
        rules_first: rulesFirst,
      }),
    });
    if (!res.ok || !res.data) {
      setError(res.error || 'Could not save how a message is handled.');
      return;
    }
    setSettings(res.data);
    setNotice(res.data.router_enabled ? 'The next message goes through the chat gate.' : 'The next message goes straight to the planner.');
  };

  const choose = (name: string) => {
    setSelected(name);
    setDraft(prompts.find((row) => row.name === name)?.override || '');
  };

  const savePrompt = async () => {
    setError('');
    setNotice('');
    const res = await apiFetch(`/api/admin/prompts/${selected}`, {
      method: 'PUT',
      body: JSON.stringify({ body: draft }),
    });
    if (!res.ok) {
      setError(res.error || 'Could not save that instruction.');
      return;
    }
    setPrompts((rows) => rows.map((row) => row.name === selected ? { ...row, override: draft.trim() } : row));
    setNotice('The next turn that uses this instruction will read the saved text.');
  };

  const resetPrompt = async () => {
    setError('');
    setNotice('');
    const res = await apiFetch(`/api/admin/prompts/${selected}`, { method: 'DELETE' });
    if (!res.ok) {
      setError(res.error || 'Could not reset that instruction.');
      return;
    }
    setDraft('');
    setPrompts((rows) => rows.map((row) => row.name === selected ? { ...row, override: '' } : row));
    setNotice('That instruction is back to the copy in the code.');
  };

  const savePrivacy = async () => {
    if (!privacy) return;
    setError('');
    setNotice('');
    const res = await apiFetch<PrivacySettings>('/api/auth/privacy', { method: 'PUT', body: JSON.stringify(privacy) });
    if (!res.ok || !res.data) {
      setError(res.error || 'Could not save what is hidden.');
      return;
    }
    setPrivacy(res.data);
    setNotice('The next message is masked with these choices.');
  };

  const showPreview = async () => {
    if (!privacy) return;
    setError('');
    const res = await apiFetch<{ person: string }>('/api/auth/privacy/preview', {
      method: 'POST',
      body: JSON.stringify({ ...privacy, text: sample }),
    });
    if (!res.ok || !res.data) {
      setError(res.error || 'Could not preview that sentence.');
      return;
    }
    setPreview(res.data.person);
  };

  const current = prompts.find((row) => row.name === selected);

  return (
    <section className="tv-profile__trips" aria-labelledby="admin-settings">
      <div className="tv-sec__head" style={{ marginBottom: '1.25rem' }}>
        <span className="tv-label">Administrator</span>
        <h2 id="admin-settings" className="tv-display tv-profile__trips-title">Settings the next turn reads.</h2>
      </div>
      {error && <p className="tv-field__error" role="alert">{error}</p>}
      {notice && <p className="tv-meta" role="status">{notice}</p>}

      {settings && (
        <fieldset className="tv-card tv-profile__card" style={{ marginBottom: '1.25rem' }}>
          <legend className="tv-label">How a message is handled</legend>
          <p className="tv-meta">Choose the path. The decision key stays in the server file.</p>
          <label className="tv-label" style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', marginTop: '0.8rem' }}>
            <input type="radio" name="message-path" checked={!gateOn} onChange={() => setGateOn(false)} />
            <span>Planner answers directly. A question stays a question, and an edit stays an edit.</span>
          </label>
          <label className="tv-label" style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', marginTop: '0.6rem' }}>
            <input type="radio" name="message-path" checked={gateOn} onChange={() => setGateOn(true)} />
            <span>A gate chooses the path first. Use this when you want a separate decision before the planner.</span>
          </label>
          {gateOn && (
            <div style={{ marginTop: '0.9rem' }}>
              <label className="tv-label" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <input type="checkbox" checked={jevOn} onChange={(event) => setJevOn(event.target.checked)} />
                Ask Jev when the rules are unsure
              </label>
              <p className="tv-meta">{settings.jev_key_set ? 'A decision key is in the server file.' : 'Jev stays unused until a decision key is in the server file.'}</p>
              <label className="tv-label" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.6rem' }}>
                <input type="checkbox" checked={rulesFirst} onChange={(event) => setRulesFirst(event.target.checked)} />
                Rules decide a clear message before any model
              </label>
              <label className="tv-label" style={{ display: 'block', marginTop: '0.8rem' }}>
                Confidence
                <input className="tv-input" inputMode="decimal" value={threshold} onChange={(event) => setThreshold(event.target.value)} />
              </label>
              <p className="tv-meta">Below this, the gate asks instead of guessing.</p>
            </div>
          )}
          <button type="button" className="tv-btn tv-btn--primary" style={{ marginTop: '0.9rem' }} onClick={() => { void saveGate(); }}>Save this choice</button>
        </fieldset>
      )}

      {privacy && (
        <fieldset className="tv-card tv-profile__card" style={{ marginBottom: '1.25rem' }}>
          <legend className="tv-label">What a model is not allowed to read</legend>
          <p className="tv-meta">Turn a kind off only when that detail should reach the planner. The chat shows the same label.</p>
          {PRIVACY_ROWS.map((row) => (
            <label key={row.key} className="tv-label" style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-start', marginTop: '0.8rem' }}>
              <input
                type="checkbox"
                checked={Boolean(privacy[row.key])}
                onChange={(event) => setPrivacy({ ...privacy, [row.key]: event.target.checked })}
              />
              <span>
                {row.title}
                <span className="tv-meta" style={{ display: 'block' }}>{row.hint}</span>
              </span>
            </label>
          ))}
          <button type="button" className="tv-link" style={{ marginTop: '0.8rem' }} onClick={() => setPatternsOpen((open) => !open)}>
            {patternsOpen ? 'Hide custom patterns' : 'Use a custom pattern'}
          </button>
          {patternsOpen && PRIVACY_ROWS.map((row) => (
            <label key={row.pattern} className="tv-label" style={{ display: 'block', marginTop: '0.6rem' }}>
              {row.title}
              <textarea className="tv-input" rows={2} spellCheck={false} value={String(privacy[row.pattern] || '')} placeholder="Empty uses the built-in pattern." onChange={(event) => setPrivacy({ ...privacy, [row.pattern]: event.target.value })} />
            </label>
          ))}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.9rem' }}>
            <button type="button" className="tv-btn tv-btn--primary" onClick={() => { void savePrivacy(); }}>Save what is hidden</button>
          </div>
          <label className="tv-label" style={{ display: 'block', marginTop: '1rem' }}>
            Try a sentence
            <textarea className="tv-input" rows={3} value={sample} onChange={(event) => setSample(event.target.value)} />
          </label>
          <button type="button" className="tv-btn tv-btn--ghost" style={{ marginTop: '0.6rem' }} onClick={() => { void showPreview(); }}>Show what the planner sees</button>
          {preview && <p className="tv-meta" style={{ marginTop: '0.6rem' }}>{preview}</p>}
        </fieldset>
      )}

      {!!prompts.length && (
        <div className="tv-card tv-profile__card" style={{ marginBottom: '1.25rem' }}>
          <h3 className="tv-label">Which instruction to edit</h3>
          <label className="tv-label" style={{ display: 'block', marginTop: '0.6rem' }}>
            Instruction
            <select className="tv-input" value={selected} onChange={(event) => choose(event.target.value)}>
              {prompts.map((row) => <option key={row.name} value={row.name}>{row.description}</option>)}
            </select>
          </label>
          <textarea className="tv-input" rows={8} value={draft} placeholder={current?.default || 'Empty uses the instruction in the code.'} onChange={(event) => setDraft(event.target.value)} style={{ marginTop: '0.6rem' }} />
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.6rem' }}>
            <button type="button" className="tv-btn tv-btn--primary" onClick={() => { void savePrompt(); }} disabled={!draft.trim()}>Save instruction</button>
            <button type="button" className="tv-btn tv-btn--ghost" onClick={() => { void resetPrompt(); }}>Use the code copy</button>
          </div>
        </div>
      )}

      {!!accounts.length && (
        <div>
          <h3 className="tv-label">Accounts</h3>
          <ul className="tv-meta">
            {accounts.map((account) => (
              <li key={account.email}>{account.full_name || account.email} · {account.email}{account.is_admin ? ' · admin' : ''}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
};
