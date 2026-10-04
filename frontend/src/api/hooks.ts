import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { api, setPublicBase } from './client';
import type { ReferentialSummary } from './types';

const anyRunning = (refs?: ReferentialSummary[]) => refs?.some((r) => r.current_run) ?? false;

export function useReferentials() {
  return useQuery({
    queryKey: ['referentials'],
    queryFn: api.referentials,
    refetchInterval: (q) => (anyRunning(q.state.data) ? 2000 : 15000),
  });
}

export function useReferential(id: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['referential', id],
    enabled: opts.enabled ?? true,
    queryFn: () => api.referential(id),
    refetchInterval: (q) => (q.state.data?.current_run ? 1500 : 15000),
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 2,
  });
}

export function useRefresh() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) => api.refresh(id, force),
    onSuccess: (run) => {
      notifications.show({ title: 'Update started', message: `${run.ref_id}${run.force ? ' (forced rebuild)' : ''}`, color: 'blue' });
      qc.invalidateQueries({ queryKey: ['referentials'] });
      qc.invalidateQueries({ queryKey: ['referential', run.ref_id] });
      qc.invalidateQueries({ queryKey: ['runs', run.ref_id] });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
}

export function useCancel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.cancel(id),
    onSuccess: (_, id) => {
      notifications.show({ title: 'Cancellation requested', message: id, color: 'orange' });
      qc.invalidateQueries({ queryKey: ['referential', id] });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
}

/** Name, logo and public URL of the application (public: also used on the sign-in page). */
export function useBranding() {
  return useQuery({
    queryKey: ['branding'],
    queryFn: async () => {
      const b = await api.branding();
      setPublicBase(b.public_url);
      document.title = b.subtitle ? `${b.title} — ${b.subtitle}` : b.title;
      return b;
    },
    staleTime: 5 * 60_000,
  });
}
