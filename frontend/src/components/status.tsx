import { cn } from '@/lib/utils'
import { statusLabel } from '@/lib/format'
import type { JobStatus, MeasurementStatus } from '@/lib/types'

type Status = JobStatus | MeasurementStatus

// Shape + label carry the meaning; colour only reinforces it (accessible without colour vision).
const MARK: Record<Status, string> = {
  PENDING: 'border border-foreground bg-transparent',
  PROCESSING: 'bg-foreground animate-pulse',
  COMPLETED: 'bg-foreground',
  COMPLETED_WITH_ERRORS: 'bg-signal',
  FAILED: 'bg-signal rotate-45',
  MEASURED: 'bg-foreground',
  NOT_APPLICABLE: 'border border-muted-foreground bg-transparent',
  UNSUPPORTED: 'bg-muted-foreground',
}

export function StatusMark({ status, className }: { status: Status; className?: string }) {
  return <span aria-hidden="true" className={cn('inline-block size-2 shrink-0', MARK[status], className)} />
}

export function StatusLabel({ status, className }: { status: Status; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 whitespace-nowrap', className)}>
      <StatusMark status={status} />
      <span className={cn(status === 'FAILED' || status === 'COMPLETED_WITH_ERRORS' ? 'text-signal' : undefined)}>
        {statusLabel(status)}
      </span>
    </span>
  )
}
