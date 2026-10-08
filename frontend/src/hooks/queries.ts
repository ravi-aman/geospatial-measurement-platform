import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, uploadFile, type UploadOptions } from '@/lib/api'
import { RESULT_STATUSES, TERMINAL_STATUSES, type FileInfo, type MeasurementQuery } from '@/lib/types'

export const queryKeys = {
  capabilities: ['capabilities'] as const,
  files: ['files'] as const,
  file: (id: string) => ['file', id] as const,
  measurements: (id: string, query: MeasurementQuery) => ['measurements', id, query] as const,
  feature: (id: string, featureId: number) => ['feature', id, featureId] as const,
}

const POLL_MS = 1000

export function useCapabilities() {
  return useQuery({ queryKey: queryKeys.capabilities, queryFn: api.capabilities, staleTime: Infinity })
}

export function useRecentFiles() {
  return useInfiniteQuery({
    queryKey: queryKeys.files,
    queryFn: ({ pageParam }) => api.listFiles(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    // Keep the list fresh while anything in it is still processing.
    refetchInterval: (query) =>
      query.state.data?.pages.some((p) => p.items.some((f) => !TERMINAL_STATUSES.includes(f.status))) ? POLL_MS * 2 : false,
  })
}

/** File status, polled every second until processing reaches a terminal state. */
export function useFile(id: string) {
  return useQuery({
    queryKey: queryKeys.file(id),
    queryFn: () => api.getFile(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_STATUSES.includes(status) ? false : POLL_MS
    },
    retry: (failures, error) => !('status' in error && (error as { status: number }).status === 404) && failures < 3,
  })
}

export function hasResults(file: FileInfo | undefined): boolean {
  return !!file && RESULT_STATUSES.includes(file.status)
}

/** Measurements as an infinite list over the API's keyset cursors (server-side filtering and sorting). */
export function useMeasurements(id: string, query: MeasurementQuery, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.measurements(id, query),
    queryFn: ({ pageParam }) => api.measurements(id, query, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.page.next_cursor,
    enabled,
    staleTime: Infinity, // results of a finished job never change
  })
}

export function useFeature(id: string, featureId: number | null) {
  return useQuery({
    queryKey: queryKeys.feature(id, featureId ?? -1),
    queryFn: () => api.feature(id, featureId as number),
    enabled: featureId != null,
    staleTime: Infinity,
  })
}

export function useUpload() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (options: UploadOptions) => uploadFile(options),
    onSuccess: (file) => {
      client.setQueryData(queryKeys.file(file.id), file)
      void client.invalidateQueries({ queryKey: queryKeys.files })
    },
  })
}
