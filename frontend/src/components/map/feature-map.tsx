import 'maplibre-gl/dist/maplibre-gl.css'

import {
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
  type ExpressionSpecification,
  type FilterSpecification,
  type MapMouseEvent,
  setWorkerUrl,
} from 'maplibre-gl'
import { useEffect, useRef } from 'react'

import { useTheme } from '@/hooks/use-theme'
import { api } from '@/lib/api'
import { coordinate } from '@/lib/format'
import type { GeoJsonGeometry, MeasurementStatus } from '@/lib/types'

/*
 * Features are drawn from PostGIS Mapbox Vector Tiles (GET /api/files/{id}/tiles/{z}/{x}/{y}.mvt), so the
 * browser only ever receives what is visible at the current zoom - never the whole dataset as GeoJSON.
 * Polygons are International Orange, lines and points take the foreground colour; failed/unsupported
 * features are grey. Table filters are mirrored here as layer filters (no tile refetch needed).
 */

// The tile-parsing worker is served verbatim by the 'maplibre-worker' Vite plugin (see vite.config.ts).
setWorkerUrl(new URL(`${import.meta.env.BASE_URL}maplibre-gl-worker.mjs`, window.location.origin).href)

const STYLE_LIGHT = import.meta.env.VITE_BASEMAP_STYLE_LIGHT ?? 'https://tiles.openfreemap.org/styles/positron'
const STYLE_DARK = import.meta.env.VITE_BASEMAP_STYLE_DARK ?? 'https://tiles.openfreemap.org/styles/dark'
const SOURCE = 'features'
const SOURCE_LAYER = 'features'
const LAYER = { fill: 'feature-fill', outline: 'feature-outline', line: 'feature-line', point: 'feature-point' } as const
const CLICKABLE = [LAYER.fill, LAYER.line, LAYER.point]

const IS_POLYGON: ExpressionSpecification = ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false]
const IS_LINE: ExpressionSpecification = ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false]
const IS_POINT: ExpressionSpecification = ['match', ['geometry-type'], ['Point', 'MultiPoint'], true, false]
const SELECTED: ExpressionSpecification = ['boolean', ['feature-state', 'selected'], false]
const PROBLEM: ExpressionSpecification = ['in', ['get', 'status'], ['literal', ['FAILED', 'UNSUPPORTED']]]

export interface MapFilter {
  statuses: MeasurementStatus[]
  geometryTypes: string[]
}

interface Props {
  fileId: string
  bbox: [number, number, number, number] | null
  selectedId: number | null
  focus: GeoJsonGeometry | null
  filter: MapFilter
  onSelect: (featureId: number | null) => void
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function userFilter(filter: MapFilter): ExpressionSpecification {
  const clauses: ExpressionSpecification[] = []
  if (filter.statuses.length) clauses.push(['in', ['get', 'status'], ['literal', filter.statuses]])
  if (filter.geometryTypes.length) clauses.push(['in', ['get', 'geometry_type'], ['literal', filter.geometryTypes]])
  return clauses.length ? (['all', ...clauses] as ExpressionSpecification) : ['literal', true]
}

function addOverlay(map: MapLibreMap, tiles: string, filter: MapFilter) {
  const signal = cssVar('--signal')
  const foreground = cssVar('--foreground')
  const background = cssVar('--background')
  const muted = cssVar('--muted-foreground')
  const base = userFilter(filter)

  map.addSource(SOURCE, { type: 'vector', tiles: [tiles], minzoom: 0, maxzoom: 16 })
  map.addLayer({
    id: LAYER.fill,
    type: 'fill',
    source: SOURCE,
    'source-layer': SOURCE_LAYER,
    filter: ['all', IS_POLYGON, base] as FilterSpecification,
    paint: {
      'fill-color': ['case', PROBLEM, muted, signal],
      'fill-opacity': ['case', SELECTED, 0.45, 0.16],
    },
  })
  map.addLayer({
    id: LAYER.outline,
    type: 'line',
    source: SOURCE,
    'source-layer': SOURCE_LAYER,
    filter: ['all', IS_POLYGON, base] as FilterSpecification,
    paint: {
      'line-color': ['case', SELECTED, foreground, PROBLEM, muted, signal],
      'line-width': ['case', SELECTED, 2.5, 1.25],
    },
  })
  map.addLayer({
    id: LAYER.line,
    type: 'line',
    source: SOURCE,
    'source-layer': SOURCE_LAYER,
    filter: ['all', IS_LINE, base] as FilterSpecification,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['case', SELECTED, signal, PROBLEM, muted, foreground],
      'line-width': ['case', SELECTED, 4, 2],
    },
  })
  map.addLayer({
    id: LAYER.point,
    type: 'circle',
    source: SOURCE,
    'source-layer': SOURCE_LAYER,
    filter: ['all', IS_POINT, base] as FilterSpecification,
    paint: {
      'circle-radius': ['case', SELECTED, 7, 4.5],
      'circle-color': ['case', SELECTED, signal, PROBLEM, muted, foreground],
      'circle-stroke-color': background,
      'circle-stroke-width': 1.5,
    },
  })
}

