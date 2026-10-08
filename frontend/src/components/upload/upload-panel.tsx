import { FileUp, X } from 'lucide-react'
import { useId, useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCapabilities, useUpload } from '@/hooks/queries'
import { ApiError } from '@/lib/api'
import { bytes } from '@/lib/format'
import { CRS_PATTERN, validateSelection } from '@/lib/upload-validation'
import { cn } from '@/lib/utils'

const FALLBACK_EXTENSIONS = ['.kml', '.zip']

export function UploadPanel() {
  const navigate = useNavigate()
  const capabilities = useCapabilities()
  const upload = useUpload()
  const inputRef = useRef<HTMLInputElement>(null)
  const crsId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [crs, setCrs] = useState('')
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const extensions = capabilities.data?.extensions ?? FALLBACK_EXTENSIONS
  const maxBytes = capabilities.data?.max_upload_bytes
  const crsInvalid = crs.trim() !== '' && !CRS_PATTERN.test(crs.trim())

  function choose(candidate: File | undefined) {
    if (!candidate) return
    const problem = validateSelection(candidate, extensions, maxBytes)
    setError(problem)
    setFile(problem ? null : candidate)
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    choose(event.dataTransfer.files[0])
  }

  function submit() {
    if (!file || crsInvalid) return
    setProgress(0)
    upload.mutate(
      { file, crs, onProgress: setProgress },
      {
        onSuccess: (result) => {
          toast.success(`${result.filename} uploaded`, { description: 'Processing has started.' })
          void navigate(`/files/${result.id}`)
        },
        onError: (err) => {
          const message = err instanceof ApiError ? err.message : 'Upload failed.'
          setError(message)
          toast.error('Upload rejected', { description: message })
        },
      },
    )
  }

  return (
    <section aria-labelledby="upload-heading" className="flex flex-col gap-5">
      <div>
        <h2 id="upload-heading" className="text-lg font-semibold tracking-tight">
          Upload a dataset
        </h2>
        <span className="text-sm text-muted-foreground">
          {extensions.join(', ')}
          {maxBytes ? ` · up to ${bytes(maxBytes)}` : ''}
        </span>
      </div>

      <label
        htmlFor="file-input"
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'group flex min-h-48 cursor-pointer flex-col justify-between border border-dashed border-foreground/40 p-5 transition-colors',
          'hover:border-foreground focus-within:border-foreground',
          dragging && 'border-signal bg-signal/5',
        )}
      >
        <FileUp className="size-6 text-muted-foreground group-hover:text-foreground" aria-hidden="true" />
        <div>
          {file ? (
            <p className="text-base font-medium break-all">
              {file.name} <span className="font-normal text-muted-foreground">· {bytes(file.size)}</span>
            </p>
          ) : (
            <p className="text-base">Drop a file here, or click to browse.</p>
          )}
          <p className="mt-1 text-sm text-muted-foreground">
            KML, or a .zip containing one Shapefile (.shp, .shx, .dbf and ideally .prj).
          </p>
        </div>
        <input
          id="file-input"
          ref={inputRef}
          type="file"
          accept={extensions.join(',')}
          className="sr-only"
          onChange={(e) => choose(e.target.files?.[0])}
        />
      </label>

      <div className="grid gap-2">
        <Label htmlFor={crsId}>CRS override (optional)</Label>
        <Input
          id={crsId}
          value={crs}
          onChange={(e) => setCrs(e.target.value)}
          placeholder="EPSG:32643"
          aria-invalid={crsInvalid}
          aria-describedby={`${crsId}-help`}
          autoComplete="off"
          spellCheck={false}
        />
        <p id={`${crsId}-help`} className={cn('text-sm', crsInvalid ? 'text-signal' : 'text-muted-foreground')}>
          {crsInvalid
            ? 'Use an authority code such as EPSG:32643.'
            : 'Only needed when a Shapefile has no .prj or a wrong one. KML is always WGS 84.'}
        </p>
      </div>

      {error && (
        <p role="alert" className="border-l-2 border-signal pl-3 text-sm">
          {error}
        </p>
      )}

      {upload.isPending && (
        <div className="grid gap-1" aria-live="polite">
          <div className="h-px w-full bg-border">
            <div className="h-px bg-signal transition-[width]" style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
          <span className="numeral text-sm text-muted-foreground">Uploading {Math.round(progress * 100)} %</span>
        </div>
      )}

      <div className="flex gap-2">
        <Button onClick={submit} disabled={!file || crsInvalid || upload.isPending} className="h-10 px-5">
          {upload.isPending ? 'Uploading…' : 'Upload and measure'}
        </Button>
        {file && !upload.isPending && (
          <Button
            variant="ghost"
            className="h-10"
            onClick={() => {
              setFile(null)
              setError(null)
              if (inputRef.current) inputRef.current.value = ''
            }}
          >
            <X /> Clear
          </Button>
        )}
      </div>
    </section>
  )
}
