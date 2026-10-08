import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, parseApiError } from '@/lib/api'
import { CRS_PATTERN, validateSelection } from '@/lib/upload-validation'

describe('parseApiError', () => {
  it('reads the backend error envelope', () => {
    const error = parseApiError(
      422,
      { error: { code: 'SHAPEFILE_INCOMPLETE', message: 'missing .shx', details: { missing: ['.shx'] }, request_id: 'r1' } },
      'header-id',
    )
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 422, code: 'SHAPEFILE_INCOMPLETE', message: 'missing .shx', requestId: 'r1' })
    expect(error.details).toEqual({ missing: ['.shx'] })
  })

  it('falls back gracefully for non-envelope bodies', () => {
    const error = parseApiError(502, '<html>bad gateway</html>', 'abc')
    expect(error).toMatchObject({ status: 502, code: 'HTTP_ERROR', requestId: 'abc' })
  })
})

describe('api client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('builds measurement queries with repeated filters and the cursor', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], page: { limit: 200, next_cursor: null, total: 0 } }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await api.measurements('f1', { sort: '-area_m2', statuses: ['MEASURED', 'FAILED'], geometryTypes: ['Polygon'] }, 'abc')
    const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://x')
    expect(url.pathname).toBe('/api/files/f1/measurements/')
    expect(url.searchParams.getAll('status')).toEqual(['MEASURED', 'FAILED'])
    expect(url.searchParams.getAll('geometry_type')).toEqual(['Polygon'])
    expect(url.searchParams.get('sort')).toBe('-area_m2')
    expect(url.searchParams.get('cursor')).toBe('abc')
  })

  it('throws ApiError with the server code on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: 'FILE_NOT_FOUND', message: 'No file with this id.' } }), {
          status: 404,
          headers: { 'x-request-id': 'req-9' },
        }),
      ),
    )
    await expect(api.getFile('missing')).rejects.toMatchObject({ status: 404, code: 'FILE_NOT_FOUND', requestId: 'req-9' })
  })

  it('maps network failures to NETWORK_ERROR', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.capabilities()).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
  })
})

describe('validateSelection', () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name)
  const extensions = ['.kml', '.zip']

  it('accepts supported files within the limit', () => {
    expect(validateSelection(file('survey.KML', 10), extensions, 100)).toBeNull()
    expect(validateSelection(file('parcels.zip', 10), extensions, undefined)).toBeNull()
  })

  it('rejects unsupported, empty and oversized files', () => {
    expect(validateSelection(file('data.geojson', 10), extensions, 100)).toMatch(/Unsupported file type/)
    expect(validateSelection(file('survey.kml', 0), extensions, 100)).toMatch(/empty/)
    expect(validateSelection(file('survey.kml', 200), extensions, 100)).toMatch(/limit/)
  })

  it('CRS override syntax matches the API', () => {
    expect(CRS_PATTERN.test('EPSG:32643')).toBe(true)
    expect(CRS_PATTERN.test('esri:102100')).toBe(true)
    expect(CRS_PATTERN.test('+proj=merc')).toBe(false)
    expect(CRS_PATTERN.test('WGS84')).toBe(false)
  })
})
