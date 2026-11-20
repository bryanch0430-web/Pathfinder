import React, { createContext, useContext, useEffect, useState } from 'react';
import { AuthError, type User, type Session } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';
import { clearGuestId, getStoredGuestId } from '../lib/guest';
import { clearLocalToken, getLocalToken, setLocalToken } from '../lib/localAuth';
import { API_BASE_URL } from '../services/apiClient';

let pendingGuestClaim: Promise<void> | null = null;

function claimGuestTrips(accessToken?: string): Promise<void> {
  const guestId = getStoredGuestId();
  if (!accessToken || !guestId) return Promise.resolve();
  if (pendingGuestClaim) return pendingGuestClaim;

  pendingGuestClaim = fetch(`${API_BASE_URL}/api/auth/claim-guest-trips`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ guest_id: guestId }),
  }).then((response) => {
    if (response.ok) {
      clearGuestId();
      window.dispatchEvent(new Event('tripverse:trips-claimed'));
    }
  }).catch(() => {
    // Keep the guest ID so a later session restore can retry the claim.
  }).finally(() => { pendingGuestClaim = null; });

  return pendingGuestClaim;
}

/** A local account, shaped like the Supabase objects the rest of the app expects. */
interface LocalAccount {
  id: string;
  email: string;
  full_name?: string | null;
  is_admin?: boolean;
}

const localUser = (account: LocalAccount): User => ({
  id: account.id,
  aud: 'authenticated',
  role: 'authenticated',
  email: account.email,
  email_confirmed_at: new Date().toISOString(),
  app_metadata: { provider: 'local', providers: ['local'] },
  user_metadata: { full_name: account.full_name ?? null, is_admin: account.is_admin ?? false },
  created_at: new Date().toISOString(),
} as unknown as User);

const localSession = (token: string, user: User, expiresIn: number): Session => ({
  access_token: token,
  refresh_token: '',
  token_type: 'bearer',
  expires_in: expiresIn,
  expires_at: Math.floor(Date.now() / 1000) + expiresIn,
  user,
} as unknown as Session);

async function postLocal(path: string, body: unknown): Promise<{ ok: boolean; detail?: string; data?: any }> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => null);
    const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}).`;
    if (!response.ok) return { ok: false, detail };
    return { ok: true, data };
  } catch {
    return { ok: false, detail: 'The Pathfinder backend is not reachable.' };
  }
}

interface AuthContextType {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signInWithPassword: (email: string, password: string) => Promise<{ error: AuthError | null }>;
  signUp: (email: string, password: string, fullName?: string) => Promise<{ error: AuthError | null; needsEmailConfirmation: boolean }>;
  signInWithOAuth: (provider: 'google' | 'github') => Promise<{ error: AuthError | null }>;
  sendPasswordReset: (email: string) => Promise<{ error: AuthError | null }>;
  signOut: () => Promise<{ error: AuthError | null }>;
  updateProfile: (fullName: string) => Promise<{ error: AuthError | null }>;
  getToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (supabase) {
      // 1. Fetch current session
      supabase.auth.getSession().then(({ data: { session } }) => {
        setSession(session);
        setUser(session?.user ?? null);
        setLoading(false);
        void claimGuestTrips(session?.access_token);
      }).catch(() => {
        setLoading(false);
      });

      // 2. Listen for auth state changes
      const {
        data: { subscription },
      } = supabase.auth.onAuthStateChange((event, session) => {
        setSession(session);
        setUser(session?.user ?? null);
        setLoading(false);
        if (event === 'SIGNED_IN') void claimGuestTrips(session?.access_token);
      });

      return () => {
        subscription.unsubscribe();
      };
    }

    // No Supabase in this build: restore a local account token, if any.
    const token = getLocalToken();
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) {
          clearLocalToken();
          return;
        }
        const me = await response.json();
        if (cancelled) return;
        const nextUser = localUser({ id: me.id, email: me.email, full_name: me.full_name, is_admin: me.is_admin });
        setUser(nextUser);
        setSession(localSession(token, nextUser, 0));
        void claimGuestTrips(token);
      })
      .catch(() => clearLocalToken())
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, []);

  const signInWithPassword = async (email: string, password: string) => {
    if (supabase) {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (!error) await claimGuestTrips(data.session?.access_token);
      return { error };
    }

    const result = await postLocal('/api/auth/local/login', { email, password });
    if (!result.ok) return { error: new AuthError(result.detail ?? 'Sign in failed.') };
    const nextUser = localUser(result.data.user);
    setLocalToken(result.data.access_token);
    setUser(nextUser);
    setSession(localSession(result.data.access_token, nextUser, result.data.expires_in ?? 0));
    await claimGuestTrips(result.data.access_token);
    return { error: null };
  };

  const signUp = async (email: string, password: string, fullName?: string) => {
    if (supabase) {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/profile`,
          data: {
            full_name: fullName,
          },
        },
      });
      if (!error) await claimGuestTrips(data.session?.access_token);
      return { error, needsEmailConfirmation: !error && !data.session };
    }

    const result = await postLocal('/api/auth/local/register', { email, password, full_name: fullName ?? null });
    if (!result.ok) return { error: new AuthError(result.detail ?? 'Could not create the account.'), needsEmailConfirmation: false };
    const nextUser = localUser(result.data.user);
    setLocalToken(result.data.access_token);
    setUser(nextUser);
    setSession(localSession(result.data.access_token, nextUser, result.data.expires_in ?? 0));
    await claimGuestTrips(result.data.access_token);
    return { error: null, needsEmailConfirmation: false };
  };

  const signInWithOAuth = async (provider: 'google' | 'github') => {
    if (!supabase) return { error: new AuthError('External sign-in needs Supabase; use your email and password here.') };
    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/profile`,
      },
    });
    return { error };
  };

  const sendPasswordReset = async (email: string) => {
    if (!supabase) return { error: new AuthError('Password reset needs Supabase. Ask an administrator to reset a local password.') };
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    return { error };
  };

  const signOut = async () => {
    if (supabase) {
      const { error } = await supabase.auth.signOut();
      return { error };
    }
    const token = getLocalToken();
    if (token) {
      void fetch(`${API_BASE_URL}/api/auth/local/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => undefined);
    }
    clearLocalToken();
    setUser(null);
    setSession(null);
    return { error: null };
  };

  const updateProfile = async (fullName: string) => {
    if (supabase) {
      const { error } = await supabase.auth.updateUser({ data: { full_name: fullName } });
      return { error };
    }
    const token = getLocalToken();
    if (!token) return { error: new AuthError('Sign in to change your details.') };
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/local/profile`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ full_name: fullName }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = typeof data?.detail === 'string' ? data.detail : 'Could not save your details.';
        return { error: new AuthError(detail) };
      }
      const nextUser = localUser(data.user);
      setLocalToken(data.access_token);
      setUser(nextUser);
      setSession(localSession(data.access_token, nextUser, data.expires_in ?? 0));
      return { error: null };
    } catch {
      return { error: new AuthError('The Pathfinder backend is not reachable.') };
    }
  };

  const getToken = async (): Promise<string | null> => {
    if (!supabase) return getLocalToken();
    if (!session?.access_token) {
      const { data } = await supabase.auth.getSession();
      return data.session?.access_token ?? null;
    }
    return session.access_token;
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        loading,
        signInWithPassword,
        signUp,
        signInWithOAuth,
        sendPasswordReset,
        signOut,
        updateProfile,
        getToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
