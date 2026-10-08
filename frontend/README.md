# Geomeasure frontend

Single-page app for uploading a KML or zipped Shapefile, following processing, and inspecting every feature on a map
and in a table. React 19, Vite 8, TypeScript 6, Tailwind CSS 4, shadcn/ui (Radix primitives, restyled), TanStack
Query 5 and Virtual 3, MapLibre GL JS 6, react-router 8, sonner. Architecture overview:
[docs/architecture/frontend.md](../docs/architecture/frontend.md). Screenshots (sample `mine_site_survey.kml`):
[title block, dark](../docs/assets/title-block-dark.jpg), [map, light](../docs/assets/map-light.jpg),
[feature table](../docs/assets/feature-table.jpg).

## Commands

```bash
npm ci                 # install exactly what package-lock.json pins
npm run dev            # dev server on http://localhost:5173, proxies the API to http://localhost:8000
npm run lint           # oxlint --deny-warnings
npm run typecheck      # tsc -b
npm test               # vitest run (jsdom), 18 tests
npm run build          # tsc -b && vite build -> dist/
npm run preview        # serve dist/ locally
```

Node 24 (as in CI and the Docker build). The backend must be running for `npm run dev` to show data; see
[docs/deployment/local.md](../docs/deployment/local.md).

## Environment variables

| Variable | When | Default | Meaning |
|---|---|---|---|
| `VITE_API_BASE_URL` | build time (`import.meta.env`) | empty | Origin of the API when it is not same-origin, e.g. `https://api.example.com`. Empty means relative URLs (`/api/...`), which works with the Vite proxy and with the nginx image. A trailing slash is stripped. |
| `VITE_BASEMAP_STYLE_LIGHT` | build time | `https://tiles.openfreemap.org/styles/positron` | MapLibre style for the light theme. |
| `VITE_BASEMAP_STYLE_DARK` | build time | `https://tiles.openfreemap.org/styles/dark` | MapLibre style for the dark theme. |
| `VITE_DEV_API_TARGET` | dev server only (`process.env` in `vite.config.ts`) | `http://localhost:8000` | Proxy target for `/api`, `/health`, `/ready`, `/docs`, `/openapi.json`. Set it in the shell; `.env` files are not loaded into `process.env` for the Vite config. |

## Why it exists

The API answers in JSON; reviewers and users need to see what was extracted and measured: where each feature is, which
CRS was used, what failed and why. The frontend is a thin client over the public API (it uses no private endpoint), so
it doubles as a worked example of consuming the API correctly: error envelope, polling, keyset pagination, vector tiles
and idempotent uploads.

## How it works

| Route | Page | Content |
|---|---|---|
| `/` | `pages/home-page.tsx` | Upload panel (drag and drop, optional CRS override, progress) and recent files (infinite list, refreshed every 2 s while any item is unfinished). |
| `/files/:fileId` | `pages/file-page.tsx` (lazy-loaded) | Title block, processing or failure state, file warnings, map with feature details panel, virtualised feature table. |
| `*` | inline | Not-found page. |

- **Data access** (`lib/api.ts`): `fetch` wrapper that maps the backend error envelope to `ApiError` (`status`, `code`,
  `details`, `requestId`); network failures become `NETWORK_ERROR`. Uploads use `XMLHttpRequest` because `fetch` cannot
  report upload progress; each upload sends an `Idempotency-Key` from `crypto.randomUUID()`.
- **Server state** (`hooks/queries.ts`): TanStack Query. File status polls every 1 s until a terminal status; 404s and
  other 4xx responses are not retried. Measurements use `useInfiniteQuery` over the API's `next_cursor`, with
  server-side filtering and sorting; finished results are cached with `staleTime: Infinity`.
- **Map** (`components/map/feature-map.tsx`): MapLibre draws the file from the API's vector tiles
  (`/api/files/{id}/tiles/{z}/{x}/{y}.mvt`, source layer `features`). Table filters are mirrored as MapLibre layer
  filters (no refetch); selection uses feature state; choosing a row fetches that feature's GeoJSON and fits the map to
  it. `cooperativeGestures` requires Ctrl/Cmd + scroll to zoom so the page scroll is not hijacked.
- **Table** (`components/file/feature-table.tsx`): 44 px rows virtualised with TanStack Virtual; the next page is
  fetched when the last rendered row is within 20 rows of the end.
- **Theme** (`hooks/use-theme.tsx`): light, dark or system; the explicit choice is kept in `localStorage`
  (`geomeasure.theme`) as a non-essential convenience; storage failures are ignored. The basemap style follows the
  theme.

## Inputs

- User actions: a selected or dropped file (`.kml`/`.zip`, checked client-side against `/api/capabilities` for
  extension, emptiness and size), an optional CRS override (`/^(EPSG|ESRI):\d{4,6}$/i`, the same syntax the API
  accepts), filters, sort order, map and row selection.
