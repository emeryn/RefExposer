import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { ApiError, api, isUnreachable, setUnauthorizedHandler } from '../api/client';
import type { Access, Me } from '../api/types';

interface AuthValue {
  me: Me | null;
  isLoading: boolean;
  /** The backend could not be reached, which is **not** the same as being signed out */
  unreachable: boolean;
  /** The session ended on its own: the sign-in page says so rather than appearing for no reason */
  expired: boolean;
  setMe: (me: Me | null) => void;
  logout: () => Promise<void>;
  /** Effective access of the current user on a referential. */
  access: (refId: string) => Access | null;
  canManage: (refId: string) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  // Set when a request comes back 401 while an account was signed in, cleared by the next sign-in
  const [expired, setExpired] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        // Not signed in is an *answer*: null, and the sign-in page comes next.
        if (e instanceof ApiError && e.status === 401) return null;
        // Anything else is a failure and must not read as being signed out, or a restarted backend would
        // throw everyone onto the sign-in page and lose what they were doing. The previous answer is kept.
        throw e;
      }
    },
    // A failure here is an outage, not a refusal: a few quick attempts, then the connection watcher polls
    // and refetches this with everything else once the backend answers again
    retry: (count, e) => isUnreachable(e) && count < 3,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
    // The one query that follows the focus of the window (the others deliberately do not): coming back to a
    // tab left open over lunch should find out at once that the session ended, rather than showing an
    // interface that looks alive and refuses the first click
    refetchOnWindowFocus: true,
  });

  const setMe = useCallback(
    (me: Me | null) => {
      if (!me) qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
      else setExpired(false);
      qc.setQueryData(['me'], me);
    },
    [qc],
  );

  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (!qc.getQueryData(['me'])) return;
      // The pages that were loading when the session ended each have their own error to report; a stack of
      // "401 Not authenticated" over the sign-in page explains nothing, and the page itself says what happened
      notifications.clean();
      setExpired(true);
      setMe(null);
    });
  }, [qc, setMe]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setExpired(false);
      setMe(null);
    }
  }, [setMe]);

  const me = data ?? null;
  const access = useCallback((refId: string) => (me?.is_admin ? 'manage' : me?.permissions[refId] ?? null), [me]);
  const value: AuthValue = {
    me,
    isLoading,
    unreachable: !me && isUnreachable(error),
    expired,
    setMe,
    logout,
    access,
    canManage: (refId) => access(refId) === 'manage',
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
