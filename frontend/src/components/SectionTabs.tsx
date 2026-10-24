import { Badge, Stack, Tabs, Text, Title, Tooltip } from '@mantine/core';
import type { Icon } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

export type Section = {
  /** Last segment of the URL, and the identifier of the tab. */
  value: string;
  label: string;
  icon: Icon;
  element: ReactNode;
  badge?: number;
  badgeColor?: string;
  badgeLabel?: string;
};

/**
 * One administration entry holding several pages as tabs: the sidebar stays short and related screens are
 * reached without going back to it. The active tab is the last segment of the URL, so a tab is bookmarkable
 * and the back button returns to the previous one.
 */
export function SectionTabs({
  title,
  description,
  base,
  sections,
}: {
  title: string;
  description?: string;
  /** Path the tabs hang from, e.g. "/admin/referentials". */
  base: string;
  sections: Section[];
}) {
  const { tab } = useParams();
  const navigate = useNavigate();
  const active = sections.find((s) => s.value === tab)?.value ?? sections[0].value;

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{title}</Title>
        {description && (
          <Text c="dimmed" size="sm">
            {description}
          </Text>
        )}
      </div>
      <Tabs value={active} onChange={(v) => navigate(`${base}/${v}`)} keepMounted={false}>
        <Tabs.List mb="md">
          {sections.map((s) => (
            <Tabs.Tab
              key={s.value}
              value={s.value}
              leftSection={<s.icon size={16} />}
              rightSection={
                s.badge ? (
                  <Tooltip label={s.badgeLabel}>
                    <Badge size="xs" color={s.badgeColor ?? 'orange'} circle>
                      {s.badge}
                    </Badge>
                  </Tooltip>
                ) : null
              }
            >
              {s.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {sections.map((s) => (
          <Tabs.Panel key={s.value} value={s.value}>
            {s.element}
          </Tabs.Panel>
        ))}
      </Tabs>
    </Stack>
  );
}
