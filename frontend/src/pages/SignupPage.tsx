import React, { useState } from 'react';
import { AuthLayout } from '../components/auth/AuthLayout';
import { AuthInput } from '../components/auth/AuthInput';
import { SocialAuth } from '../components/auth/SocialAuth';
import { ArrowUpRightIcon } from '../components/home/v2/IconsV2';
import { useAuth } from '../context/AuthContext';
import { SIGNUP_AUTH_VISUAL } from '../constants/authVisuals';

interface SignupPageProps {
  onNavigateLogin: () => void;
  onNavigateHome: () => void;
  onSuccess: () => void;
}

export const SignupPage: React.FC<SignupPageProps> = ({
  onNavigateLogin,
  onNavigateHome,
  onSuccess,
}) => {
  const { signUp } = useAuth();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);

    if (password.length < 6) {
      setError('Use at least 6 characters for your password.');
      return;
    }

    setLoading(true);
    try {
      const { error: authError, needsEmailConfirmation } = await signUp(email.trim(), password, fullName.trim());
      if (authError) {
        setError(authError.message || 'Could not create that account.');
      } else if (needsEmailConfirmation) {
        setNotice('Check your email to confirm your account, then sign in.');
      } else {
        onSuccess();
      }
    } catch (err: any) {
      setError(err?.message || 'Something went wrong creating your account.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="Create account"
      title="Describe the trip."
      titleAccent="Watch it get built."
      subtitle="An account saves your trips and lets you reopen a plan on any device. You can try Pathfinder as a guest first."
      onNavigateHome={onNavigateHome}
      visual={SIGNUP_AUTH_VISUAL}
    >
      {error && (
        <div className="tv-alert" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="tv-alert" role="status">{notice}</div>}

      <form onSubmit={handleSubmit} className="tv-auth__form">
        <AuthInput
          label="Name"
          type="text"
          name="name"
          autoComplete="name"
          required
          placeholder="How should we address you?"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
        />

        <AuthInput
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          required
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <AuthInput
          label="Password"
          type="password"
          name="password"
          autoComplete="new-password"
          required
          placeholder="••••••••"
          hint="At least 6 characters."
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <button type="submit" className="tv-btn tv-btn--primary" disabled={loading} style={{ width: '100%' }}>
          <span>{loading ? 'Creating account…' : 'Create account'}</span>
          {!loading && <ArrowUpRightIcon width={15} height={15} />}
        </button>
      </form>

      <div className="tv-divider">
        <span className="tv-label">or</span>
      </div>

      <SocialAuth onError={setError} />

      <p className="tv-auth__alt">
        Already have an account?{' '}
        <button type="button" onClick={onNavigateLogin}>
          Sign in
        </button>
      </p>
    </AuthLayout>
  );
};
