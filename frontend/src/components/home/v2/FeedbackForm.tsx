import { useRef, useState, type FormEvent } from 'react';
import { apiFetch } from '../../../services/apiClient';
import './feedback-form.css';

const RATINGS = [
  { label: 'Rough', sticker: 'rough' },
  { label: 'Meh', sticker: 'meh' },
  { label: 'Okay', sticker: 'okay' },
  { label: 'Happy', sticker: 'happy' },
  { label: 'Loved it', sticker: 'love' },
];

export function FeedbackForm() {
  const [rating, setRating] = useState<number | null>(null);
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');
  const pending = useRef(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current || rating === null) return;
    const form = new FormData(event.currentTarget);
    pending.current = true;
    setStatus('sending');
    const response = await apiFetch('/api/feedback', {
      method: 'POST', skipAuth: true, signal: AbortSignal.timeout(90000),
      body: JSON.stringify({ rating, message: message.trim(), page: window.location.pathname, website: form.get('website') || '' }),
    });
    pending.current = false;
    if (response.ok) {
      setStatus('sent');
      setMessage('');
      setRating(null);
    } else {
      setStatus('error');
      setError(response.status === 429
        ? 'You’ve sent a few responses recently. Please try again in 10 minutes.'
        : 'Your feedback couldn’t be sent. Please try again shortly.');
    }
  };

  return (
    <div className="tv-feedback" id="feedback">
      <div className="tv-feedback__intro">
        <h3>How was your experience?</h3>
        <p>A quick reaction or a few words help us make Pathfinder better.</p>
      </div>
      <form onSubmit={submit} className="tv-feedback__form" aria-label="Share your feedback">
        <fieldset disabled={status === 'sending'}>
          <legend>Rate your experience</legend>
          <div className="tv-feedback__ratings">
            {RATINGS.map(({ label, sticker }, index) => (
              <label key={label}>
                <input type="radio" name="rating" value={index + 1} required checked={rating === index + 1}
                  onChange={() => { setRating(index + 1); setStatus('idle'); }} />
                <span>
                  <img className="tv-feedback__reaction" src={`/images/feedback/${sticker}.webp`} alt="" width={64} height={64} draggable={false} />
                  {label}
                </span>
              </label>
            ))}
          </div>
          <label className="tv-feedback__message" htmlFor="feedback-message">Anything you’d like us to know? <span>(optional)</span></label>
          <textarea id="feedback-message" name="message" rows={3} maxLength={2000} value={message}
            placeholder="What worked, or what could be better?" onChange={(event) => setMessage(event.target.value)} />
          <div className="tv-feedback__trap" aria-hidden="true"><label htmlFor="feedback-website">Website</label><input id="feedback-website" name="website" autoComplete="off" tabIndex={-1} /></div>
          <div className="tv-feedback__actions">
            <button type="submit" className="tv-btn tv-btn--primary" disabled={rating === null || status === 'sending'}>{status === 'sending' ? 'Sending…' : 'Send feedback'}</button>
            <p role="status" aria-live="polite">{status === 'sent' ? 'Thanks — your feedback has been sent.' : status === 'error' ? error : ''}</p>
          </div>
        </fieldset>
      </form>
    </div>
  );
}
