import { Anchor, Code, Table, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { CodeSnippet } from './Common';

/**
 * Minimal Markdown renderer for text written by a model.
 *
 * It builds React elements and never injects HTML, so nothing in the text can become markup, a script or a
 * remote request. Images are not rendered at all: an image URL in an answer would send the data around it to
 * whoever serves that URL. Links are kept, with their real target shown and no referrer.
 */
export function Markdown({ text }: { text: string }) {
  return <>{blocks(text)}</>;
}

function blocks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];

    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(<CodeSnippet key={key++} code={code.join('\n')} />);
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const order = Math.min(heading[1].length + 2, 6) as 3 | 4 | 5 | 6;
      out.push(
        <Title key={key++} order={order} mt="sm">
          {inline(heading[2])}
        </Title>,
      );
      i += 1;
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] ?? '')) {
      const rows: string[][] = [];
      const header = cells(line);
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      out.push(
        <Table key={key++} striped withTableBorder highlightOnHover my="xs">
          <Table.Thead>
            <Table.Tr>
              {header.map((c, n) => (
                <Table.Th key={n}>{inline(c)}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, n) => (
              <Table.Tr key={n}>
                {row.map((c, m) => (
                  <Table.Td key={m}>{inline(c)}</Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>,
      );
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const items: string[] = [];
      const ordered = /^\s*\d+\.\s+/.test(line);
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''));
        i += 1;
      }
      const List = ordered ? 'ol' : 'ul';
      out.push(
        <List key={key++} style={{ margin: '4px 0', paddingLeft: 22 }}>
          {items.map((item, n) => (
            <li key={n}>
              <Text component="span" size="sm">
                {inline(item)}
              </Text>
            </li>
          ))}
        </List>,
      );
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const paragraph: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*+]|\d+\.)\s)/.test(lines[i])) {
      paragraph.push(lines[i]);
      i += 1;
    }
    out.push(
      <Text key={key++} size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {inline(paragraph.join('\n'))}
      </Text>,
    );
  }
  return out;
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim());
}

/** Inline spans: `code`, **bold**, *italic* and links. Everything else stays literal text. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('`')) {
      out.push(<Code key={key++}>{token.slice(1, -1)}</Code>);
    } else if (token.startsWith('**')) {
      out.push(<b key={key++}>{token.slice(2, -2)}</b>);
    } else if (token.startsWith('[')) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token) ?? [];
      // Only ordinary web links and links inside the application: no javascript:, no data:
      out.push(
        href && /^(https?:\/\/|\/)/.test(href) ? (
          <Anchor key={key++} href={href} target="_blank" rel="noopener noreferrer nofollow">
            {label}
          </Anchor>
        ) : (
          <span key={key++}>{label}</span>
        ),
      );
    } else {
      out.push(<i key={key++}>{token.slice(1, -1)}</i>);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
