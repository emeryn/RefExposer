import { Badge, Button, Code, Group, Menu, Text, Tooltip } from '@mantine/core';
import { IconCheck, IconClock, IconDownload, IconFileZip, IconHistory } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { absoluteUrl, api, downloadUrl } from '../api/client';
import type { DownloadFormat } from '../api/types';
import { fmtBytes } from '../lib/format';
import { CopyIcon } from './Common';

export function FormatStatus({ f }: { f: DownloadFormat }) {
  if (f.unavailable_reason) return <Text size="xs" c="dimmed">{f.unavailable_reason}</Text>;
  if (f.ready)
    return (
      <Tooltip label={f.sha256 ? <>SHA-256 : <Code>{f.sha256}</Code></> : 'Ready'} multiline maw={560}>
        <Group gap={4} wrap="nowrap">
          <IconCheck size={12} color="var(--mantine-color-teal-6)" />
          <Text size="xs" c="dimmed">
            {fmtBytes(f.size)}
          </Text>
        </Group>
      </Tooltip>
    );
  return (
    <Tooltip label="Generated on first request, then kept until the next version">
      <Group gap={4} wrap="nowrap">
        <IconClock size={12} color="var(--mantine-color-dimmed)" />
        <Text size="xs" c="dimmed">
          on demand
        </Text>
      </Group>
    </Tooltip>
  );
}

/** "Download" button of the referential header. */
export function DownloadMenu({ refId, size = 'sm' }: { refId: string; size?: 'xs' | 'sm' }) {
  const { data, refetch } = useQuery({ queryKey: ['downloads', refId], queryFn: () => api.downloads(refId), enabled: false });
  return (
    <Menu position="bottom-end" width={340} shadow="md" onOpen={() => refetch()}>
      <Menu.Target>
        <Button variant="default" size={size} leftSection={<IconDownload size={16} />}>
          Download
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Full referential{data?.version ? ` · version ${data.version}` : ''}</Menu.Label>
        {(data?.formats ?? []).map((f) => (
          <Menu.Item
            key={f.format}
            component="a"
            href={downloadUrl(refId, f.format)}
            disabled={!!f.unavailable_reason}
            rightSection={<FormatStatus f={f} />}
          >
            {f.label}
          </Menu.Item>
        ))}
        {!data && <Menu.Item disabled>Loading…</Menu.Item>}
        <Menu.Divider />
        <Menu.Item
          component="a"
          href={downloadUrl(refId, 'source')}
          leftSection={<IconFileZip size={14} />}
          disabled={!data?.sources.length}
          rightSection={
            data?.sources.length ? (
              <Text size="xs" c="dimmed">
                {data.sources.length > 1 ? `${data.sources.length} files (zip)` : fmtBytes(data.sources[0].size)}
              </Text>
            ) : null
          }
        >
          {data?.raw_url ? `Raw file (${data.sources[0]?.name ?? '…'})` : 'Original source files'}
        </Menu.Item>
        <Menu.Item component="a" href={downloadUrl(refId, 'previous')} leftSection={<IconHistory size={14} />} disabled={!data?.has_previous}>
          Previous version{data?.raw_url ? '' : ' (Parquet)'}
        </Menu.Item>
        <Menu.Divider />
        {data?.raw_url ? (
          <>
            <Group px="sm" py={4} gap={4} wrap="nowrap">
              <Text size="xs" c="dimmed" style={{ flex: 1 }}>
                Stable link for tools: <Code fz={10}>/raw</Code>
              </Text>
              <CopyIcon value={absoluteUrl(data.raw_url)} label="Copy the raw file link" />
            </Group>
            <Group px="sm" py={4} gap={4} wrap="nowrap">
              <Code fz={10} style={{ flex: 1, wordBreak: 'break-all' }}>
                {`curl -fsS -u x:$REFEX_TOKEN -z ${data.sources[0]?.name ?? 'file'} -o ${data.sources[0]?.name ?? 'file'} ${absoluteUrl(data.raw_url)}`}
              </Code>
              <CopyIcon
                value={`curl -fsS -u x:$REFEX_TOKEN -z ${data.sources[0]?.name ?? 'file'} -o ${data.sources[0]?.name ?? 'file'} ${absoluteUrl(data.raw_url)}`}
                label="Copy the curl command (downloads only when changed)"
              />
            </Group>
          </>
        ) : (
          <Group px="sm" py={4} gap={4} wrap="nowrap">
            <Text size="xs" c="dimmed" style={{ flex: 1 }}>
              Stable link: <Code fz={10}>/download/csv</Code>
            </Text>
            <CopyIcon value={absoluteUrl(downloadUrl(refId, 'csv'))} label="Copy the CSV link" />
          </Group>
        )}
      </Menu.Dropdown>
    </Menu>
  );
}

export function PrebuiltBadge() {
  return (
    <Badge size="xs" variant="outline" color="gray">
      prebuilt
    </Badge>
  );
}
