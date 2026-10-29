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

/**
 * The alert feed. One query for the whole application, so the bell in the header and the alerts page share
 * the same data and the same refresh.
 *
 * `counts.unread` is what the account has not marked as read, kept on the server: the badge of the bell says
 * the same thing on every browser the person uses, and an alert read elsewhere stops counting here.
 */
export function useAlerts(limit = 50, dismissed = false) {
  return useQuery({
    queryKey: ['alerts', limit, dismissed],
    queryFn: () => api.alerts(limit, undefined, dismissed),
    refetchInterval: 60_000,
  });
}

/**
 * Availability of the relational search. It is an **experimental feature**, off until an administrator turns
 * it on, so the menu entry, the route and the assistant tools all follow this one answer rather than letting
 * the page fail on an endpoint that is not there.
 */
export function useInvestigate() {
  return useQuery({ queryKey: ['investigate-status'], queryFn: api.investigateStatus, staleTime: 60_000 });
}

/** Availability of the assistant: it only appears in the menu once an administrator enabled it. */
export function useAssistant() {
  return useQuery({ queryKey: ['assistant'], queryFn: api.assistant, staleTime: 60_000 });
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
    mutationFn: ({ id, force, replace }: { id: string; force?: boolean; replace?: boolean }) => api.refresh(id, force, replace),
    onSuccess: (run) => {
      const how = run.replace ? ' (new base)' : run.force ? ' (forced rebuild)' : '';
      notifications.show({ title: 'Update started', message: `${run.ref_id}${how}`, color: 'blue' });
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
