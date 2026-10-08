import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef } from 'react'

import { StatusLabel } from '@/components/status'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useMeasurements } from '@/hooks/queries'
import { area, count, length, quantityText, relativeDifference, statusLabel } from '@/lib/format'
import type { FileSummary, MeasurementQuery, MeasurementStatus, SortKey } from '@/lib/types'
import { cn } from '@/lib/utils'

const ROW_HEIGHT = 44
const STATUSES: MeasurementStatus[] = ['MEASURED', 'NOT_APPLICABLE', 'UNSUPPORTED', 'FAILED']
const SORTS: { value: SortKey; label: string }[] = [
  { value: 'feature_id', label: 'File order' },
  { value: '-area_m2', label: 'Largest area first' },
  { value: 'area_m2', label: 'Smallest area first' },
  { value: '-length_m', label: 'Longest first' },
  { value: 'length_m', label: 'Shortest first' },
]
const COLUMNS = 'grid-cols-[64px_minmax(180px,2fr)_minmax(120px,1fr)_minmax(150px,1fr)_130px_130px_120px_minmax(150px,1.2fr)]'

const STATUS_COUNT_KEY: Record<MeasurementStatus, keyof FileSummary> = {
  MEASURED: 'measured',
  NOT_APPLICABLE: 'not_applicable',
  UNSUPPORTED: 'unsupported',
  FAILED: 'failed',
}

interface Props {
  fileId: string
  summary: FileSummary
  query: MeasurementQuery
  onQueryChange: (query: MeasurementQuery) => void
  selectedId: number | null
  onSelect: (featureId: number) => void
}

