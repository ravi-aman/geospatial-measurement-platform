// Types mirroring the backend's public API schemas (backend/app/api/schemas.py / GET /openapi.json).

export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'COMPLETED_WITH_ERRORS' | 'FAILED'
export type MeasurementStatus = 'MEASURED' | 'NOT_APPLICABLE' | 'UNSUPPORTED' | 'FAILED'
export type SourceFormat = 'KML' | 'SHAPEFILE'
export type ProjectionMethod = 'LOCAL_EQUAL_AREA' | 'UTM'

export const TERMINAL_STATUSES: readonly JobStatus[] = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED']
export const RESULT_STATUSES: readonly JobStatus[] = ['COMPLETED', 'COMPLETED_WITH_ERRORS']

export interface Issue {
  code: string
  message: string
}

export interface LayerSummary {
  name: string
  driver: string
  feature_count: number
}

export interface FileSummary {
  total_features: number
  measured: number
  not_applicable: number
  unsupported: number
  failed: number
  geometry_types: Record<string, number>
  total_area_m2: number | null
  total_length_m: number | null
  bbox: [number, number, number, number] | null
  features_with_z: number
  repaired_features: number
  error_counts: Record<string, number>
  issue_counts: Record<string, number>
  layers: LayerSummary[]
}

export interface Job {
  id: string
  status: JobStatus
  attempts: number
  max_attempts: number
  progress: { processed_features: number; total_features: number | null }
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error: { code: string; message: string; retryable: boolean } | null
}

export interface FileInfo {
  id: string
  filename: string
  format: SourceFormat
  size_bytes: number
  sha256: string
  status: JobStatus
  feature_count: number | null
  crs: string | null
  crs_name: string | null
  crs_source: 'FILE' | 'USER_OVERRIDE' | null
  measurement_strategy: string
  summary: FileSummary | null
  warnings: Issue[]
  job: Job
  created_at: string
  links: { self: string; measurements: string; features: string; tiles: string; job: string }
}

export interface FileListItem {
  id: string
  filename: string
  format: SourceFormat
  size_bytes: number
  status: JobStatus
  feature_count: number | null
  crs: string | null
  created_at: string
}

export interface FileList {
  items: FileListItem[]
  next_cursor: string | null
}

export interface Measurement {
  area_m2: number | null
  perimeter_m: number | null
  length_m: number | null
  projected_crs: string
  method: ProjectionMethod
  geodesic_area_m2: number | null
  geodesic_length_m: number | null
  relative_difference: number | null
}

export interface MeasurementItem {
  feature_id: number
  layer: string
  name: string | null
  geometry_type: string | null
  measurement_status: MeasurementStatus
  measurement: Measurement | null
  geometry_repaired: boolean
  validity_reason: string | null
  issues: Issue[]
  error: Issue | null
}

export interface Page {
  limit: number
  next_cursor: string | null
  total: number | null
}

export interface MeasurementPage {
  file_id: string
  status: JobStatus
  crs: string | null
  units: { area: 'm2'; length: 'm' }
  items: MeasurementItem[]
  page: Page
}

export interface GeoJsonGeometry {
  type: string
  coordinates?: unknown
  geometries?: GeoJsonGeometry[]
}

export interface FeatureDetail {
  type: 'Feature'
  id: number
  geometry: GeoJsonGeometry | null
  properties: Record<string, unknown>
  layer: string
  source_fid: number | null
  name: string | null
  geometry_type: string | null
  geometry_crs: 'EPSG:4326' | null
  raw_geometry: GeoJsonGeometry | null
  repaired_geometry: GeoJsonGeometry | null
  geometry_repaired: boolean
  validity_reason: string | null
  measurement_status: MeasurementStatus
  measurement: Measurement | null
  issues: Issue[]
  error: Issue | null
}

export interface Capabilities {
  formats: string[]
  extensions: string[]
  max_upload_bytes: number
  max_features_per_file: number
  measurement_strategy: string
  measurements: Record<string, string>
}

export type SortKey = 'feature_id' | '-area_m2' | 'area_m2' | '-length_m' | 'length_m'

export interface MeasurementQuery {
  sort: SortKey
  statuses: MeasurementStatus[]
  geometryTypes: string[]
}
