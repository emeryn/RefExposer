import { Alert, Box, Button, Center, Divider, Paper, PasswordInput, SegmentedControl, Stack, Text, TextInput } from '@mantine/core';
import { BrandHero } from '../components/Brand';
import { IconAlertCircle, IconKey, IconLock, IconUser } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { MfaChallenge, MfaEnrolment, Me } from '../api/types';
import { CodeInput, Enrolment, RecoveryCodes } from '../components/Mfa';
import { createCredential, getAssertion, webauthnSupported } from '../lib/webauthn';
import { useAuth } from './AuthContext';

/** Error sent back by the OpenID Connect callback (?auth_error=...), read once then removed from the URL. */
function takeAuthError(): string | null {
  const params = new URLSearchParams(window.location.search);
  const err = params.get('auth_error');
  if (err) {
    params.delete('auth_error');
    const qs = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}`);
  }
  return err;
}

export default function LoginPage() {
  const { setMe } = useAuth();
  const { data: providers } = useQuery({ queryKey: ['providers'], queryFn: api.providers, staleTime: 60_000 });
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(() => takeAuthError());
  const [loading, setLoading] = useState(false);
  // Second factor: challenge after the password, enrolment when it is required, recovery codes to keep
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [enrolment, setEnrolment] = useState<MfaEnrolment | null>(null);
  const [pending, setPending] = useState<{ me: Me; codes: string[] } | null>(null);

  const attempt = async (fn: () => Promise<void>) => {
    setLoading(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
      if ((err as { status?: number }).status === 401 && challenge && /expired/.test((err as Error).message)) setChallenge(null);
    } finally {
      setLoading(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    attempt(async () => {
      try {
        const res = await api.login(username, password);
        if ('mfa' in res && 'mfa_token' in res) {
          setChallenge(res);
          if (res.mfa === 'setup') setEnrolment(await api.mfaSetup(res.mfa_token));
        } else setMe(res as Me);
      } finally {
        setPassword('');
      }
    });
  };
  const [setupMethod, setSetupMethod] = useState<'totp' | 'webauthn'>('totp');
  const canKey = !!challenge?.webauthn_available && webauthnSupported();
  const useKey = () =>
    attempt(async () => {
      const o = await api.mfaWebauthnOptions(challenge!.mfa_token);
      setMe(await api.mfaWebauthnVerify(challenge!.mfa_token, o.state, await getAssertion(o.options)));
    });
  const registerKey = () =>
    attempt(async () => {
      const o = await api.mfaSetupWebauthnOptions(challenge!.mfa_token);
      const res = await api.mfaSetupWebauthnConfirm(challenge!.mfa_token, o.state, await createCredential(o.options), 'Security key');
      setPending({ me: res, codes: res.recovery_codes });
    });
  const verify = (code: string) => attempt(async () => setMe(await api.mfaVerify(challenge!.mfa_token, code)));
  const confirm = (code: string) =>
    attempt(async () => {
      const res = await api.mfaSetupConfirm(challenge!.mfa_token, code);
      setPending({ me: res, codes: res.recovery_codes });
    });

  const next = `${window.location.pathname}${window.location.search}`;
  // Username / password form: local accounts and / or the LDAP directory
  const showForm = !providers || providers.local || providers.ldap.enabled;
  const oidcUrl = providers?.oidc.enabled ? `${providers.oidc.login_url}?next=${encodeURIComponent(next)}` : null;

  return (
    <Center mih="100vh" p="md" style={{ background: 'var(--app-subtle-bg)' }}>
      <Box w="100%" maw={400}>
        <BrandHero caption="Sign in to access the referentials" />
        <Paper withBorder shadow="sm" p="xl" radius="md">
          <Stack>
            {error && (
              <Alert color="red" icon={<IconAlertCircle size={18} />} p="sm">
                {error}
              </Alert>
            )}
            {pending ? (
              <RecoveryCodes codes={pending.codes} onDone={() => setMe(pending.me)} />
            ) : challenge ? (
              <Stack gap="sm">
                <Text fw={600}>{challenge.mfa === 'setup' ? 'Set up the second factor' : 'Second factor'}</Text>
                {challenge.mfa === 'setup' ? (
                  <>
                    <Text size="sm" c="dimmed">
                      A second factor is required for your account.
                    </Text>
                    {canKey && (
                      <SegmentedControl
                        value={setupMethod}
                        onChange={(v) => setSetupMethod(v as 'totp' | 'webauthn')}
                        data={[
                          { value: 'totp', label: 'Authenticator app' },
                          { value: 'webauthn', label: 'Security key / passkey' },
                        ]}
                      />
                    )}
                    {setupMethod === 'webauthn' && canKey ? (
                      <Button leftSection={<IconKey size={18} />} loading={loading} onClick={registerKey}>
                        Register a security key
                      </Button>
                    ) : (
                      enrolment && <Enrolment enrolment={enrolment} onCode={confirm} loading={loading} />
                    )}
                  </>
                ) : (
                  <>
                    {challenge.methods.webauthn && canKey && (
                      <Button leftSection={<IconKey size={18} />} loading={loading} onClick={useKey}>
                        Use your security key
                      </Button>
                    )}
                    {challenge.methods.totp ? (
                      <>
                        <Text size="sm" c="dimmed">
                          {challenge.methods.webauthn && canKey ? 'Or type' : 'Type'} the code shown by your authenticator app.
                        </Text>
                        <CodeInput onSubmit={verify} loading={loading} allowRecovery />
                      </>
                    ) : (
                      <>
                        <Text size="xs" c="dimmed">
                          No security key at hand? Use a recovery code.
                        </Text>
                        <TextInput
                          placeholder="xxxx-xxxx"
                          onKeyDown={(e) => e.key === 'Enter' && verify(e.currentTarget.value)}
                          aria-label="Recovery code"
                        />
                      </>
                    )}
                  </>
                )}
                <Button variant="subtle" size="compact-sm" onClick={() => { setChallenge(null); setEnrolment(null); setError(null); }}>
                  Back
                </Button>
              </Stack>
            ) : (
            <>
            {oidcUrl && (
              <>
                <Button component="a" href={oidcUrl} size="md" variant={showForm ? 'light' : 'filled'} leftSection={<IconKey size={18} />} fullWidth>
                  Sign in with {providers!.oidc.label}
                </Button>
                {showForm && <Divider label="or" labelPosition="center" />}
              </>
            )}
            {providers && !showForm && !oidcUrl && (
              <Alert color="orange" p="sm">
                No sign-in method is enabled: contact an administrator.
              </Alert>
            )}
            {showForm && (
            <form onSubmit={submit}>
              <Stack>
                <TextInput
                  label="Username"
                  description={providers?.ldap.enabled ? (providers.local ? `Local account or ${providers.ldap.label}` : providers.ldap.label) : undefined}
                  leftSection={<IconUser size={16} />}
                  value={username}
                  onChange={(e) => setUsername(e.currentTarget.value)}
                  autoComplete="username"
                  autoFocus={!oidcUrl}
                  required
                />
                <PasswordInput
                  label="Password"
                  leftSection={<IconLock size={16} />}
                  value={password}
                  onChange={(e) => setPassword(e.currentTarget.value)}
                  autoComplete="current-password"
                  required
                />
                <Button type="submit" loading={loading} fullWidth mt="xs" variant={oidcUrl ? 'default' : 'filled'}>
                  Sign in
                </Button>
              </Stack>
            </form>
            )}
            </>
            )}
          </Stack>
        </Paper>
        <Text size="xs" c="dimmed" ta="center" mt="md">
          Restricted access: every new account must be approved by an administrator. Sign-ins are logged.
        </Text>
      </Box>
    </Center>
  );
}