export function FeatureTable({ fileId, summary, query, onQueryChange, selectedId, onSelect }: Props) {
  const measurements = useMeasurements(fileId, query, true)
  const rows = measurements.data?.pages.flatMap((page) => page.items) ?? []
  const total = measurements.data?.pages[0]?.page.total ?? null
  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })
  const items = virtualizer.getVirtualItems()
  const lastIndex = items.at(-1)?.index ?? -1

  // Infinite scroll: fetch the next keyset page when the last rendered row nears the end.
  useEffect(() => {
    if (lastIndex >= rows.length - 20 && measurements.hasNextPage && !measurements.isFetchingNextPage) {
      void measurements.fetchNextPage()
    }
  }, [lastIndex, rows.length, measurements])

  const geometryTypes = Object.keys(summary.geometry_types).filter((t) => t !== '(none)')

  return (
    <section aria-labelledby="features-heading" className="flex flex-col">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-4 border-b border-rule pb-4">
        <div>
          <h2 id="features-heading" className="text-lg font-semibold tracking-tight">
            Features
          </h2>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total == null ? 'Loading…' : `${count(total)} of ${count(summary.total_features)} shown`}
          </p>
        </div>

        <fieldset className="grid gap-1.5">
          <legend className="mb-1.5 text-xs text-muted-foreground">Status</legend>
          <ToggleGroup
            type="multiple"
            value={query.statuses}
            onValueChange={(value) => onQueryChange({ ...query, statuses: value as MeasurementStatus[] })}
            className="flex-wrap border border-border"
          >
            {STATUSES.filter((s) => (summary[STATUS_COUNT_KEY[s]] as number) > 0).map((status) => (
              <ToggleGroupItem key={status} value={status} className="h-8 gap-2 px-3 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background">
                {statusLabel(status)}
                <span className="tabular opacity-70">{count(summary[STATUS_COUNT_KEY[status]] as number)}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </fieldset>

        {geometryTypes.length > 1 && (
          <fieldset className="grid gap-1.5">
            <legend className="mb-1.5 text-xs text-muted-foreground">Geometry</legend>
            <ToggleGroup
              type="multiple"
              value={query.geometryTypes}
              onValueChange={(value) => onQueryChange({ ...query, geometryTypes: value })}
              className="flex-wrap border border-border"
            >
              {geometryTypes.map((type) => (
                <ToggleGroupItem key={type} value={type} className="h-8 gap-2 px-3 text-xs data-[state=on]:bg-foreground data-[state=on]:text-background">
                  {type}
                  <span className="tabular opacity-70">{count(summary.geometry_types[type])}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </fieldset>
        )}

        <div className="grid gap-1.5">
          <span id="sort-label" className="text-xs text-muted-foreground">
            Order
          </span>
          <Select value={query.sort} onValueChange={(value) => onQueryChange({ ...query, sort: value as SortKey })}>
            <SelectTrigger aria-labelledby="sort-label" className="h-8 w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORTS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div ref={scrollRef} className="h-[560px] overflow-auto" role="table" aria-label="Features and measurements" aria-rowcount={total ?? undefined}>
        <div role="rowgroup" className="sticky top-0 z-10 min-w-[1000px] bg-background">
          <div role="row" className={cn('grid border-b border-border py-2 text-xs text-muted-foreground', COLUMNS)}>
            <span role="columnheader" className="px-2">#</span>
            <span role="columnheader" className="px-2">Name</span>
            <span role="columnheader" className="px-2">Geometry</span>
            <span role="columnheader" className="px-2">Status</span>
            <span role="columnheader" className="px-2 text-right">Area</span>
            <span role="columnheader" className="px-2 text-right">Length</span>
            <span role="columnheader" className="px-2 text-right">Geodesic diff.</span>
            <span role="columnheader" className="px-2">Notes</span>
          </div>
        </div>

        {measurements.isPending && <p className="p-4 text-sm text-muted-foreground">Loading measurements…</p>}
        {measurements.isError && (
          <p role="alert" className="p-4 text-sm text-signal">
            Could not load measurements: {measurements.error.message}
          </p>
        )}
        {measurements.isSuccess && rows.length === 0 && (
          <p className="p-4 text-sm text-muted-foreground">No features match these filters.</p>
        )}

        <div role="rowgroup" className="relative min-w-[1000px]" style={{ height: virtualizer.getTotalSize() }}>
          {items.map((item) => {
            const row = rows[item.index]
            const m = row.measurement
            const selected = row.feature_id === selectedId
            const notes = [row.geometry_repaired ? 'Repaired' : null, row.error?.message, ...row.issues.map((i) => i.message)]
              .filter(Boolean)
              .join(' · ')
            return (
              <div
                key={row.feature_id}
                role="row"
                aria-selected={selected}
                tabIndex={0}
                onClick={() => onSelect(row.feature_id)}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(row.feature_id))}
                className={cn(
                  'absolute inset-x-0 grid cursor-pointer items-center border-b border-border text-sm hover:bg-muted',
                  COLUMNS,
                  selected && 'bg-muted shadow-[inset_3px_0_0_var(--signal)]',
                )}
                style={{ height: ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
              >
                <span role="cell" className="numeral px-2 text-muted-foreground">{row.feature_id}</span>
                <span role="cell" className="truncate px-2" title={row.name ?? undefined}>
                  {row.name ?? <span className="text-muted-foreground">Unnamed</span>}
                  <span className="block truncate text-xs text-muted-foreground">{row.layer}</span>
                </span>
                <span role="cell" className="truncate px-2">{row.geometry_type ?? '—'}</span>
                <span role="cell" className="px-2"><StatusLabel status={row.measurement_status} /></span>
                <span role="cell" className="numeral px-2 text-right">{m?.area_m2 != null ? quantityText(area(m.area_m2)) : '—'}</span>
                <span role="cell" className="numeral px-2 text-right">{m?.length_m != null ? quantityText(length(m.length_m)) : '—'}</span>
                <span role="cell" className="numeral px-2 text-right text-muted-foreground">{m ? relativeDifference(m.relative_difference) : '—'}</span>
                <span role="cell" className="truncate px-2 text-xs text-muted-foreground" title={notes}>{notes}</span>
              </div>
            )
          })}
        </div>
        {measurements.isFetchingNextPage && <p className="p-3 text-xs text-muted-foreground">Loading more…</p>}
      </div>
    </section>
  )
}
