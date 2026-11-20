import React, { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../services/apiClient';
import { useAuth } from '../context/AuthContext';

type Lane = 'waiting' | 'handling' | 'open' | 'history';

interface DeskChat {
  trip_id: string;
  destination: string | null;
  origin: string | null;
  trip_status: string;
  status: string;
  lane: Lane;
  reason: string;
  stuck: number;
  guest: boolean;
  updated_at: string | null;
}

interface DeskMessage {
  id: string;
  speaker: 'traveller' | 'planner' | 'person';
  content: string;
}

interface DayItem { name: string; start?: string | null }
interface DayPlan { day: number; items: DayItem[] }

interface DeskDetail {
  chat: DeskChat;
  brief: { origin: string; destination: string; duration_days: number | null; currency: string; pace: string; travel_mode: string };
  messages: DeskMessage[];
  day_plan: DayPlan[];
}

const shortId = (id: string) => id.slice(0, 8);
const when = (value: string | null) => {
  if (!value) return '';
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
};

export const DeskPage: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const { user, loading } = useAuth();
  const admin = Boolean(user?.user_metadata?.is_admin);
  const [chats, setChats] = useState<DeskChat[]>([]);
  const [pane, setPane] = useState<'queue' | 'history'>('queue');
  const [navOpen, setNavOpen] = useState(true);
  const [ready, setReady] = useState(() => localStorage.getItem('pathfinder.desk-ready') === 'ready');
  const [tag, setTag] = useState<'all' | 'stuck'>('all');
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const [detail, setDetail] = useState<DeskDetail | null>(null);
  const [reply, setReply] = useState('');
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [days, setDays] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [place, setPlace] = useState({ x: 120, y: 64, w: 980, h: 560 });
  const drag = useRef<{ kind: 'move' | 'size'; x: number; y: number; place: typeof place } | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const loadList = async () => {
    const res = await apiFetch<{ chats: DeskChat[] }>('/api/admin/desk');
    if (!res.ok || !res.data) {
      setError(res.error || 'The desk could not load chats.');
      return;
    }
    setChats(res.data.chats);
    setError('');
  };
  const loadChat = async (tripId: string) => {
    const res = await apiFetch<DeskDetail>(`/api/admin/desk/${tripId}`);
    if (!res.ok || !res.data) {
      setError(res.error || 'That chat could not be opened.');
      return;
    }
    setDetail(res.data);
    setOrigin(res.data.brief.origin);
    setDestination(res.data.brief.destination);
    setDays(res.data.brief.duration_days ? String(res.data.brief.duration_days) : '');
  };

  useEffect(() => {
    if (!admin) return;
    void loadList();
    const timer = window.setInterval(() => { void loadList(); }, 5000);
    return () => window.clearInterval(timer);
  }, [admin]);
  useEffect(() => {
    if (!active) return;
    void loadChat(active);
    const timer = window.setInterval(() => { void loadChat(active); }, 4000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    const node = threadRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [detail?.messages.length, active]);

  const act = async (tripId: string, path: string, body?: object) => {
    setNotice('');
    const res = await apiFetch(`/api/admin/desk/${tripId}/${path}`, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      setError(res.error || 'That desk action failed.');
      return;
    }
    await loadList();
    if (active === tripId) await loadChat(tripId);
  };

  const needle = query.trim().toLowerCase();
  const matches = (chat: DeskChat) => {
    const tagged = tag === 'all' || chat.stuck > 0 || chat.reason === 'stuck';
    const text = `${chat.destination || ''} ${chat.origin || ''} ${chat.trip_id}`.toLowerCase();
    return tagged && (!needle || text.includes(needle));
  };
  const waiting = chats.filter((chat) => chat.lane === 'waiting' && matches(chat));
  const handling = chats.filter((chat) => chat.lane === 'handling' && matches(chat));
  const open = chats.filter((chat) => chat.lane === 'open' && matches(chat));
  const history = chats.filter((chat) => chat.lane === 'history' && matches(chat));
  const held = detail?.chat.status === 'handling';

  const beginDrag = (kind: 'move' | 'size') => (event: React.PointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    if (kind === 'move' && (event.target as HTMLElement).closest('button, input, textarea, form')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { kind, x: event.clientX, y: event.clientY, place };
  };
  const followDrag = (event: React.PointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current || !mainRef.current) return;
    const box = mainRef.current.getBoundingClientRect();
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    const next = current.kind === 'move'
      ? { ...current.place, x: current.place.x + dx, y: current.place.y + dy }
      : { ...current.place, w: current.place.w + dx, h: current.place.h + dy };
    const w = Math.min(Math.max(next.w, 520), box.width - 16);
    const h = Math.min(Math.max(next.h, 320), box.height - 16);
    setPlace({
      w, h,
      x: Math.min(Math.max(next.x, 8), Math.max(8, box.width - w - 8)),
      y: Math.min(Math.max(next.y, 8), Math.max(8, box.height - h - 8)),
    });
  };

  const tile = (chat: DeskChat, waitingTile: boolean) => (
    <div
      key={chat.trip_id}
      role="button"
      tabIndex={0}
      className={`tv-ops__tile${waitingTile ? ' is-waiting' : ''}${chat.stuck || chat.reason === 'stuck' ? ' is-stuck' : ''}${active === chat.trip_id ? ' is-on' : ''}`}
      onClick={() => setActive(chat.trip_id)}
      onKeyDown={(event) => { if (event.key === 'Enter') setActive(chat.trip_id); }}
    >
      <strong className="tv-ops__sid">{chat.destination || 'New voyage'}</strong>
      <span className="tv-ops__time">{shortId(chat.trip_id)} · {when(chat.updated_at)}</span>
      <span className="tv-ops__marks">
        {(chat.stuck || chat.reason === 'stuck') && <span className="tv-ops__mark">STUCK</span>}
        {waitingTile && (
          <button type="button" className="tv-ops__accept" onClick={(event) => { event.stopPropagation(); setReady(true); setActive(chat.trip_id); void act(chat.trip_id, 'take'); }}>Accept</button>
        )}
      </span>
    </div>
  );

  const lane = (title: string, items: DeskChat[], waitingTile: boolean) => (
    <div className="tv-ops__lane">
      <div className="tv-ops__lane-card"><span>{title}</span><span>{items.length}</span></div>
      <div className="tv-ops__tiles">
        {items.length ? items.map((chat) => tile(chat, waitingTile)) : <p className="tv-ops__empty">Nothing connected.</p>}
      </div>
    </div>
  );

  if (!loading && !admin) {
    return (
      <main className="tv-ops tv-ops--locked">
        <h1 className="tv-ops__brand">Pathfinder</h1>
        <p>This desk is for an operator.</p>
        <button type="button" className="tv-ops__tag" onClick={onBack}>Back to the planner</button>
      </main>
    );
  }

  return (
    <main className="tv-ops">
      <header className="tv-ops__bar">
        <strong className="tv-ops__brand">Pathfinder</strong>
        <div className="tv-ops__status">
          <i className={`tv-ops__dot${ready ? ' is-ready' : ''}`} />
          <span>Presence</span>
          <button type="button" onClick={() => { const next = !ready; setReady(next); localStorage.setItem('pathfinder.desk-ready', next ? 'ready' : 'away'); }}>{ready ? 'Ready' : 'Away'}</button>
          <button type="button" className="tv-ops__textbtn" onClick={onBack}>Back to the planner</button>
          <button type="button" className="tv-ops__textbtn" onClick={() => window.open('/desk', 'pathfinder-desk', 'width=1280,height=840')}>Open in a window</button>
        </div>
      </header>
      <div className="tv-ops__frame">
        <nav className={`tv-ops__nav${navOpen ? '' : ' is-collapsed'}`} aria-label="Contact desk">
          <button type="button" className={pane === 'queue' ? 'is-on' : ''} onClick={() => setPane('queue')}><span>Queue</span><span>{waiting.length + handling.length + open.length}</span></button>
          <button type="button" className={pane === 'history' ? 'is-on' : ''} onClick={() => setPane('history')}><span>History</span><span>{history.length}</span></button>
          <button type="button" className="tv-ops__nav-toggle" onClick={() => setNavOpen((open) => !open)}>{navOpen ? 'Collapse' : '›'}</button>
        </nav>
        <section className="tv-ops__main" ref={mainRef}>
          {error && <p className="tv-ops__alert" role="alert">{error}</p>}
          <div className="tv-ops__scroll">
            <div className="tv-ops__filters">
              <input className="tv-ops__filter" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search city or chat" aria-label="Search chats" />
              <div className="tv-ops__tags" role="radiogroup" aria-label="Filter">
                <button type="button" className={`tv-ops__tag${tag === 'all' ? ' is-on' : ''}`} onClick={() => setTag('all')}>All</button>
                <button type="button" className={`tv-ops__tag${tag === 'stuck' ? ' is-on' : ''}`} onClick={() => setTag('stuck')}>Stuck</button>
              </div>
            </div>
            <div className="tv-ops__board">
              <h1>{pane === 'queue' ? 'Queue' : 'History'}</h1>
              <p>{pane === 'queue' ? 'Waiting chats can be accepted. Open chats are still with the planner. Take over works on any of them.' : `${history.length} finished`}</p>
              {pane === 'queue' ? (
                <>
                  {lane('With a person', handling, false)}
                  {lane('Waiting', waiting, true)}
                  {lane('With the planner', open, false)}
                </>
              ) : lane('History', history, false)}
            </div>
          </div>
          {active && detail && (
            <section className="tv-ops__session" aria-label={detail.chat.destination || 'Chat'} style={{ transform: `translate(${place.x}px, ${place.y}px)`, width: place.w, height: place.h }}>
              <header className="tv-ops__winbar" onPointerDown={beginDrag('move')} onPointerMove={followDrag} onPointerUp={() => { drag.current = null; }}>
                <i className="tv-ops__grip" aria-hidden="true" />
                <h2>{detail.chat.destination || 'New voyage'}</h2>
                <button type="button" className="tv-ops__textbtn" onClick={() => setActive(null)} aria-label="Close">Close</button>
              </header>
              <div className="tv-ops__work">
                <section className="tv-ops__thread">
                  <div className="tv-ops__threadbar">
                    <div>
                      <strong>{shortId(detail.chat.trip_id)}</strong>
                      <p>{detail.chat.guest ? 'Guest' : 'Signed in'} · {detail.chat.origin || 'Origin not set'}</p>
                    </div>
                    <button type="button" className="tv-ops__tag" onClick={() => { setReady(true); void act(active, held ? 'release' : 'take'); }}>{held ? 'Hand back' : 'Take over'}</button>
                  </div>
                  <div className="tv-ops__messages" ref={threadRef}>
                    {detail.messages.length ? detail.messages.map((message) => (
                      <p key={message.id} className={`is-${message.speaker}`}>
                        <span className="tv-ops__who">{message.speaker === 'traveller' ? 'Traveller' : message.speaker === 'person' ? 'Person' : 'Planner'}</span>
                        {message.content}
                      </p>
                    )) : <p>No messages yet.</p>}
                  </div>
                  {held ? (
                    <form className="tv-ops__say" onSubmit={(event) => { event.preventDefault(); if (!reply.trim()) return; void act(active, 'say', { text: reply }).then(() => setReply('')); }}>
                      <input value={reply} onChange={(event) => setReply(event.target.value)} placeholder="Reply as yourself" />
                      <button type="submit" className="tv-ops__tag is-on">Send</button>
                    </form>
                  ) : <p className="tv-ops__lock">Take this chat over before you reply.</p>}
                </section>
                <aside className="tv-ops__contact">
                  <strong>Contact</strong>
                  <dl>
                    <div><dt>Session</dt><dd>{shortId(detail.chat.trip_id)}</dd></div>
                    <div><dt>{detail.chat.guest ? 'Guest' : 'Signed in'}</dt><dd>{detail.chat.guest ? 'Guest' : 'Account'}</dd></div>
                    <div><dt>Trip</dt><dd>{detail.brief.destination || 'None'}</dd></div>
                    <div><dt>From</dt><dd>{detail.brief.origin || 'None'}</dd></div>
                    <div><dt>Days</dt><dd>{detail.brief.duration_days || 'None'}</dd></div>
                    <div><dt>Preferences</dt><dd>{[detail.brief.pace, detail.brief.travel_mode, detail.brief.currency].filter(Boolean).join(' · ') || 'None saved'}</dd></div>
                  </dl>
                  {notice && <p className="tv-ops__time">{notice}</p>}
                  {held && (
                    <div className="tv-ops__tools">
                      <strong>Details</strong>
                      <p className="tv-ops__time">Saving writes the brief. The planner does not run.</p>
                      <form onSubmit={(event) => {
                        event.preventDefault();
                        void apiFetch(`/api/admin/desk/${active}/brief`, { method: 'PUT', body: JSON.stringify({ origin, destination, duration_days: days ? Number(days) : null }) })
                          .then((res) => { setNotice(res.ok ? 'Details saved.' : res.error || 'Could not save.'); if (res.ok) void loadChat(active); });
                      }}>
                        <label className="tv-ops__field">To<input value={destination} onChange={(event) => setDestination(event.target.value)} /></label>
                        <label className="tv-ops__field">From<input value={origin} onChange={(event) => setOrigin(event.target.value)} /></label>
                        <label className="tv-ops__field">Days<input value={days} inputMode="numeric" onChange={(event) => setDays(event.target.value)} /></label>
                        <button type="submit" className="tv-ops__tag">Save details</button>
                      </form>
                      <button type="button" className="tv-ops__tag is-on" onClick={() => { void act(active, 'release-draft').then(() => setNotice('The traveller can edit this plan now.')); }}>Release as draft</button>
                      <div>
                        {detail.day_plan.length ? detail.day_plan.map((day) => (
                          <div key={day.day} className="tv-ops__day">
                            <h3>Day {day.day}</h3>
                            {(day.items || []).map((item) => (
                              <form key={`${day.day}-${item.name}`} className="tv-ops__stop" onSubmit={(event) => {
                                event.preventDefault();
                                const start = String(new FormData(event.currentTarget).get('start') || item.start || '09:00');
                                void act(active, 'stop', { day: day.day, name: item.name, start });
                              }}>
                                <span>{item.name}</span>
                                <input name="start" defaultValue={item.start || ''} placeholder="09:00" />
                                <button type="submit" className="tv-ops__textbtn">Save</button>
                              </form>
                            ))}
                          </div>
                        )) : <p className="tv-ops__time">This chat has no plan yet.</p>}
                      </div>
                    </div>
                  )}
                </aside>
              </div>
              <span className="tv-ops__size" onPointerDown={beginDrag('size')} onPointerMove={followDrag} onPointerUp={() => { drag.current = null; }} />
            </section>
          )}
        </section>
      </div>
    </main>
  );
};