- API responses: capabilities, file list, file info, measurement pages, single features (GeoJSON), vector tiles.
- Build-time configuration (table above).

## Outputs

- `POST /api/files/` (multipart: `file`, optional `crs`) and read-only `GET` requests. Nothing else is written,
  except the theme preference in `localStorage`.
- Rendered views: measurements in a readable unit (m², ha or km²; m or km) with the exact SI value alongside,
  projected-vs-geodesic difference, issues, errors and attributes.

## Failure modes

| Situation | Behaviour |
|---|---|
| API unreachable | `NETWORK_ERROR` message ("The API is unreachable...") inline or in a toast. |
| Upload rejected (4xx) | The API's message is shown inline and in a toast; `requestId` is available on the error object. |
| File id unknown | "This file does not exist (or the link is wrong)." No retries on 404. |
| Processing failed | Failure panel with the error code and message; states whether the failure was temporary (re-upload retries it) or caused by the file. |
| Results requested too early | Not possible from the UI: measurements are only queried once the file status has results. |
| Non-JSON error bodies (e.g. a proxy's 502 page) | `HTTP_ERROR` with the status code. |
| Feature without a WGS 84 geometry (unknown CRS) | Listed in the table; not drawn on the map; when the whole file has no placeable geometry the map says so. |

## Design decisions

- **Design system: Swiss style.** White/neutral surfaces, near-black type, 1 px rules instead of shadows, square
  corners (all radii 0), and a **single accent: International Orange `#FF4F00`** (`--signal`; `#FF6A26` in dark mode),
  used for failures, warnings, focus rings, progress and polygons on the map. **Archivo** (variable, self-hosted via
  `@fontsource-variable/archivo`) is the only typeface; its width axis provides condensed numerals (`.numeral`) for
  measurements.
- **Title block.** The file page opens with a ruled grid modelled on the title block of a survey drawing: file name and
  status, coordinate reference (with source), feature count and breakdown, sum of polygon areas, sum of line lengths,
  processing time and measurement strategy.
- **Status is never colour-only.** Every status has a text label and a distinct mark shape (outline, filled, pulsing,
  rotated); tested in `status.test.tsx`.
- **Never download the whole dataset.** Vector tiles for the map, keyset pages plus virtualisation for the table.
- **Lazy-load the map.** The file page (with MapLibre) is a separate chunk; the initial bundle is 452 kB (146 kB gzip).
- **MapLibre worker served verbatim.** A small Vite plugin (`maplibre-worker` in `vite.config.ts`) serves
  `maplibre-gl-worker.mjs` in dev and emits it as a build asset; the app calls `setWorkerUrl()`. Vite's dependency
  pre-bundling broke the worker's relative URL.

## Scaling considerations

- Table memory and DOM size are bounded by the virtualiser and page size (200 rows per request; the API caps at 500).
- Map payload is bounded by the tile endpoint (at most 20,000 features per tile, largest first; tile source `maxzoom`
  16, overzoomed beyond).
- Polling cost: one small `GET` per second per open unfinished file, stopping at a terminal status.
- The client holds no global store beyond the TanStack Query cache.

## Security considerations

- Attribute values and messages are rendered as React text, never as HTML.
- No authentication exists in the app or the API; anyone who can reach the UI can upload and read files (see
  [docs/architecture/security.md](../docs/architecture/security.md)).
- External requests: the API, and basemap styles/tiles/fonts from OpenFreeMap unless the style variables point
  elsewhere. No analytics.
- Client-side validation is a convenience only; the API re-validates everything.
- The idempotency key is generated once per upload call. The UI does not retry uploads automatically, so today the key
  only protects against a duplicated delivery of the same request.

## Testing

18 Vitest tests in jsdom: `lib/api.test.ts` (8: error envelope parsing, query construction, error mapping, network
failure, file-selection validation, CRS pattern parity), `lib/format.test.ts` (7: unit selection and formatting),
`components/status.test.tsx` (3: labels and accent use). CI runs lint, typecheck, tests and build on every push.

Not covered: the map, table, upload panel and query hooks have no automated tests, and there is no browser end-to-end
suite. The UI was verified manually in a browser (which is how a MapLibre container-height bug was found: MapLibre's CSS
makes its container `position: relative`, so the container needs an explicit size).

## Future improvements

- Browser end-to-end tests (e.g. Playwright) for upload -> processing -> map/table.
- Component tests for the table and upload panel.
- Cancel button for uploads (the API client already accepts an `AbortSignal`).
- Export of measurements (CSV/GeoJSON) from the UI.
- Content-Security-Policy for the static hosting, and self-hosted basemap styles.
