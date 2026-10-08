import { X } from 'lucide-react'
import type { ReactNode } from 'react'

import { StatusLabel } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { area, codeLabel, length, metres, quantityText, relativeDifference } from '@/lib/format'
import type { FeatureDetail } from '@/lib/types'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-3 border-b border-border py-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

interface Props {
  feature: FeatureDetail | undefined
  loading: boolean
  onClose: () => void
}

export function FeatureDetails({ feature, loading, onClose }: Props) {
  if (loading) {
    return (
      <div className="grid gap-3 p-4" aria-label="Loading feature">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (!feature) {
    return (
      <div className="flex h-full items-center p-6 text-sm text-muted-foreground">
        Select a feature on the map or in the table to see its geometry, measurement and attributes.
      </div>
    )
  }

  const m = feature.measurement
  const properties = Object.entries(feature.properties)
  return (
    <article aria-label={`Feature ${feature.id}`} className="flex flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-rule p-4">
        <div className="min-w-0">
          <p className="numeral text-xs text-muted-foreground">
            Feature {feature.id} · {feature.layer}
          </p>
          <h2 className="mt-1 truncate text-lg font-semibold" title={feature.name ?? undefined}>
            {feature.name ?? 'Unnamed feature'}
          </h2>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close feature details">
          <X />
        </Button>
      </header>

      <div className="p-4">
        <dl>
          <Row label="Geometry">{feature.geometry_type ?? 'None'}</Row>
          <Row label="Status">
            <StatusLabel status={feature.measurement_status} />
          </Row>
          {m?.area_m2 != null && (
            <Row label="Area">
              <span className="numeral text-base">{quantityText(area(m.area_m2))}</span>
              <span className="block text-xs text-muted-foreground">{metres(m.area_m2, 'm²')}</span>
            </Row>
          )}
          {m?.perimeter_m != null && <Row label="Perimeter">{quantityText(length(m.perimeter_m))}</Row>}
          {m?.length_m != null && (
            <Row label="Length">
              <span className="numeral text-base">{quantityText(length(m.length_m))}</span>
              <span className="block text-xs text-muted-foreground">{metres(m.length_m, 'm')}</span>
            </Row>
          )}
          {m && (
            <>
              <Row label="Projected to">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block cursor-help truncate underline decoration-dotted underline-offset-2">
                      {m.method === 'UTM' ? m.projected_crs : 'Local Lambert equal-area'}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-sm break-all">{m.projected_crs}</TooltipContent>
                </Tooltip>
              </Row>
              <Row label="Geodesic check">
                {quantityText(m.geodesic_area_m2 != null ? area(m.geodesic_area_m2) : length(m.geodesic_length_m))}
                <span className="block text-xs text-muted-foreground">
                  Difference {relativeDifference(m.relative_difference)}
                </span>
              </Row>
            </>
          )}
          {feature.geometry_repaired && (
            <Row label="Validity">
              Repaired before measuring
              <span className="block text-xs text-muted-foreground">{feature.validity_reason}</span>
            </Row>
          )}
          {feature.error && (
            <Row label="Error">
              <span className="text-signal">{codeLabel(feature.error.code)}</span>
              <span className="block text-xs text-muted-foreground">{feature.error.message}</span>
            </Row>
          )}
          {feature.issues.map((issue) => (
            <Row key={issue.code} label="Note">
              {codeLabel(issue.code)}
              <span className="block text-xs text-muted-foreground">{issue.message}</span>
            </Row>
          ))}
        </dl>

        <h3 className="mt-6 mb-1 text-xs text-muted-foreground">Attributes ({properties.length})</h3>
        {properties.length === 0 ? (
          <p className="text-sm text-muted-foreground">This feature has no attributes.</p>
        ) : (
          <dl>
            {properties.map(([key, value]) => (
              <Row key={key} label={key}>
                {formatValue(value)}
              </Row>
            ))}
          </dl>
        )}
      </div>
    </article>
  )
}
