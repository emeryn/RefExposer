import { IconDatabaseCog, IconFolderSearch, IconWorldShare } from '@tabler/icons-react';
import { lazy } from 'react';
import { useReferentials } from '../../api/hooks';
import { SectionTabs } from '../../components/SectionTabs';

const ReferentialsAdminPage = lazy(() => import('./ReferentialsAdminPage'));
const DiscoveryPage = lazy(() => import('./DiscoveryPage'));
const PeersPage = lazy(() => import('./PeersPage'));

export default function ReferentialsSection() {
  const { data: refs } = useReferentials();
  const inError = (refs ?? []).filter((r) => r.health === 'error').length;

  return (
    <SectionTabs
      title="Referentials"
      description="The data RefExposer publishes: where it comes from, how it is read and who may read it."
      base="/admin/referentials"
      sections={[
        {
          value: 'list',
          label: 'Referentials',
          icon: IconDatabaseCog,
          element: <ReferentialsAdminPage />,
          badge: inError,
          badgeColor: 'red',
          badgeLabel: `${inError} referential(s) in error`,
        },
        { value: 'discovery', label: 'Bulk discovery', icon: IconFolderSearch, element: <DiscoveryPage /> },
        { value: 'peers', label: 'Synchronized instances', icon: IconWorldShare, element: <PeersPage /> },
      ]}
    />
  );
}
