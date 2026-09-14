'use client';

import { useParams } from 'next/navigation';

import { ActorDetailPage } from '@/components/integrations/ActorDetailPage';

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>();
  return <ActorDetailPage kind="source" actorId={params.id} />;
}
