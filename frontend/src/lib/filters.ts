export const RESERVED = new Set(['q', 'sort', 'limit', 'offset', 'columns', 'format', 'count']);

export const OPERATORS: Record<string, { label: string; symbol: string; noValue?: boolean; hint?: string }> = {
  eq: { label: 'equals', symbol: '=' },
  ne: { label: 'differs from', symbol: '≠' },
  gt: { label: 'greater than', symbol: '>' },
  gte: { label: 'greater than or equal to', symbol: '≥' },
  lt: { label: 'less than', symbol: '<' },
  lte: { label: 'less than or equal to', symbol: '≤' },
  contains: { label: 'contains', symbol: '∋' },
  ncontains: { label: 'does not contain', symbol: '∌' },
  startswith: { label: 'starts with', symbol: 'starts with' },
  endswith: { label: 'ends with', symbol: 'ends with' },
  in: { label: 'one of', symbol: '∈', hint: 'comma separated values' },
  nin: { label: 'none of', symbol: '∉', hint: 'comma separated values' },
  isnull: { label: 'is empty', symbol: 'empty', noValue: true },
  regex: { label: 'matches the regular expression', symbol: '~' },
};

export type Kind = 'text' | 'number' | 'temporal' | 'boolean' | 'list' | 'struct';

export function typeKind(t: string): Kind {
  const u = t.toUpperCase();
  if (u.endsWith(']')) return 'list';
  if (/^(STRUCT|MAP|UNION|JSON)/.test(u)) return 'struct';
  if (/^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|U\w*INT|FLOAT|DOUBLE|DECIMAL)/.test(u)) return 'number';
  if (/^(DATE|TIME|TIMESTAMP|INTERVAL)/.test(u)) return 'temporal';
  if (u === 'BOOLEAN') return 'boolean';
  return 'text';
}

export const OPS_BY_KIND: Record<Kind, string[]> = {
  text: ['contains', 'eq', 'ne', 'startswith', 'endswith', 'in', 'nin', 'ncontains', 'isnull', 'regex'],
  number: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'nin', 'isnull'],
  temporal: ['gte', 'lte', 'gt', 'lt', 'eq', 'ne', 'isnull'],
  boolean: ['eq', 'isnull'],
  list: ['contains', 'eq', 'ncontains', 'in', 'nin', 'isnull'],
  struct: ['contains', 'ncontains', 'isnull'],
};

export interface ActiveFilter {
  param: string;
  column: string;
  op: string;
  value: string;
}

export function readFilters(params: URLSearchParams): ActiveFilter[] {
  const out: ActiveFilter[] = [];
  params.forEach((value, param) => {
    if (RESERVED.has(param)) return;
    let column = param;
    let op = 'eq';
    const idx = param.lastIndexOf('__');
    if (idx > 0 && OPERATORS[param.slice(idx + 2)]) {
      column = param.slice(0, idx);
      op = param.slice(idx + 2);
    }
    out.push({ param, column, op, value });
  });
  return out;
}

export function filterParam(column: string, op: string) {
  // Columns named like a reserved parameter (count, sort...) need the explicit __eq suffix
  return op === 'eq' && !RESERVED.has(column) ? column : `${column}__${op}`;
}
