import { ActionIcon, Alert, Badge, Button, Card, Divider, Group, Modal, Stack, Table, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDeviceMobile, IconKey, IconShieldLock, IconTrash } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { MfaEnrolment } from '../api/types';
import { fmtDate, fmtRelative } from '../lib/format';
import { createCredential, webauthnSupported } from '../lib/webauthn';
import { CodeInput, Enrolment, RecoveryCodes } from './Mfa';

type Step =
  | { kind: 'enrol'; enrolment: MfaEnrolment }
  | { kind: 'disable' }
  | { kind: 'codes' }
  | { kind: 'key' }
  | { kind: 'show'; codes: string[] };

/** "My account": second factors of local and LDAP accounts (authenticator app, security keys / passkeys). */
export default function TwoFactorCard() {
  const qc = useQueryClient();
  const { data: status } = useQuery({ queryKey: ['mfa'], queryFn: api.mfaStatus });
  const [step, setStep] = useState<Step | null>(null);
  const [keyName, setKeyName] = useState('');
  const [loading, setLoading] = useState(false);

  const act = async (fn: () => Promise<void>) => {
    setLoading(true);
    try {
      await fn();
      qc.invalidateQueries({ queryKey: ['mfa'] });
      qc.invalidateQueries({ queryKey: ['me'] });
    } catch (e) {
      notifications.show({ title: 'Failed', message: (e as Error).message, color: 'red' });
    } finally {
      setLoading(false);
    }
  };
  // New recovery codes are only returned with the first second factor
  const showCodes = (codes: string[]) => setStep(codes.length ? { kind: 'show', codes } : null);
  if (!status) return null;
  const canKey = !!status.webauthn_available && webauthnSupported();
  const lastFactor = status.required && (status.totp ? 1 : 0) + status.webauthn.length <= 1;

  return (
    <Card>
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <IconShieldLock size={18} />
          <Title order={5}>Two-factor authentication</Title>
          <Badge color={status.enabled ? 'teal' : 'gray'} variant="light">
            {status.enabled ? 'enabled' : 'disabled'}
          </Badge>
          {status.required && (
            <Badge color="orange" variant="light">
              required
            </Badge>
          )}
        </Group>
        {status.enabled && (
          <Button size="xs" variant="default" onClick={() => setStep({ kind: 'codes' })}>
            New recovery codes ({status.recovery_codes_left} left)
          </Button>
        )}
      </Group>
      <Text size="sm" c="dimmed" mb="sm">
        After your password, confirm your sign-in with an authenticator app or a security key / passkey (YubiKey, Windows Hello,
        Touch ID, phone…). You can use several.
      </Text>

      <Group justify="space-between">
        <Group gap="xs">
          <IconDeviceMobile size={16} />
          <Text size="sm" fw={500}>
            Authenticator app
          </Text>
          {status.totp && (
            <Text size="xs" c="dimmed">
              since {fmtDate(status.enabled_at)}
            </Text>
          )}
        </Group>
        {status.totp ? (
          <Button size="xs" color="red" variant="light" disabled={lastFactor} onClick={() => setStep({ kind: 'disable' })}>
            Disable
          </Button>
        ) : (
          <Button size="xs" loading={loading} onClick={() => act(async () => setStep({ kind: 'enrol', enrolment: await api.mfaEnroll() }))}>
            Set up
          </Button>
        )}
      </Group>

      <Divider my="sm" />
      <Group justify="space-between" mb={4}>
        <Group gap="xs">
          <IconKey size={16} />
          <Text size="sm" fw={500}>
            Security keys and passkeys
          </Text>
        </Group>
        <Button size="xs" variant="light" disabled={!canKey} onClick={() => setStep({ kind: 'key' })}>
          Add
        </Button>
      </Group>
      {!canKey && (
        <Text size="xs" c="dimmed">
          Available over HTTPS (or on localhost), at the address set in REFEX_PUBLIC_URL.
        </Text>
      )}
      {status.webauthn.length > 0 && (
        <Table fz="sm" verticalSpacing={4}>
          <Table.Tbody>
            {status.webauthn.map((k) => (
              <Table.Tr key={k.id}>
                <Table.Td>{k.name}</Table.Td>
                <Table.Td c="dimmed">added {fmtDate(k.created_at)}</Table.Td>
                <Table.Td c="dimmed">{k.last_used_at ? `used ${fmtRelative(k.last_used_at)}` : 'never used'}</Table.Td>
                <Table.Td w={40}>
                  <Tooltip label={lastFactor ? 'Required: add another factor first' : 'Remove'}>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      disabled={lastFactor}
                      onClick={() => act(async () => void (await api.mfaWebauthnDelete(k.id)))}
                      aria-label="Remove"
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Tooltip>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={!!step} onClose={() => setStep(null)} title={<Text fw={700}>Two-factor authentication</Text>}>
        {step?.kind === 'enrol' && (
          <Enrolment enrolment={step.enrolment} loading={loading} onCode={(code) => act(async () => showCodes((await api.mfaEnable(code)).recovery_codes))} />
        )}
        {step?.kind === 'key' && (
          <Stack gap="sm">
            <TextInput label="Name" placeholder="YubiKey 5, laptop, phone…" value={keyName} onChange={(e) => setKeyName(e.currentTarget.value)} />
            <Text size="xs" c="dimmed">
              Your browser asks you to touch the key, or to confirm with your fingerprint, face or PIN.
            </Text>
            <Button
              leftSection={<IconKey size={16} />}
              loading={loading}
              onClick={() =>
                act(async () => {
                  const o = await api.mfaWebauthnRegisterOptions();
                  const res = await api.mfaWebauthnRegister(o.state, await createCredential(o.options), keyName || 'Security key');
                  setKeyName('');
                  showCodes(res.recovery_codes);
                })
              }
            >
              Register the key
            </Button>
          </Stack>
        )}
        {step?.kind === 'disable' && (
          <Stack gap="sm">
            <Text size="sm">Type a code of your authenticator app (or a recovery code) to disable it.</Text>
            <CodeInput allowRecovery loading={loading} onSubmit={(code) => act(async () => { await api.mfaDisable(code); setStep(null); })} />
          </Stack>
        )}
        {step?.kind === 'codes' && (
          <Stack gap="sm">
            {status.totp ? (
              <Text size="sm">Type a code of your authenticator app: the current recovery codes stop working.</Text>
            ) : (
              <Alert color="gray" p="xs">
                <Text size="xs">Type one of your current recovery codes: they are replaced by new ones.</Text>
              </Alert>
            )}
            <CodeInput
              allowRecovery={!status.totp}
              loading={loading}
              onSubmit={(code) => act(async () => setStep({ kind: 'show', codes: (await api.mfaRecoveryCodes(code)).recovery_codes }))}
            />
          </Stack>
        )}
        {step?.kind === 'show' && <RecoveryCodes codes={step.codes} onDone={() => setStep(null)} />}
      </Modal>
    </Card>
  );
}
