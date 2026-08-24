'use client';

import * as React from 'react';
import { ArrowRight, Boxes, Database, FileText, Sparkles, Warehouse } from 'lucide-react';

import type { ActorRef } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Connector logos are served by our own API from vendored copies, never fetched
 * from the upstream registry: the catalog must render even when that registry is
 * unreachable (section 11.4), and no browser request should ever leave for the
 * engine. A connector without a vendored logo falls back to a local glyph, so a
 * missing file is a cosmetic detail rather than a broken card.
 */

const GLYPHS: Record<string, { Icon: React.ElementType; tint: string }> = {
  postgres: { Icon: Database, tint: 'text-[#336791] bg-[#336791]/10' },
  faker: { Icon: Sparkles, tint: 'text-brand bg-brand/10' },
  file: { Icon: FileText, tint: 'text-warning bg-warning/10' },
  warehouse: { Icon: Warehouse, tint: 'text-success bg-success/10' },
};

const SIZES = {
  xs: 'h-5 w-5 rounded-[4px] [&_svg]:h-3 [&_svg]:w-3',
  sm: 'h-6 w-6 rounded-md [&_svg]:h-3.5 [&_svg]:w-3.5',
  md: 'h-8 w-8 rounded-md [&_svg]:h-4 [&_svg]:w-4',
  lg: 'h-10 w-10 rounded-lg [&_svg]:h-5 [&_svg]:w-5',
} as const;

export function ConnectorIcon({
  icon, connectorKey, size = 'sm', className,
}: {
  icon?: string | null;
  /** When given, the vendored logo for this connector is preferred. */
  connectorKey?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const [logoFailed, setLogoFailed] = React.useState(false);
  // Reset when the card is reused for a different connector, or a single failure
  // would suppress every subsequent logo in a recycled list row.
  React.useEffect(() => setLogoFailed(false), [connectorKey]);

  const glyph = (icon && GLYPHS[icon]) || { Icon: Boxes, tint: 'text-text-tertiary bg-surface-2' };
  const showLogo = Boolean(connectorKey) && !logoFailed;

  return (
    <span
      className={cn('inline-flex flex-shrink-0 items-center justify-center overflow-hidden',
        SIZES[size], showLogo ? 'bg-surface-2' : glyph.tint, className)}
      aria-hidden
    >
      {showLogo ? (
        // eslint-disable-next-line @next/next/no-img-element -- same-origin SVG, no loader needed
        <img
          src={`/api/v1/connectors/${encodeURIComponent(connectorKey!)}/icon.svg`}
          alt=""
          loading="lazy"
          className="h-full w-full object-contain p-0.5"
          onError={() => setLogoFailed(true)}
        />
      ) : (
        <glyph.Icon />
      )}
    </span>
  );
}

/** Source → Destination, the visual signature of a pipeline row. */
export function SourceDestinationPath({
  source, destination, size = 'sm', showNames = true,
}: {
  source: ActorRef;
  destination: ActorRef;
  size?: keyof typeof SIZES;
  showNames?: boolean;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <ConnectorIcon icon={source.icon} connectorKey={source.connector_key} size={size} />
      {showNames && (
        <span className="truncate text-caption text-text-secondary">
          {source.connector_display_name ?? source.connector_key}
        </span>
      )}
      <ArrowRight className="h-3 w-3 flex-shrink-0 text-text-quaternary" aria-hidden />
      <ConnectorIcon icon={destination.icon} connectorKey={destination.connector_key} size={size} />
      {showNames && (
        <span className="truncate text-caption text-text-secondary">
          {destination.connector_display_name ?? destination.connector_key}
        </span>
      )}
    </span>
  );
}
