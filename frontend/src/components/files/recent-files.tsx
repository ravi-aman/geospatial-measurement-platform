import { Link } from 'react-router'

import { StatusLabel } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useRecentFiles } from '@/hooks/queries'
import { bytes, count, timestamp } from '@/lib/format'

export function RecentFiles() {
  const files = useRecentFiles()
  const items = files.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <section aria-labelledby="recent-heading" className="flex flex-col gap-5">
      <h2 id="recent-heading" className="text-lg font-semibold tracking-tight">
        Recent files
      </h2>

      {files.isPending && (
        <div className="grid gap-3" aria-label="Loading files">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      )}

      {files.isError && (
        <p role="alert" className="border-l-2 border-signal pl-3 text-sm">
          Could not load files: {files.error.message}
        </p>
      )}

      {files.isSuccess && items.length === 0 && (
        <p className="border-t border-border pt-4 text-sm text-muted-foreground">
          No files yet. Upload a KML or a zipped Shapefile to see it here.
        </p>
      )}

      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="rule-bottom text-left text-xs text-muted-foreground">
                <th scope="col" className="py-2 pr-4 font-medium">File</th>
                <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Features</th>
                <th scope="col" className="hidden py-2 pr-4 font-medium xl:table-cell">CRS</th>
                <th scope="col" className="hidden py-2 pr-4 text-right font-medium xl:table-cell">Size</th>
                <th scope="col" className="py-2 font-medium">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {items.map((file) => (
                <tr key={file.id} className="border-b border-border hover:bg-muted">
                  <td className="max-w-[220px] py-2.5 pr-4">
                    <Link to={`/files/${file.id}`} className="block truncate font-medium hover:underline" title={file.filename}>
                      {file.filename}
                    </Link>
                    <span className="text-xs text-muted-foreground">{file.format === 'KML' ? 'KML' : 'Shapefile'}</span>
                  </td>
                  <td className="py-2.5 pr-4">
                    <StatusLabel status={file.status} />
                  </td>
                  <td className="tabular py-2.5 pr-4 text-right">{count(file.feature_count)}</td>
                  <td className="hidden py-2.5 pr-4 xl:table-cell">{file.crs ?? '—'}</td>
                  <td className="tabular hidden py-2.5 pr-4 text-right xl:table-cell">{bytes(file.size_bytes)}</td>
                  <td className="py-2.5 whitespace-nowrap text-muted-foreground">{timestamp(file.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {files.hasNextPage && (
        <Button variant="outline" onClick={() => void files.fetchNextPage()} disabled={files.isFetchingNextPage} className="self-start">
          {files.isFetchingNextPage ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </section>
  )
}
