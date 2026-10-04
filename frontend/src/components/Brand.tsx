import { Group, Image, Stack, Text, Title } from '@mantine/core';
import { useBranding } from '../api/hooks';

/** Company logo when one is configured, the default RefExposer mark otherwise. */
export function BrandLogo({ size }: { size: number }) {
  const { data } = useBranding();
  if (data?.logo_url)
    return <Image src={data.logo_url} alt="" h={size} w="auto" maw={size * 4} fit="contain" style={{ flexShrink: 0 }} />;
  return <Image src="/logo.svg" alt="" h={size} w={size} style={{ flexShrink: 0 }} />;
}

/** Logo, title and subtitle of the header. */
export function BrandHeader() {
  const { data } = useBranding();
  return (
    <Group gap={10} wrap="nowrap">
      <BrandLogo size={34} />
      <div style={{ minWidth: 0 }}>
        <Text fw={800} size="lg" lh={1} truncate>
          {data?.title ?? 'RefExposer'}
        </Text>
        {data?.subtitle && (
          <Text size="xs" c="dimmed" lh={1.2} visibleFrom="sm" truncate>
            {data.subtitle}
          </Text>
        )}
      </div>
    </Group>
  );
}

/** Centered logo and title of the sign-in and waiting pages. */
export function BrandHero({ caption }: { caption?: string }) {
  const { data } = useBranding();
  return (
    <Stack align="center" gap={6} mb="lg">
      <BrandLogo size={56} />
      <Title order={2} ta="center">
        {data?.title ?? 'RefExposer'}
      </Title>
      {data?.subtitle && (
        <Text c="dimmed" size="sm" ta="center" mt={-4}>
          {data.subtitle}
        </Text>
      )}
      {caption && (
        <Text c="dimmed" size="sm" ta="center">
          {caption}
        </Text>
      )}
    </Stack>
  );
}