function boundsOf(geometry: GeoJsonGeometry): LngLatBounds | null {
  const bounds = new LngLatBounds()
  let any = false
  const visit = (value: unknown) => {
    if (Array.isArray(value) && typeof value[0] === 'number' && typeof value[1] === 'number') {
      bounds.extend([value[0], value[1]])
      any = true
    } else if (Array.isArray(value)) {
      value.forEach(visit)
    }
  }
  visit(geometry.coordinates)
  geometry.geometries?.forEach((g) => visit(g.coordinates))
  return any ? bounds : null
}

export function FeatureMap({ fileId, bbox, selectedId, focus, filter, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const { resolved } = useTheme()
  const state = useRef({ filter, selectedId, onSelect, theme: resolved })
  state.current = { filter, selectedId, onSelect, theme: resolved }
  const tiles = api.tileUrlTemplate(fileId)

  // Create the map once per file.
  useEffect(() => {
    if (!containerRef.current) return
    const map = new MapLibreMap({
      container: containerRef.current,
      style: state.current.theme === 'dark' ? STYLE_DARK : STYLE_LIGHT,
      ...(bbox ? { bounds: bbox, fitBoundsOptions: { padding: 48, maxZoom: 17 } } : { center: [0, 20], zoom: 1 }),
      attributionControl: { compact: true },
      // The map is embedded in a scrolling page: require Ctrl/Cmd + scroll to zoom so it never hijacks scrolling.
      cooperativeGestures: true,
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')

    map.on('style.load', () => {
      addOverlay(map, tiles, state.current.filter)
      if (state.current.selectedId != null) {
        map.setFeatureState({ source: SOURCE, sourceLayer: SOURCE_LAYER, id: state.current.selectedId }, { selected: true })
      }
    })
    map.on('click', (event: MapMouseEvent) => {
      const [hit] = map.queryRenderedFeatures(event.point, { layers: CLICKABLE.filter((l) => map.getLayer(l)) })
      state.current.onSelect(hit?.id != null ? Number(hit.id) : null)
    })
    for (const layer of CLICKABLE) {
      map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''))
    }
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __geomeasureMap?: MapLibreMap }).__geomeasureMap = map
    return () => {
      map.remove()
      mapRef.current = null
    }
    // bbox only matters for the initial view; re-creating the map on every poll would be wasteful.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, tiles])

  // Basemap follows the colour theme; the overlay is re-added on 'style.load'.
  useEffect(() => {
    const map = mapRef.current
    if (map) map.setStyle(resolved === 'dark' ? STYLE_DARK : STYLE_LIGHT)
  }, [resolved])

  // Mirror table filters.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.getLayer(LAYER.fill)) return
    const base = userFilter(filter)
    map.setFilter(LAYER.fill, ['all', IS_POLYGON, base] as FilterSpecification)
    map.setFilter(LAYER.outline, ['all', IS_POLYGON, base] as FilterSpecification)
    map.setFilter(LAYER.line, ['all', IS_LINE, base] as FilterSpecification)
    map.setFilter(LAYER.point, ['all', IS_POINT, base] as FilterSpecification)
  }, [filter])

  // Selection highlight.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.getSource(SOURCE) || selectedId == null) return
    const target = { source: SOURCE, sourceLayer: SOURCE_LAYER, id: selectedId }
    map.setFeatureState(target, { selected: true })
    return () => {
      if (map.getSource(SOURCE)) map.setFeatureState(target, { selected: false })
    }
  }, [selectedId])

  // Fly to a feature chosen from the table.
  useEffect(() => {
    const map = mapRef.current
    const bounds = focus ? boundsOf(focus) : null
    if (map && bounds) map.fitBounds(bounds, { padding: 80, maxZoom: 18, duration: 600 })
  }, [focus])

  return (
    <div className="relative h-full min-h-[360px] w-full">
      {/* explicit size: MapLibre's CSS makes its container position:relative, so inset-0 would collapse it */}
      <div ref={containerRef} className="h-full w-full" role="region" aria-label="Map of the file's features" />
      {bbox && (
        <>
          <span className="numeral pointer-events-none absolute top-2 left-2 bg-background/90 px-1.5 py-0.5 text-[11px]">
            {coordinate(bbox[3], 'lat')} · {coordinate(bbox[0], 'lon')}
          </span>
          <span className="numeral pointer-events-none absolute right-2 bottom-7 bg-background/90 px-1.5 py-0.5 text-[11px]">
            {coordinate(bbox[1], 'lat')} · {coordinate(bbox[2], 'lon')}
          </span>
        </>
      )}
      {!bbox && (
        <p className="absolute inset-x-0 top-1/2 mx-auto w-fit -translate-y-1/2 bg-background px-3 py-2 text-sm text-muted-foreground">
          No feature could be placed on the map.
        </p>
      )}
    </div>
  )
}
