import type { ReactNode } from 'react'

import { StatusLabel } from '@/components/status'
import { area, bytes, count, duration, length, metres, timestamp, type Quantity } from '@/lib/format'
import { TERMINAL_STATUSES, type FileInfo } from '@/lib/types'
import { cn } from '@/lib/utils'

/*
 * The survey "title block": like the cartouche on a survey or engineering drawing, a ruled grid stating what
 * the sheet is, its coordinate reference and its headline quantities. Measurements are set in condensed
 * numerals with their unit; the exact SI value sits underneath.
 */

function Cell({ label, children, sub, className }: { label: string; children: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex min-w-0 flex-col justify-between gap-3 border-border p-4', className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0">
        <div className="numeral text-3xl leading-none font-medium sm:text-4xl">{children}</div>
        {sub && <div className="mt-2 truncate text-xs text-muted-foreground">{sub}</div>}
      </dd>
    </div>
  )
}

function QuantityValue({ quantity }: { quantity: Quantity | null }) {
  if (!quantity) return <span className="text-muted-foreground">—</span>
  return (
    <>
      {quantity.value}
      <span className="ml-1.5 text-lg text-muted-foreground">{quantity.unit}</span>
    </>
  )
}

function crsNote(file: FileInfo): string {
  if (!TERMINAL_STATUSES.includes(file.status)) return 'Determined during processing'
  if (!file.crs) return 'Not declared by the file'
  return `${file.crs_name ?? ''}${file.crs_source === 'USER_OVERRIDE' ? ' · set on upload' : ''}`
}

const STRATEGY_LABELS: Record<string, string> = {
  local_equal_area: 'Local equal-area projection',
  utm: 'UTM zone per feature',
}

export function TitleBlock({ file }: { file: FileInfo }) {
  const s = file.summary
  const finished = TERMINAL_STATUSES.includes(file.status)
  const breakdown = s
    ? [
        [s.measured, 'measured'],
        [s.not_applicable, 'points'],
        [s.unsupported, 'unsupported'],
        [s.failed, 'failed'],
      ]
        .filter(([n]) => (n as number) > 0)
        .map(([n, label]) => `${count(n as number)} ${label}`)
        .join(' · ')
    : null

  return (
    <section aria-label="File summary" className="border-y border-y-rule">
      <div className="flex flex-col gap-4 border-b border-border p-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">
            {file.format === 'KML' ? 'KML' : 'Zipped Shapefile'} · {bytes(file.size_bytes)} · uploaded {timestamp(file.created_at)}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight break-all sm:text-3xl">{file.filename}</h1>
        </div>
        <StatusLabel status={file.status} className="text-base" />
      </div>
      <dl className="grid grid-cols-2 lg:grid-cols-5 [&>*:nth-child(2n)]:border-l lg:[&>*:not(:first-child)]:border-l [&>*:nth-child(n+3)]:border-t lg:[&>*:nth-child(n+3)]:border-t-0">
        <Cell label="Coordinate reference" sub={crsNote(file)}>
          <span className={cn(finished && !file.crs && 'text-signal')}>{file.crs ?? (finished ? 'Unknown' : '—')}</span>
        </Cell>
        <Cell label="Features" sub={breakdown || undefined}>
          {count(file.feature_count)}
        </Cell>
        <Cell label="Sum of polygon areas" sub={s?.total_area_m2 != null ? metres(s.total_area_m2, 'm²') : 'No polygons measured'}>
          <QuantityValue quantity={area(s?.total_area_m2)} />
        </Cell>
        <Cell label="Sum of line lengths" sub={s?.total_length_m != null ? metres(s.total_length_m, 'm') : 'No lines measured'}>
          <QuantityValue quantity={length(s?.total_length_m)} />
        </Cell>
        <Cell
          label="Processing time"
          className="col-span-2 lg:col-span-1"
          sub={STRATEGY_LABELS[file.measurement_strategy] ?? file.measurement_strategy}
        >
          {duration(file.job.duration_ms)}
        </Cell>
      </dl>
    </section>
  )
}
