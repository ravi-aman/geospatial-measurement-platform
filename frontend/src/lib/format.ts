// Presentation of measurements. The API returns metres / square metres; the UI picks a readable unit
// but never hides the SI value it came from.

const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const twoDecimals = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const compact = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 })

export interface Quantity {
  value: string
  unit: string
}

export const M2_PER_HECTARE = 10_000
export const M2_PER_KM2 = 1_000_000

/** Best-fit unit for an area: m² below 1 ha, hectares below 1 km², km² above. */
export function area(m2: number | null | undefined): Quantity | null {
  if (m2 == null || !Number.isFinite(m2)) return null
  if (m2 >= M2_PER_KM2) return { value: compact.format(m2 / M2_PER_KM2), unit: 'km²' }
  if (m2 >= M2_PER_HECTARE) return { value: compact.format(m2 / M2_PER_HECTARE), unit: 'ha' }
  return { value: twoDecimals.format(m2), unit: 'm²' }
}

/** Best-fit unit for a length: metres below 1 km, kilometres above. */
export function length(m: number | null | undefined): Quantity | null {
  if (m == null || !Number.isFinite(m)) return null
  if (m >= 1000) return { value: compact.format(m / 1000), unit: 'km' }
  return { value: twoDecimals.format(m), unit: 'm' }
}

export function quantityText(q: Quantity | null): string {
  return q ? `${q.value} ${q.unit}` : '—'
}

export function metres(value: number | null | undefined, unit: 'm' | 'm²'): string {
  return value == null ? '—' : `${twoDecimals.format(value)} ${unit}`
}

export function count(value: number | null | undefined): string {
  return value == null ? '—' : integer.format(value)
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(2)} GB`
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`
}

/** Relative difference between projected and geodesic values, e.g. "0.0003 %" or "< 0.0001 %". */
export function relativeDifference(value: number | null | undefined): string {
  if (value == null) return '—'
  const percent = Math.abs(value) * 100
  if (percent < 0.0001) return '< 0.0001 %'
  return `${value < 0 ? '−' : '+'}${percent.toPrecision(2)} %`
}

/** Decimal degrees with hemisphere, for bounding-box folio markers. */
export function coordinate(value: number, axis: 'lon' | 'lat'): string {
  const hemisphere = axis === 'lon' ? (value >= 0 ? 'E' : 'W') : value >= 0 ? 'N' : 'S'
  return `${Math.abs(value).toFixed(4)}° ${hemisphere}`
}

export function timestamp(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso))
}

const STATUS_LABELS: Record<string, string> = {
  PENDING: 'Queued',
  PROCESSING: 'Processing',
  COMPLETED: 'Completed',
  COMPLETED_WITH_ERRORS: 'Completed with errors',
  FAILED: 'Failed',
  MEASURED: 'Measured',
  NOT_APPLICABLE: 'Not applicable',
  UNSUPPORTED: 'Unsupported',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/** "GEOMETRY_REPAIRED" -> "Geometry repaired" */
export function codeLabel(code: string): string {
  const text = code.toLowerCase().replaceAll('_', ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
