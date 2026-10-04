import { Alert, Button, Checkbox, Code, Group, List, Modal, Stack, Text } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import '@mantine/dropzone/styles.css';
import { notifications } from '@mantine/notifications';
import { IconFile, IconFileImport, IconFolder, IconUpload, IconX } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { ReferentialDetail } from '../api/types';
import { fmtBytes } from '../lib/format';

/** Manual import of a new version from files (upload), for users with the manage right. */
export function ImportButton({ r, size = 'sm' }: { r: ReferentialDetail; size?: 'xs' | 'sm' }) {
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [pin, setPin] = useState(false);
  const urls = r.source_type === 'http' ? r.source_urls : [];
  const expected = urls.length > 1 ? urls.map((u) => u.split('?')[0].split('/').pop()!.replace(/\.(gz|zip)$/i, '')) : [];

  const run = useMutation({
    mutationFn: () => api.importFiles(r.id, files, pin),
    onSuccess: () => {
      notifications.show({ title: 'Import started', message: `${files.length} file(s) sent to ${r.name}`, color: 'blue' });
      qc.invalidateQueries({ queryKey: ['referential', r.id] });
      qc.invalidateQueries({ queryKey: ['referentials'] });
      setOpened(false);
      setFiles([]);
    },
    onError: (e: Error) => notifications.show({ title: 'Import refused', message: e.message, color: 'red' }),
  });

  return (
    <>
      <Button variant="default" size={size} leftSection={<IconFileImport size={16} />} onClick={() => setOpened(true)} disabled={!!r.current_run}>
        Import
      </Button>
      <Modal opened={opened} onClose={() => setOpened(false)} size="lg" title={<Text fw={700}>Manual import — {r.name}</Text>}>
        <Stack>
          <Text size="sm" c="dimmed">
            The files you send become the new version of the referential, instead of its usual source. They go through the same checks
            (format, empty or corrupted file, validation rules): if they are refused, the current version is kept.
          </Text>
          {expected.length > 0 && (
            <Alert color="indigo" variant="light" p="sm">
              <Text size="sm">This referential combines {expected.length} sources: send one file per source, named:</Text>
              <List size="sm" mt={4}>
                {expected.map((e) => (
                  <List.Item key={e}>
                    <Code>{e}</Code> (compressed .gz / .zip accepted)
                  </List.Item>
                ))}
              </List>
            </Alert>
          )}
          <Dropzone onDrop={(f) => setFiles(f)} multiple maxSize={2 * 1024 ** 3}>
            <Group justify="center" gap="lg" mih={120} style={{ pointerEvents: 'none' }}>
              <Dropzone.Accept>
                <IconUpload size={40} color="var(--mantine-color-indigo-6)" />
              </Dropzone.Accept>
              <Dropzone.Reject>
                <IconX size={40} color="var(--mantine-color-red-6)" />
              </Dropzone.Reject>
              <Dropzone.Idle>
                <IconFile size={40} color="var(--mantine-color-dimmed)" />
              </Dropzone.Idle>
              <div>
                <Text size="md">Drop the file(s) here or click to choose</Text>
                <Text size="xs" c="dimmed" mt={4}>
                  Format expected: {r.format.toUpperCase()} — compressed files (.gz, .zip) are accepted. 2 GB maximum.
                </Text>
              </div>
            </Group>
          </Dropzone>
          {files.length > 0 && (
            <Stack gap={2}>
              {files.map((f) => (
                <Text key={f.name} size="sm">
                  {f.name} — {fmtBytes(f.size)}
                </Text>
              ))}
            </Stack>
          )}
          {r.source_type === 'http' && (
            <Checkbox
              checked={pin}
              onChange={(e) => setPin(e.currentTarget.checked)}
              label="Freeze this version"
              description="Scheduled updates from the remote source are suspended until you resume them (e.g. when the source is unavailable or wrong)."
            />
          )}
          {r.import_folder && (
            <Alert color="gray" variant="light" p="sm" icon={<IconFolder size={18} />}>
              <Text size="sm">
                Files can also be dropped in the import folder <Code>{r.import_folder}</Code>: they are imported automatically once copied.
              </Text>
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpened(false)}>
              Cancel
            </Button>
            <Button leftSection={<IconFileImport size={16} />} onClick={() => run.mutate()} disabled={!files.length} loading={run.isPending}>
              Import
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
