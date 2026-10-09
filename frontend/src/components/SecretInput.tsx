import { ActionIcon, Menu, Text, TextInput, Tooltip, type TextInputProps } from '@mantine/core';
import { IconKey, IconSettings } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/client';

/** Value masked by the API: sent back unchanged, the stored value is kept. */
export const MASKED = '********';

/**
 * Input of a credential (header value, Basic authentication, Git token) with a picker of the secret manager:
 * choosing a secret writes its ${secret:<name>} reference, never its value.
 * `keepPrefix`: for "user:password", the part before ':' is kept and the secret replaces the password.
 */
export function SecretInput({
  value,
  onChange,
  keepPrefix = false,
  description,
  ...props
}: Omit<TextInputProps, 'value' | 'onChange'> & { value: string; onChange: (v: string) => void; keepPrefix?: boolean }) {
  const { data: secrets } = useQuery({ queryKey: ['secrets'], queryFn: api.secrets, staleTime: 30_000 });
  const pick = (reference: string) => {
    if (keepPrefix && /^[^:$]+:/.test(value)) onChange(`${value.split(':')[0]}:${reference}`);
    else if (/^\s*(Bearer|Token|Basic)\s+/i.test(value)) onChange(value.replace(/^(\s*\w+\s+).*$/, `$1${reference}`));
    else onChange(reference);
  };
  const masked = value === MASKED;
  const literal = !!value && !masked && !value.includes('${');
  return (
    <TextInput
      {...props}
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
      autoComplete="off"
      description={
        masked ? (
          <Text span size="xs" c="dimmed">
            Stored value kept (never shown). Type a new value or pick a secret to replace it.
          </Text>
        ) : literal ? (
          <Text span size="xs" c="orange">
            Literal value: it is stored in the definition. Prefer a secret of the secret manager.
          </Text>
        ) : (
          description
        )
      }
      rightSectionPointerEvents="all"
      rightSection={
        <Menu position="bottom-end" withinPortal shadow="md">
          <Menu.Target>
            <Tooltip label="Use a secret of the secret manager" withArrow>
              <ActionIcon variant="subtle" size="sm" color="indigo" aria-label="Pick a secret">
                <IconKey size={14} />
              </ActionIcon>
            </Tooltip>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Secrets</Menu.Label>
            {!secrets?.length && (
              <Menu.Item disabled>
                <Text size="xs">No secret yet</Text>
              </Menu.Item>
            )}
            {secrets?.map((s) => (
              <Menu.Item key={s.name} onClick={() => pick(s.reference)}>
                <Text size="sm" ff="monospace">
                  {s.name}
                </Text>
                {(s.description || s.hosts.length > 0) && (
                  <Text size="xs" c="dimmed">
                    {[s.description, s.hosts.length ? `hosts: ${s.hosts.join(', ')}` : null].filter(Boolean).join(' · ')}
                  </Text>
                )}
              </Menu.Item>
            ))}
            <Menu.Divider />
            <Menu.Item component={Link} to="/admin/secrets" target="_blank" leftSection={<IconSettings size={14} />}>
              Manage secrets
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      }
    />
  );
}
