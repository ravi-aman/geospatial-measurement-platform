import { describe, expect, it } from 'vitest'

import {
  area,
  bytes,
  codeLabel,
  coordinate,
  duration,
  length,
  metres,
  quantityText,
  relativeDifference,
  statusLabel,
} from '@/lib/format'

describe('area', () => {
  it('chooses m², ha or km² by magnitude', () => {
    expect(area(950.5)).toEqual({ value: '950.50', unit: 'm²' })
    expect(area(25_000)).toEqual({ value: '2.5', unit: 'ha' })
    expect(area(13_784_000)).toEqual({ value: '13.784', unit: 'km²' })
  })

  it('returns null for missing or non-finite values', () => {
    expect(area(null)).toBeNull()
    expect(area(undefined)).toBeNull()
    expect(area(Number.NaN)).toBeNull()
  })
})

describe('length', () => {
  it('chooses m or km', () => {
    expect(length(999.994)).toEqual({ value: '999.99', unit: 'm' })
    expect(length(6094.78)).toEqual({ value: '6.095', unit: 'km' })
  })
})

describe('presentation helpers', () => {
  it('formats quantities and exact SI values', () => {
    expect(quantityText(area(1_476_069.7))).toBe('1.476 km²')
    expect(quantityText(null)).toBe('—')
    expect(metres(1_476_069.7, 'm²')).toBe('1,476,069.70 m²')
    expect(metres(null, 'm')).toBe('—')
  })

  it('formats the projected-vs-geodesic difference with sign and floor', () => {
    expect(relativeDifference(-4.23e-9)).toBe('< 0.0001 %')
    expect(relativeDifference(0.0013)).toBe('+0.13 %')
    expect(relativeDifference(-0.0008)).toBe('−0.080 %')
    expect(relativeDifference(null)).toBe('—')
  })

  it('formats sizes, durations and coordinates', () => {
    expect(bytes(512)).toBe('512 B')
    expect(bytes(4220)).toBe('4.1 KB')
    expect(bytes(100 * 1024 * 1024)).toBe('100.0 MB')
    expect(duration(950)).toBe('950 ms')
    expect(duration(1040)).toBe('1.0 s')
    expect(duration(125_000)).toBe('2 min 5 s')
    expect(coordinate(21.34, 'lat')).toBe('21.3400° N')
    expect(coordinate(-0.1276, 'lon')).toBe('0.1276° W')
  })

  it('labels statuses and codes', () => {
    expect(statusLabel('COMPLETED_WITH_ERRORS')).toBe('Completed with errors')
    expect(statusLabel('NOT_APPLICABLE')).toBe('Not applicable')
    expect(codeLabel('GEOMETRY_REPAIRED')).toBe('Geometry repaired')
  })
})
