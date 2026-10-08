/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origin of the API when it is not served from the same origin (e.g. https://api.example.com). */
  readonly VITE_API_BASE_URL?: string
  /** MapLibre style URLs for the basemap (default: OpenFreeMap positron / dark, no API key needed). */
  readonly VITE_BASEMAP_STYLE_LIGHT?: string
  readonly VITE_BASEMAP_STYLE_DARK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
