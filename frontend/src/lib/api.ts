import type {
  Capabilities,
  FeatureDetail,
  FileInfo,
  FileList,
  MeasurementPage,
  MeasurementQuery,
} from '@/lib/types'

// Empty in development (Vite proxies /api to the backend); set VITE_API_BASE_URL for a separately hosted API.
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

/** Mirrors the backend's error envelope: {"error": {"code", "message", "details", "request_id"}}. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

export function parseApiError(status: number, body: unknown, requestId: string | null): ApiError {
  if (body && typeof body === 'object' && 'error' in body) {
    const error = (body as { error: Record<string, unknown> }).error
    return new ApiError(
      status,
      typeof error.code === 'string' ? error.code : 'HTTP_ERROR',
      typeof error.message === 'string' ? error.message : `Request failed (${status})`,
      (error.details as Record<string, unknown>) ?? {},
      (typeof error.request_id === 'string' ? error.request_id : null) ?? requestId,
    )
  }
  return new ApiError(status, 'HTTP_ERROR', `Request failed (${status})`, {}, requestId)
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'The API is unreachable. Check that the backend is running.')
  }
  const requestId = response.headers.get('x-request-id')
  const body: unknown = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) throw parseApiError(response.status, body, requestId)
  return body as T
}

function measurementParams(query: MeasurementQuery, cursor: string | null, limit: number): URLSearchParams {
  const params = new URLSearchParams({ sort: query.sort, limit: String(limit) })
  query.statuses.forEach((s) => params.append('status', s))
  query.geometryTypes.forEach((g) => params.append('geometry_type', g))
  if (cursor) params.set('cursor', cursor)
  return params
}

export const api = {
  capabilities: () => request<Capabilities>('/api/capabilities'),
  listFiles: (cursor: string | null, limit = 20) =>
    request<FileList>(`/api/files/?${new URLSearchParams({ limit: String(limit), ...(cursor ? { cursor } : {}) })}`),
  getFile: (id: string) => request<FileInfo>(`/api/files/${encodeURIComponent(id)}/`),
  measurements: (id: string, query: MeasurementQuery, cursor: string | null, limit = 200) =>
    request<MeasurementPage>(`/api/files/${encodeURIComponent(id)}/measurements/?${measurementParams(query, cursor, limit)}`),
  feature: (id: string, featureId: number) =>
    request<FeatureDetail>(`/api/files/${encodeURIComponent(id)}/features/${featureId}/`),
  tileUrlTemplate: (id: string) => {
    const path = `/api/files/${encodeURIComponent(id)}/tiles/{z}/{x}/{y}.mvt`
    return API_BASE_URL ? `${API_BASE_URL}${path}` : `${window.location.origin}${path}`
  },
}

export interface UploadOptions {
  file: File
  crs?: string
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

/**
 * Multipart upload with progress events. fetch() cannot report upload progress, so this one call uses XHR.
 * An Idempotency-Key makes a retried request (e.g. after a dropped connection) safe to resend.
 */
export function uploadFile({ file, crs, onProgress, signal }: UploadOptions): Promise<FileInfo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl('/api/files/'))
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.setRequestHeader('Idempotency-Key', crypto.randomUUID())
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total)
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = null
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as FileInfo)
      else reject(parseApiError(xhr.status, body, xhr.getResponseHeader('x-request-id')))
    }
    xhr.onerror = () => reject(new ApiError(0, 'NETWORK_ERROR', 'Upload failed: the API is unreachable.'))
    xhr.onabort = () => reject(new ApiError(0, 'ABORTED', 'Upload cancelled.'))
    signal?.addEventListener('abort', () => xhr.abort())

    const form = new FormData()
    form.append('file', file)
    if (crs?.trim()) form.append('crs', crs.trim())
    xhr.send(form)
  })
}
