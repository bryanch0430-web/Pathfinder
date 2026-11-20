import { supabase } from '../lib/supabase';
import { getOrCreateGuestId } from '../lib/guest';
import { getLocalToken } from '../lib/localAuth';

export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface ApiRequestOptions extends RequestInit {
  /**
   * If true, bypasses automatic guest ID / user token injection.
   */
  skipAuth?: boolean;
}

// In-memory token cache to avoid awaiting supabase.auth.getSession() on every API call
let cachedAccessToken: string | null = null;
let cachedTokenExpiresAt: number = 0;

// Listen to Supabase auth events to keep token cache hot
supabase?.auth.onAuthStateChange((_event, session) => {
  cachedAccessToken = session?.access_token ?? null;
  cachedTokenExpiresAt = session?.expires_at ? session.expires_at * 1000 : 0;
});

/**
 * Helper to construct authentication headers (Bearer token or Guest ID).
 */
export async function getAuthHeaders(skipAuth: boolean = false): Promise<Headers> {
  const headers = new Headers();
  if (skipAuth) return headers;

  try {
    // A local account (no Supabase) wins over the guest identity.
    const localToken = getLocalToken();
    if (localToken) {
      headers.set('Authorization', `Bearer ${localToken}`);
      return headers;
    }

    let token = cachedAccessToken;
    const now = Date.now();

    // If cache miss or token expiring within 60 seconds, fetch session
    if (supabase && (!token || now >= cachedTokenExpiresAt - 60000)) {
      const { data: { session } } = await supabase.auth.getSession();
      token = session?.access_token ?? null;
      cachedAccessToken = token;
      cachedTokenExpiresAt = session?.expires_at ? session.expires_at * 1000 : 0;
    }

    if (token) {
      // Authenticated User Identity
      headers.set('Authorization', `Bearer ${token}`);
    } else {
      // Guest Identity
      const guestId = getOrCreateGuestId();
      headers.set('X-Guest-ID', guestId);
    }
  } catch (err) {
    console.warn('Error resolving auth state in apiClient, falling back to guest ID:', err);
    headers.set('X-Guest-ID', getOrCreateGuestId());
  }

  return headers;
}

/** FastAPI validation errors are a list of objects. `new Error(that)` prints "[object Object]". */
export function errorText(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value.trim()) return value;
  if (Array.isArray(value)) {
    const lines = value.map((item) => errorText(item, '')).filter(Boolean);
    if (lines.length) return lines.join(' ');
  }
  if (value && typeof value === 'object') {
    const record = value as { msg?: unknown; message?: unknown; loc?: unknown };
    const raw = typeof record.msg === 'string' ? record.msg : typeof record.message === 'string' ? record.message : '';
    const message = raw.replace(/^Value error,\s*/, '').trim();
    if (message) {
      const loc = Array.isArray(record.loc)
        ? record.loc.filter((part) => part !== 'body' && part !== 'payload').join('.')
        : '';
      return loc ? `${loc}: ${message}` : message;
    }
  }
  return fallback;
}

/**
 * Centralized API fetch wrapper for Pathfinder.
 * Automatically injects:
 * - `Authorization: Bearer <token>` if an active Supabase user session exists.
 * - `X-Guest-ID: <UUID>` if the user is unauthenticated (anonymous guest).
 *
 * Guarantees mutual exclusivity so both headers are never sent concurrently.
 */
export async function apiFetch<T = any>(
  endpoint: string,
  options: ApiRequestOptions = {}
): Promise<{ data: T | null; status: number; ok: boolean; error?: string }> {
  const { skipAuth = false, headers: customHeaders, ...restOptions } = options;
  const authHeaders = await getAuthHeaders(skipAuth);
  const headers = new Headers(customHeaders || {});

  // Merge auth headers if not overridden
  authHeaders.forEach((value, key) => {
    if (!headers.has(key)) {
      headers.set(key, value);
    }
  });

  // Set default JSON Content-Type if not already provided and sending a body
  if (!headers.has('Content-Type') && restOptions.body && typeof restOptions.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE_URL}${endpoint}`;

  try {
    const res = await fetch(url, {
      ...restOptions,
      headers,
    });

    let responseData: any = null;
    const contentType = res.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      responseData = await res.json();
    } else {
      const text = await res.text();
      responseData = text ? { message: text } : null;
    }

    if (!res.ok) {
      const errorMessage = errorText(responseData?.detail ?? responseData?.message, `HTTP ${res.status}`);
      return {
        data: responseData,
        status: res.status,
        ok: false,
        error: errorMessage,
      };
    }

    return {
      data: responseData as T,
      status: res.status,
      ok: true,
    };
  } catch (err: any) {
    return {
      data: null,
      status: 0,
      ok: false,
      error: err?.message || 'Network request failed',
    };
  }
}
