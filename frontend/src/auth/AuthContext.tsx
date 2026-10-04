import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, type ReactNode } from 'react';
import { ApiError, api, setUnauthorizedHandler } from '../api/client';
import type { Access, Me } from '../api/types';

interface AuthValue {
  me: Me | null;
  isLoading: boolean;
  setMe: (me: Me | null) => void;
  logout: () => Promise<void>;
  /** Effective access of the current user on a referential. */
  access: (refId: string) => Access | null;
  canManage: (refId: string) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    retry: false,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });

  const setMe = useCallback(
    (me: Me | null) => {
      if (!me) qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
      qc.setQueryData(['me'], me);
    },
    [qc],
  );

  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (qc.getQueryData(['me'])) setMe(null);
    });
  }, [qc, setMe]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setMe(null);
    }
  }, [setMe]);

  const me = data ?? null;
  const access = useCallback((refId: string) => (me?.is_admin ? 'manage' : me?.permissions[refId] ?? null), [me]);
  const value: AuthValue = {
    me,
    isLoading,
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
