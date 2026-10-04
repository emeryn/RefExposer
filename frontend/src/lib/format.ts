import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import localizedFormat from 'dayjs/plugin/localizedFormat';

dayjs.extend(relativeTime);
dayjs.extend(localizedFormat);

export const LOCALE = 'en-GB';
const nf = new Intl.NumberFormat(LOCALE);
const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });

export const fmtNumber = (n: number | null | undefined) => (n == null ? '—' : nf.format(n));
export const fmtCompact = (n: number | null | undefined) => (n == null ? '—' : compact.format(n));
export const fmtDecimal = (n: number, digits = 6) => n.toLocaleString(LOCALE, { maximumFractionDigits: digits });

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toLocaleString(LOCALE, { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 })} ${units[i]}`;
}

export const fmtRelative = (d: string | null | undefined) => (d ? dayjs(d).fromNow() : '—');
export const fmtDate = (d: string | null | undefined) => (d ? dayjs(d).format('YYYY-MM-DD HH:mm') : '—');
export const fmtDateLong = (d: string | null | undefined) => (d ? dayjs(d).format('dddd D MMMM YYYY, HH:mm:ss') : '—');

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m} min ${s.toString().padStart(2, '0')} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function fmtDelta(n: number | null | undefined): string {
  if (n == null || n === 0) return '±0';
  return `${n > 0 ? '+' : '−'}${nf.format(Math.abs(n))}`;
}

const CRON_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** Human readable description of common crontab expressions. */
export function describeCron(expr: string | null | undefined): string {
  if (!expr) return 'Manual';
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [min, hour, dom, mon, dow] = parts;
  const at = (h: string, m: string) => `${h.padStart(2, '0')}:${m.padStart(2, '0')}`;
  const isNum = (s: string) => /^\d+$/.test(s);
  if (dom === '*' && mon === '*') {
    if (min.startsWith('*/') && hour === '*' && dow === '*') return `Every ${min.slice(2)} min`;
    if (isNum(min) && hour.startsWith('*/') && dow === '*') return `Every ${hour.slice(2)} h (at :${min.padStart(2, '0')})`;
    if (isNum(min) && hour === '*' && dow === '*') return `Hourly (at :${min.padStart(2, '0')})`;
    if (isNum(min) && isNum(hour) && dow === '*') return `Daily at ${at(hour, min)}`;
    if (isNum(min) && isNum(hour) && isNum(dow)) return `Every ${CRON_DAYS[Number(dow) % 7]} at ${at(hour, min)}`;
  }
  if (isNum(min) && isNum(hour) && isNum(dom) && mon === '*' && dow === '*') return `Monthly on day ${dom} at ${at(hour, min)}`;
  return expr;
}

export function plural(n: number, singular: string, pluralForm?: string) {
  return `${fmtNumber(n)} ${n === 1 ? singular : pluralForm ?? `${singular}s`}`;
}
