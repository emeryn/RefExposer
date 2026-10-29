/**
 * Whether the backend is answering, and how the interface finds out that it is again.
 *
 * A browser application cannot be left waiting for someone to press F5. The backend is restarted — an
 * update, a crash, a machine rebooted — and for a few seconds every request fails; without anything watching,
 * React Query gives up after its retries and each page stays on the error it happened to get. The pages are
 * then stale for as long as the tab is open.
 *
 * So a failure that never reached the server (a refused connection, or a `502`/`503`/`504` from the proxy in
 * front of it, which means the proxy is up and the application is not) switches this store to *unreachable*
 * and starts polling `/api/health` with a backoff. That endpoint is **public**: it answers whether or not the
 * session is still valid, which is what makes it a reachability probe rather than a second authentication
 * question. When it answers, everything the pages are showing is refetched at once and the interface carries
 * on where it was.
 *
 * Deliberately a plain module and not a React context: `client.ts` reports into it, and `client.ts` is called
 * from query functions rather than from components.
 */

/** Waits between probes, in milliseconds, then the last one over and over. Quick at first: most outages of a
 *  restarted container are over in a few seconds, and nobody should have to wait fifteen for the picture. */
const DELAYS = [1000, 2000, 4000, 8000, 15000];

export interface Connection {
  /** Whether the last request reached the backend */
  reachable: boolean;
  /** Probes that failed since it went away: what the notice counts */
  attempts: number;
  /** A probe is in flight */
  checking: boolean;
  /** When it went away, so the notice can say how long */
  since: number | null;
}

let state: Connection = { reachable: true, attempts: 0, checking: false, since: null };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;
let recovered: () => void = () => {};

function set(next: Partial<Connection>) {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}

/** `useSyncExternalStore` needs these two, and the identity of the snapshot has to be stable. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export const snapshot = (): Connection => state;

/** What to do when the backend comes back: refetch what the pages are showing. Set once, by the application. */
export function onRecovered(fn: () => void) {
  recovered = fn;
}

function stop() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function schedule(delay: number) {
  stop();
  timer = setTimeout(() => {
    timer = null;
    void probe();
  }, delay);
}

/** A request did not reach the server. Reported by `client.ts`; the first one starts the polling. */
export function reportUnreachable() {
  if (!state.reachable) return;
  set({ reachable: false, attempts: 0, since: Date.now() });
  // Not immediately: a single failure is often a request caught mid-restart, and half a second is enough to
  // tell that apart from an outage without flashing a notice for nothing
  schedule(500);
}

/** A request got an answer, so the backend is there. Cheap: it does nothing unless something had failed. */
export function reportReachable() {
  if (state.reachable) return;
  stop();
  set({ reachable: true, attempts: 0, checking: false, since: null });
  recovered();
}

/**
 * Probe the public liveness endpoint. Returns whether it answered; a failure schedules the next probe, so
 * calling this once is enough to keep trying until the backend is back.
 */
export async function probe(): Promise<boolean> {
  if (state.checking) return state.reachable;
  set({ checking: true });
  try {
    const res = await fetch('/api/health', { cache: 'no-store', headers: { Accept: 'application/json' } });
    // 5xx here is the proxy, not the application: nginx answers, whatever it proxies to does not
    if (res.status >= 500) throw new Error(`${res.status}`);
    reportReachable();
    return true;
  } catch {
    set({ checking: false, attempts: state.attempts + 1 });
    schedule(DELAYS[Math.min(state.attempts - 1, DELAYS.length - 1)]);
    return false;
  }
}

/** Probe now rather than on the next scheduled attempt: the notice offers it, and so do these two events. */
export function checkNow() {
  stop();
  void probe();
}

if (typeof window !== 'undefined') {
  // The network came back, or the tab did: both are worth a probe at once instead of waiting out the backoff
  window.addEventListener('online', () => {
    if (!state.reachable) checkNow();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !state.reachable) checkNow();
  });
}
