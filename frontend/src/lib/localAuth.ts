/*
 * The local account token.
 *
 * When the app runs without Supabase (VITE_GUEST_ONLY=true) sign-in goes to the
 * backend's own /api/auth/local endpoints instead, and the JWT it returns lives
 * here. apiClient sends it as a Bearer token exactly like a Supabase token, so
 * every route treats a signed-in local account as an ordinary authenticated user.
 */
const TOKEN_KEY = 'pathfinder-local-token';

export const getLocalToken = (): string | null => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export const setLocalToken = (token: string): void => {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage blocked: the session lasts until reload */
  }
};

export const clearLocalToken = (): void => {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to do */
  }
};
