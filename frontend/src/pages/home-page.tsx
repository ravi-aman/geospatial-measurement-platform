import { RecentFiles } from '@/components/files/recent-files'
import { UploadPanel } from '@/components/upload/upload-panel'

export function HomePage() {
  return (
    <div className="grid gap-12 pt-10 lg:grid-cols-12 lg:gap-0">
      <div className="lg:col-span-12 lg:pb-10">
        <h1 className="max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Area and length for every feature in a KML or Shapefile.
        </h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">
          Each feature is projected to a local metric coordinate system before measuring, and checked against an
          ellipsoidal geodesic reference. Points are listed without a measurement.
        </p>
      </div>
      <div className="rule-top pt-6 lg:col-span-4 lg:pr-10">
        <UploadPanel />
      </div>
      <div className="rule-top pt-6 lg:col-span-8 lg:border-l lg:border-l-border lg:pl-10">
        <RecentFiles />
      </div>
    </div>
  )
}
