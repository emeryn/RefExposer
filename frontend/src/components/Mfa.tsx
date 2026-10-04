import { Alert, Button, Code, Group, Image, PinInput, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import { useState } from 'react';
import type { MfaEnrolment } from '../api/types';
import { CopyIcon } from './Common';

/** 6-digit code field; a recovery code (xxxx-xxxx) can be typed instead when `allowRecovery`. */
export function CodeInput({ onSubmit, loading, allowRecovery = false }: { onSubmit: (code: string) => void; loading?: boolean; allowRecovery?: boolean }) {
  const [code, setCode] = useState('');
  const [recovery, setRecovery] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState('');
  return (
    <Stack gap="sm">
      {recovery ? (
        <PinInput
          length={9}
          type={/^[a-z0-9-]*$/}
          placeholder="·"
          value={recoveryCode}
          onChange={(v) => setRecoveryCode(v.toLowerCase())}
          size="sm"
          aria-label="Recovery code"
        />
      ) : (
        <PinInput
          length={6}
          type="number"
          oneTimeCode
          autoFocus
          value={code}
          onChange={setCode}
          onComplete={(v) => onSubmit(v)}
          size="md"
          aria-label="Code of the authenticator app"
        />
      )}
      <Button loading={loading} disabled={recovery ? recoveryCode.length < 9 : code.length < 6} onClick={() => onSubmit(recovery ? recoveryCode : code)}>
        Verify
      </Button>
      {allowRecovery && (
        <Button variant="subtle" size="compact-sm" onClick={() => setRecovery((r) => !r)}>
          {recovery ? 'Use the authenticator app' : 'Use a recovery code'}
        </Button>
      )}
    </Stack>
  );
}

/** QR code and secret of an enrolment, then the first code. */
export function Enrolment({ enrolment, onCode, loading }: { enrolment: MfaEnrolment; onCode: (code: string) => void; loading?: boolean }) {
  return (
    <Stack gap="sm">
      <Text size="sm">
        Scan this QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, FreeOTP, 1Password…), then type
        the 6-digit code it shows.
      </Text>
      <Group justify="center">
        <Image src={enrolment.qr_svg} w={200} h={200} alt="QR code of the second factor" style={{ background: 'white', borderRadius: 8 }} />
      </Group>
      <Group gap={4} justify="center" wrap="nowrap">
        <Text size="xs" c="dimmed">
          Or type this key:
        </Text>
        <Code fz={11}>{enrolment.secret.replace(/(.{4})/g, '$1 ').trim()}</Code>
        <CopyIcon value={enrolment.secret} label="Copy the key" />
      </Group>
      <CodeInput onSubmit={onCode} loading={loading} />
    </Stack>
  );
}

/** Recovery codes, shown once. */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const download = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([`RefExposer recovery codes (one use each)\n\n${codes.join('\n')}\n`], { type: 'text/plain' }));
    a.download = 'refexposer-recovery-codes.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <Stack gap="sm">
      <Alert color="orange" title="Keep these recovery codes">
        Each code signs you in once if you lose your phone. They will not be shown again.
      </Alert>
      <SimpleGrid cols={2} spacing={4}>
        {codes.map((c) => (
          <Code key={c} ta="center" fz="sm">
            {c}
          </Code>
        ))}
      </SimpleGrid>
      <Group grow>
        <Button variant="default" leftSection={<IconDownload size={16} />} onClick={download}>
          Download
        </Button>
        <Button onClick={onDone}>I have saved them</Button>
      </Group>
    </Stack>
  );
}
