import { lazy, Suspense } from 'react'
import { Link, Route, Routes } from 'react-router'

import { AppShell } from '@/components/layout/app-shell'
import { Skeleton } from '@/components/ui/skeleton'
import { HomePage } from '@/pages/home-page'

// The file page carries MapLibre (~800 kB); load it only when a file is opened.
const FilePage = lazy(() => import('@/pages/file-page').then((m) => ({ default: m.FilePage })))

function NotFound() {
  return (
    <div className="pt-10">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link to="/" className="mt-3 inline-block text-sm underline">
        Go to files
      </Link>
    </div>
  )
}

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/files/:fileId"
          element={
            <Suspense fallback={<Skeleton className="mt-8 h-96 w-full" />}>
              <FilePage />
            </Suspense>
          }
        />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AppShell>
  )
}
