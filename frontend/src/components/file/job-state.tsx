import { count } from '@/lib/format'
import type { FileInfo, Issue } from '@/lib/types'
import { codeLabel } from '@/lib/format'

export function ProcessingState({ file }: { file: FileInfo }) {
  const { processed_features: done, total_features: total } = file.job.progress
  const fraction = total ? Math.min(done / total, 1) : null
  return (
    <section aria-live="polite" className="flex flex-col gap-3 border-b border-border py-6">
      <p className="text-sm">
        {file.status === 'PENDING'
          ? 'Queued. A worker will pick this file up shortly.'
          : total
            ? `Processing: ${count(done)} of ${count(total)} features.`
            : `Processing: ${count(done)} features so far.`}
      </p>
      <div className="h-px w-full max-w-xl bg-border" role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={fraction != null ? Math.round(fraction * 100) : undefined} aria-label="Processing progress">
        <div
          className={fraction == null ? 'h-px w-1/4 animate-pulse bg-signal' : 'h-px bg-signal transition-[width]'}
          style={fraction != null ? { width: `${Math.round(fraction * 100)}%` } : undefined}
        />
      </div>
      {file.job.attempts > 1 && (
        <p className="text-xs text-muted-foreground">
          Attempt {file.job.attempts} of {file.job.max_attempts}
        </p>
      )}
    </section>
  )
}

export function FailedState({ file }: { file: FileInfo }) {
  const error = file.job.error
  return (
    <section role="alert" className="flex flex-col gap-2 border-b border-border py-6">
      <p className="font-medium text-signal">Processing failed{error ? `: ${codeLabel(error.code)}` : ''}</p>
      {error && <p className="max-w-3xl text-sm">{error.message}</p>}
      <p className="text-xs text-muted-foreground">
        {error?.retryable
          ? `The failure looked temporary and was retried ${file.job.attempts} time(s). Uploading the file again will retry it.`
          : 'The file itself could not be processed; uploading it again will not change the result.'}
      </p>
    </section>
  )
}

export function Warnings({ warnings }: { warnings: Issue[] }) {
  if (!warnings.length) return null
  return (
    <section aria-labelledby="warnings-heading" className="border-b border-border py-4">
      <h2 id="warnings-heading" className="mb-2 text-xs text-muted-foreground">
        File notes ({warnings.length})
      </h2>
      <ul className="grid gap-2 lg:grid-cols-2">
        {warnings.map((w) => (
          <li key={`${w.code}-${w.message}`} className="border-l-2 border-signal pl-3 text-sm">
            <span className="font-medium">{codeLabel(w.code)}.</span> {w.message}
          </li>
        ))}
      </ul>
    </section>
  )
}
