import { bytes } from '@/lib/format'

/** Same syntax the API accepts for the optional CRS override. */
export const CRS_PATTERN = /^(EPSG|ESRI):\d{4,6}$/i

/** Client-side checks mirror the server's first line of defence (the server re-validates everything). */
export function validateSelection(file: File, extensions: string[], maxBytes: number | undefined): string | null {
  const dot = file.name.lastIndexOf('.')
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (!extensions.includes(extension)) return `Unsupported file type. Choose a ${extensions.join(' or ')} file.`
  if (file.size === 0) return 'The file is empty.'
  if (maxBytes && file.size > maxBytes) return `The file is ${bytes(file.size)}; the limit is ${bytes(maxBytes)}.`
  return null
}
