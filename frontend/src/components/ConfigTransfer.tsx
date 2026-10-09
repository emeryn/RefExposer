import { Alert, Badge, Button, Checkbox, Code, Group, Modal, MultiSelect, SegmentedControl, Stack, Switch, Table, Text, Tooltip } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconCheck, IconFileImport, IconKey, IconUsersGroup } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { ConfigImportResult, Definition } from '../api/types';

const ACTION_COLORS: Record<string, string> = {
  create: 'teal',
  update: 'indigo',
  unchanged: 'gray',
  skip: 'gray',
  conflict: 'orange',
  error: 'red',
};

/** Export of the definitions (YAML / JSON), with the rights and the names of the secrets they need. */
export function ExportModal({ opened, onClose, definitions }: { opened: boolean; onClose: () => void; definitions: Definition[] }) {
  const exportable = definitions.filter((d) => d.origin !== 'sync');
  const [ids, setIds] = useState<string[]>([]);
  const [grants, setGrants] = useState(true);
  const [credentials, setCredentials] = useState<'masked' | 'included'>('masked');
  const [format, setFormat] = useState<'yaml' | 'json'>('yaml');
  const run = useMutation({
    mutationFn: () => api.exportConfig({ ids, grants, credentials, format }),
    onSuccess: () => onClose(),
    onError: (e: Error) => notifications.show({ title: 'Export failed', message: e.message, color: 'red' }),
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={700}>Export the configuration</Text>} size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          A file to set up another environment: the definitions (interface, internal referentials without their rows, YAML files of{' '}
          <Code>config/</Code>), the rights of the groups and the names of the secrets they use. Secret values never leave the instance.
          The file can also be dropped into <Code>config/</Code>.
        </Text>
        <MultiSelect
          label="Referentials"
          description="All when empty (the referentials of the sync folder are left out)"
          data={exportable.map((d) => ({ value: d.id, label: `${d.config.name} (${d.id})` }))}
          value={ids}
          onChange={setIds}
          searchable
          clearable
        />
        <Switch label="Rights of the groups and users" checked={grants} onChange={(e) => setGrants(e.currentTarget.checked)} />
        <div>
          <Text size="sm" fw={500} mb={4}>
            Literal credentials of the definitions (not stored as secrets)
          </Text>
          <SegmentedControl
            value={credentials}
            onChange={(v) => setCredentials(v as 'masked' | 'included')}
            data={[
              { value: 'masked', label: 'Masked (recommended)' },
              { value: 'included', label: 'Included in clear' },
            ]}
          />
          <Text size="xs" c={credentials === 'included' ? 'orange' : 'dimmed'} mt={4}>
            {credentials === 'included'
              ? 'The file will contain these credentials in clear: keep it like a password, or move them to the secret manager.'
              : 'Masked values keep the value of the target when the referential exists there; otherwise enter them after the import.'}
          </Text>
        </div>
        <Group justify="space-between">
          <SegmentedControl size="xs" value={format} onChange={(v) => setFormat(v as 'yaml' | 'json')} data={['yaml', 'json']} />
          <Button onClick={() => run.mutate()} loading={run.isPending}>
            Download
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Import: the plan first (what would be created, updated, refused), then its application. */
export function ImportModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [content, setContent] = useState<string | null>(null);
  const [fileName, setFileName] = useState('');
  const [onExisting, setOnExisting] = useState<'skip' | 'update'>('skip');
  const [grants, setGrants] = useState(true);
  const [createGroups, setCreateGroups] = useState(true);
  const [pull, setPull] = useState(false);
  const [plan, setPlan] = useState<ConfigImportResult | null>(null);
  const [selected, setSelected] = useState<string[]>([]);

  const body = (apply: boolean) => ({
    content: content ?? '',
    apply,
    on_existing: onExisting,
    grants,
    create_groups: createGroups,
    pull,
    ids: apply ? selected : null,
  });
  const analyse = useMutation({
    mutationFn: () => api.importConfig(body(false)),
    onSuccess: (res) => {
      setPlan(res);
      setSelected(res.plan.filter((p) => p.action === 'create' || p.action === 'update').map((p) => p.id!));
    },
    onError: (e: Error) => notifications.show({ title: 'Unreadable file', message: e.message, color: 'red' }),
  });
  const apply = useMutation({
    mutationFn: () => api.importConfig(body(true)),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['definitions'] });
      qc.invalidateQueries({ queryKey: ['referentials'] });
      qc.invalidateQueries({ queryKey: ['admin-groups'] });
      notifications.show({
        title: 'Configuration imported',
        message: `${res.created?.length ?? 0} created, ${res.updated?.length ?? 0} updated, ${res.grants_changed ?? 0} right(s)` +
          (res.errors?.length ? `, ${res.errors.length} error(s)` : ''),
        color: res.errors?.length ? 'orange' : 'teal',
      });
      setPlan(res);
    },
    onError: (e: Error) => notifications.show({ title: 'Import failed', message: e.message, color: 'red' }),
  });
  const reset = () => {
    setContent(null);
    setPlan(null);
    setSelected([]);
  };
  const close = () => {
    reset();
    onClose();
  };
  const selectable = (a: string) => a === 'create' || a === 'update';

  return (
    <Modal opened={opened} onClose={close} title={<Text fw={700}>Import a configuration</Text>} size="xl">
      <Stack>
        {!content ? (
          <Dropzone
            onDrop={async (files) => {
              setFileName(files[0].name);
              setContent(await files[0].text());
            }}
            multiple={false}
            accept={['application/x-yaml', 'application/yaml', 'text/yaml', 'application/json', 'text/plain', '.yml', '.yaml', '.json']}
          >
            <Group justify="center" mih={110} style={{ pointerEvents: 'none' }}>
              <IconFileImport size={36} color="var(--mantine-color-dimmed)" />
              <div>
                <Text>Drop an export (YAML or JSON) or a file of config/</Text>
                <Text size="xs" c="dimmed">
                  Nothing is changed before you confirm the plan.
                </Text>
              </div>
            </Group>
          </Dropzone>
        ) : (
          <Group justify="space-between">
            <Text size="sm">
              File: <b>{fileName}</b>
              {plan?.export?.instance && (
                <Text span c="dimmed">
                  {' '}· exported from {plan.export.instance} on {plan.export.exported_at?.slice(0, 16).replace('T', ' ')} by {plan.export.exported_by}
                </Text>
              )}
            </Text>
            <Button size="compact-sm" variant="subtle" onClick={reset}>
              Another file
            </Button>
          </Group>
        )}
        {content && (
          <Group>
            <SegmentedControl
              size="xs"
              value={onExisting}
              onChange={(v) => {
                setOnExisting(v as 'skip' | 'update');
                setPlan(null);
              }}
              data={[
                { value: 'skip', label: 'Keep the existing referentials' },
                { value: 'update', label: 'Update the existing referentials' },
              ]}
            />
            <Switch size="xs" label="Rights" checked={grants} onChange={(e) => setGrants(e.currentTarget.checked)} />
            <Switch size="xs" label="Create the missing groups" checked={createGroups} disabled={!grants} onChange={(e) => setCreateGroups(e.currentTarget.checked)} />
            <Switch size="xs" label="Start the imports" checked={pull} onChange={(e) => setPull(e.currentTarget.checked)} />
          </Group>
        )}
        {content && !plan && (
          <Button onClick={() => analyse.mutate()} loading={analyse.isPending} w="fit-content">
            Analyse
          </Button>
        )}
        {plan && (
          <>
            {plan.missing_secrets.length > 0 && (
              <Alert color="orange" icon={<IconKey size={16} />} p="sm">
                <Text size="sm">
                  Secrets to create first in <Link to="/admin/secrets" target="_blank">Secrets</Link> (same names), then analyse again:
                </Text>
                {plan.missing_secrets.map((s) => (
                  <Text key={s.name} size="xs">
                    <Code>{s.name}</Code> {s.description ? `— ${s.description}` : ''} {s.hosts.length ? `(hosts: ${s.hosts.join(', ')})` : ''} · used by{' '}
                    {s.used_by.join(', ')}
                  </Text>
                ))}
              </Alert>
            )}
            {plan.missing_groups.length > 0 && grants && (
              <Alert color={createGroups ? 'blue' : 'orange'} icon={<IconUsersGroup size={16} />} p="sm">
                <Text size="sm">
                  Groups {createGroups ? 'created' : 'missing (their rights are left out)'}: {plan.missing_groups.join(', ')}. Add their members
                  afterwards (or let LDAP / OpenID Connect synchronize them).
                </Text>
              </Alert>
            )}
            <Table striped fz="sm" verticalSpacing={4}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={30} />
                  <Table.Th>Referential</Table.Th>
                  <Table.Th>Action</Table.Th>
                  <Table.Th>Detail</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {plan.plan.map((p, i) => (
                  <Table.Tr key={`${p.id}-${i}`}>
                    <Table.Td>
                      <Checkbox
                        disabled={!selectable(p.action) || plan.applied}
                        checked={selected.includes(p.id ?? '')}
                        onChange={(e) => {
                          const checked = e.currentTarget.checked;
                          setSelected((s) => (checked ? [...s, p.id!] : s.filter((x) => x !== p.id)));
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" fw={500}>
                        {p.name ?? p.id}
                      </Text>
                      <Text size="xs" c="dimmed" ff="monospace">
                        {p.id} · {p.type}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={ACTION_COLORS[p.action] ?? 'gray'} variant="light">
                        {p.action}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" c={p.action === 'error' ? 'red' : 'dimmed'}>
                        {p.reason ?? (p.changed ? `changed: ${p.changed.join(', ')}` : '')}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {plan.applied ? (
              <Alert color={plan.errors?.length ? 'orange' : 'teal'} icon={plan.errors?.length ? <IconAlertTriangle size={16} /> : <IconCheck size={16} />}>
                <Text size="sm">
                  {plan.created?.length ?? 0} created, {plan.updated?.length ?? 0} updated, {plan.grants_changed ?? 0} right(s) set
                  {plan.groups_created?.length ? `, groups created: ${plan.groups_created.join(', ')}` : ''}
                  {plan.runs ? `, ${plan.runs} import(s) started` : ''}.
                </Text>
                {plan.errors?.map((e) => (
                  <Text key={e.id} size="xs" c="red">
                    {e.id}: {e.error}
                  </Text>
                ))}
              </Alert>
            ) : (
              <Group justify="space-between">
                <Button variant="subtle" onClick={() => analyse.mutate()} loading={analyse.isPending}>
                  Analyse again
                </Button>
                <Tooltip label="Also applies the rights of the referentials already present" disabled={!grants}>
                  <Button onClick={() => apply.mutate()} loading={apply.isPending} disabled={!selected.length && !(grants && plan.grants > 0)}>
                    Import {selected.length} referential(s)
                  </Button>
                </Tooltip>
              </Group>
            )}
          </>
        )}
      </Stack>
    </Modal>
  );
}
