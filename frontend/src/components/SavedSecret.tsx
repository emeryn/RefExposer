import { Checkbox, PasswordInput, Stack } from '@mantine/core';

/**
 * Password field of a setting stored encrypted in the database.
 *
 * The value is never sent back to the browser: the API only says whether one is saved. Leaving the field empty
 * keeps it, and the checkbox is the only way to remove it.
 */
export function SavedSecret({
  label,
  description,
  value,
  isSet,
  clear,
  onChange,
  onClear,
}: {
  label: string;
  description?: string;
  value: string;
  isSet?: boolean;
  clear?: boolean;
  onChange: (v: string) => void;
  onClear?: (v: boolean) => void;
}) {
  return (
    <Stack gap={4}>
      <PasswordInput
        label={label}
        description={description}
        placeholder={isSet && !clear ? '•••••••• (saved, leave empty to keep it)' : ''}
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        disabled={clear}
        autoComplete="new-password"
        rightSectionWidth={36}
      />
      {isSet && onClear && (
        <Checkbox size="xs" label="Clear the saved value" checked={!!clear} onChange={(e) => onClear(e.currentTarget.checked)} />
      )}
    </Stack>
  );
}
