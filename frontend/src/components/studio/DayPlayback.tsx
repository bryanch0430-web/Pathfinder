/*
 * Day playback for the studio map. Play walks the day's stops on a clock,
 * the way the original Pathfinder map does: the token moves between stops,
 * and the next day starts on its own when this one ends.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { formatClock, placeOnRoute, routeChain, scheduleDay, type PlaybackFrame } from '../../lib/dayRoute';
import type { TripDocument } from '../../services/tripService';

type Day = TripDocument['days'][number];

interface Props {
  days: Day[];
  day: number | null;
  onSelectDay: (day: number | null) => void;
  onFocus: (name: string) => void;
  onFrame: (frame: PlaybackFrame) => void;
  /** A pin the traveller clicked. `n` changes so the same stop can be chosen twice. */
  picked: { name: string; n: number } | null;
}

export function DayPlayback({ days, day, onSelectDay, onFocus, onFrame, picked }: Props) {
  const entry = day == null ? null : days.find((item) => item.day === day) ?? null;
  const chain = useMemo(() => (entry ? routeChain(scheduleDay(entry.items)) : []), [entry]);
  const first = chain[0]?.startMin ?? 9 * 60;
  const [clock, setClock] = useState(first);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(true);
  const carry = useRef(false);
  const advancing = useRef(false);
  const wantPlay = useRef(false);
  const focus = useRef(onFocus);
  const frame = useRef(onFrame);
  const selectDay = useRef(onSelectDay);
  focus.current = onFocus;
  frame.current = onFrame;
  selectDay.current = onSelectDay;

  useEffect(() => {
    setClock(first);
    if (carry.current) {
      carry.current = false;
      advancing.current = true;
    } else if (wantPlay.current) {
      wantPlay.current = false;
      setPlaying(true);
    } else {
      setPlaying(false);
    }
  }, [day, first]);

  useEffect(() => {
    if (!playing || chain.length < 1) return;
    const timer = window.setInterval(() => setClock((value) => value + 5), 275 / speed);
    return () => window.clearInterval(timer);
  }, [playing, speed, chain.length]);

  useEffect(() => {
    if (!playing || !chain.length) return;
    if (advancing.current) {
      advancing.current = false;
      return;
    }
    if (clock < chain[chain.length - 1].endMin) return;
    const index = days.findIndex((item) => item.day === day);
    const next = days[index + 1];
    if (!next) {
      setPlaying(false);
      return;
    }
    carry.current = true;
    selectDay.current(next.day);
  }, [clock, playing, chain, day, days]);

  useEffect(() => {
    if (!picked) return;
    const hit = chain.find((stop) => stop.name.toLowerCase() === picked.name.toLowerCase() && !stop.key.endsWith('-back'));
    if (!hit) return;
    setClock(hit.startMin);
    setPlaying(false);
  }, [picked, chain]);

  const where = placeOnRoute(chain.map((stop) => ({ start: stop.startMin, end: stop.endMin })), clock);
  const current = chain[where.leg == null ? where.at : Math.min(chain.length - 1, where.leg + 1)];

  useEffect(() => {
    frame.current({ clock, playing, follow });
    if (current) focus.current(current.name);
  }, [clock, playing, follow, current]);

  if (!days.some((item) => item.items.some((stop) => !stop.option))) return null;

  if (day == null) {
    return (
      <div className="tv-playback" role="group" aria-label="Play a day on the map">
        <button type="button" className="tv-playback__play" onClick={() => { wantPlay.current = true; onSelectDay(days[0].day); }}>
          Play day 1
        </button>
        <p className="tv-playback__now"><span className="tv-label">WHOLE ROUTE</span><strong>Pick a day to walk its stops</strong></p>
      </div>
    );
  }

  if (!chain.length) return null;

  return (
    <div className="tv-playback" role="group" aria-label="Play the day's stops on the map">
      <button type="button" className="tv-playback__play" aria-pressed={playing} onClick={() => setPlaying((value) => !value)}>
        {playing ? 'Pause' : 'Play'}
      </button>
      <button type="button" className="tv-playback__ghost" onClick={() => setSpeed((value) => (value === 1 ? 2 : value === 2 ? 4 : 1))} aria-label="Playback speed">
        {speed}×
      </button>
      <button type="button" className="tv-playback__ghost" aria-pressed={follow} onClick={() => setFollow((value) => !value)}>
        Follow
      </button>
      <p className="tv-playback__now">
        <span className="tv-label">{formatClock(clock)}{current?.key.endsWith('-back') ? ' · back' : ''}</span>
        <strong>{current?.name}</strong>
      </p>
      <span className="tv-playback__key"><i /> travelled</span>
      <span className="tv-playback__key"><i className="is-ahead" /> ahead</span>
      <ol>
        {chain.map((stop, index) => (
          <li key={stop.key}>
            <button
              type="button"
              aria-current={index === (where.leg == null ? where.at : where.leg) ? 'true' : undefined}
              onClick={() => { setClock(stop.startMin); setPlaying(false); }}
            >
              {stop.key.endsWith('-back') ? `Back to ${stop.name}` : stop.name}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
