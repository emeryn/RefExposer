import { Button } from '@mantine/core';
import { IconMapOff } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/Common';

export default function NotFound() {
  return (
    <EmptyState icon={<IconMapOff size={28} />} title="Page not found">
      <Button component={Link} to="/" variant="light" mt="sm">
        Back to the dashboard
      </Button>
    </EmptyState>
  );
}
