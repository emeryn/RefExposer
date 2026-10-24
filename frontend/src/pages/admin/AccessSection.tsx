import { IconUsers, IconUsersGroup } from '@tabler/icons-react';
import { lazy } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { SectionTabs } from '../../components/SectionTabs';

const UsersPage = lazy(() => import('./UsersPage'));
const GroupsPage = lazy(() => import('./GroupsPage'));

export default function AccessSection() {
  const { me } = useAuth();
  const pending = me?.pending_requests ?? 0;

  return (
    <SectionTabs
      title="Users and access"
      description="Accounts, their role and their second factor; groups carry the rights on the referentials."
      base="/admin/users"
      sections={[
        {
          value: 'users',
          label: 'Users',
          icon: IconUsers,
          element: <UsersPage />,
          badge: pending,
          badgeLabel: `${pending} pending access request(s)`,
        },
        { value: 'groups', label: 'Groups', icon: IconUsersGroup, element: <GroupsPage /> },
      ]}
    />
  );
}
