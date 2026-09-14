'use client';

import { useParams } from 'next/navigation';

import { ActorDetailPage } from '@/components/integrations/ActorDetailPage';

export default function DestinationDetailPage() {
  const params = useParams<{ id: string }>();
  return <ActorDetailPage kind="destination" actorId={params.id} />;
}
