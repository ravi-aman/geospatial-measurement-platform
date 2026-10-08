import { ArrowLeft } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'

import { FeatureDetails } from '@/components/file/feature-details'
import { FeatureTable } from '@/components/file/feature-table'
import { FailedState, ProcessingState, Warnings } from '@/components/file/job-state'
import { TitleBlock } from '@/components/file/title-block'
import { FeatureMap } from '@/components/map/feature-map'
import { Skeleton } from '@/components/ui/skeleton'
import { hasResults, useFeature, useFile } from '@/hooks/queries'
import { ApiError } from '@/lib/api'
import type { MeasurementQuery } from '@/lib/types'

const DEFAULT_QUERY: MeasurementQuery = { sort: 'feature_id', statuses: [], geometryTypes: [] }

export function FilePage() {
  const { fileId = '' } = useParams()
  const file = useFile(fileId)
  const [query, setQuery] = useState<MeasurementQuery>(DEFAULT_QUERY)
  const [selection, setSelection] = useState<{ id: number; fromTable: boolean } | null>(null)
  const feature = useFeature(fileId, selection?.id ?? null)
  const mapFilter = useMemo(() => ({ statuses: query.statuses, geometryTypes: query.geometryTypes }), [query.statuses, query.geometryTypes])

  if (file.isPending) {
    return (
      <div className="grid gap-4 pt-8" aria-label="Loading file">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }
  if (file.isError) {
    const notFound = file.error instanceof ApiError && file.error.status === 404
    return (
      <div className="pt-10">
        <p role="alert" className="border-l-2 border-signal pl-3">
          {notFound ? 'This file does not exist (or the link is wrong).' : `Could not load the file: ${file.error.message}`}
        </p>
        <Link to="/" className="mt-4 inline-flex items-center gap-1 text-sm hover:underline">
          <ArrowLeft className="size-4" /> All files
        </Link>
      </div>
    )
  }

  const data = file.data
  const ready = hasResults(data) && data.summary != null
  return (
    <div className="flex flex-col gap-6 pt-6">
      <Link to="/" className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All files
      </Link>

      <div>
        <TitleBlock file={data} />
        {(data.status === 'PENDING' || data.status === 'PROCESSING') && <ProcessingState file={data} />}
        {data.status === 'FAILED' && <FailedState file={data} />}
        <Warnings warnings={data.warnings} />
      </div>

      {ready && data.summary && (
        <>
          <div className="grid border border-rule lg:grid-cols-12">
            <div className="h-[460px] border-b border-border lg:col-span-8 lg:h-[560px] lg:border-r lg:border-b-0">
              <FeatureMap
                fileId={data.id}
                bbox={data.summary.bbox}
                selectedId={selection?.id ?? null}
                focus={selection?.fromTable ? (feature.data?.geometry ?? null) : null}
                filter={mapFilter}
                onSelect={(id) => setSelection(id == null ? null : { id, fromTable: false })}
              />
            </div>
            <aside aria-label="Selected feature" className="max-h-[560px] overflow-auto lg:col-span-4">
              <FeatureDetails
                feature={selection ? feature.data : undefined}
                loading={selection != null && feature.isPending}
                onClose={() => setSelection(null)}
              />
            </aside>
          </div>

          <FeatureTable
            fileId={data.id}
            summary={data.summary}
            query={query}
            onQueryChange={setQuery}
            selectedId={selection?.id ?? null}
            onSelect={(id) => setSelection({ id, fromTable: true })}
          />
        </>
      )}
    </div>
  )
}
