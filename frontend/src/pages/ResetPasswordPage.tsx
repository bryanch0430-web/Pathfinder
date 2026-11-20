import React, { useState } from 'react';
import { AuthLayout } from '../components/auth/AuthLayout';
import { AuthInput } from '../components/auth/AuthInput';
import { useAuth } from '../context/AuthContext';
import { supabase } from '../lib/supabase';

interface ResetPasswordPageProps {
  onNavigateHome: () => void;
  onNavigateLogin: () => void;
  onSuccess: () => void;
}

export function ResetPasswordPage({ onNavigateHome, onNavigateLogin, onSuccess }: ResetPasswordPageProps) {
  const { user, loading: authLoading } = useAuth();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      if (!supabase) throw new Error('Account updates are unavailable in this local guest preview.');
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) setError(updateError.message);
      else onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update your password.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <AuthLayout eyebrow="Account recovery" title="Choose a new" titleAccent="password."
      subtitle="Set a new password to return to your account." onNavigateHome={onNavigateHome}>
      {authLoading ? <p className="tv-body">Checking your recovery link…</p> : user ? (
        <>
          {error && <div className="tv-alert" role="alert">{error}</div>}
          <form onSubmit={handleSubmit} className="tv-auth__form">
            <AuthInput label="New password" type="password" name="password" autoComplete="new-password"
              required minLength={6} hint="At least 6 characters." value={password}
              onChange={(event) => setPassword(event.target.value)} />
            <button type="submit" className="tv-btn tv-btn--primary" disabled={saving} style={{ width: '100%' }}>
              {saving ? 'Saving…' : 'Save new password'}
            </button>
          </form>
        </>
      ) : (
        <p className="tv-auth__alt">This recovery link is invalid or expired.{' '}
          <button type="button" onClick={onNavigateLogin}>Request a new link</button>
        </p>
      )}
    </AuthLayout>
  );
}
