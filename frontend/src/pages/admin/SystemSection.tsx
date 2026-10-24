import { IconCalendarTime, IconClipboardList, IconDatabase, IconServer } from '@tabler/icons-react';
import { lazy } from 'react';
import { SectionTabs } from '../../components/SectionTabs';

const SystemPage = lazy(() => import('../SystemPage'));
const StoragePage = lazy(() => import('./StoragePage'));
const TasksPage = lazy(() => import('./TasksPage'));
const AuditPage = lazy(() => import('./AuditPage'));

export default function SystemSection() {
  return (
    <SectionTabs
      title="System"
      description="State of the service, disk space, scheduled tasks and the audit trail."
      base="/system"
      sections={[
        { value: 'status', label: 'Status', icon: IconServer, element: <SystemPage /> },
        { value: 'storage', label: 'Disk space', icon: IconDatabase, element: <StoragePage /> },
        { value: 'tasks', label: 'Tasks', icon: IconCalendarTime, element: <TasksPage /> },
        { value: 'audit', label: 'Audit log', icon: IconClipboardList, element: <AuditPage /> },
      ]}
    />
  );
}
