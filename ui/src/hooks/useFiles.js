import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getFiles, getFile, uploadFile, deleteFile, getStats } from '../api/client.js'
import toast from 'react-hot-toast'

const FILES_KEY  = ['files']
const STATS_KEY  = ['stats']

/** All files, auto-refreshed every 5 s */
export function useFiles() {
  return useQuery({
    queryKey: FILES_KEY,
    queryFn: getFiles,
    refetchInterval: 5_000,
  })
}

/** Single file, polls while status is processing */
export function useFile(id) {
  return useQuery({
    queryKey: ['file', id],
    queryFn:  () => getFile(id),
    enabled:  !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'processing' ? 2_000 : false
    },
  })
}

/** Upload mutation */
export function useUploadFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file, onProgress }) => uploadFile(file, onProgress),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: FILES_KEY })
      qc.invalidateQueries({ queryKey: STATS_KEY })
      toast.success(`"${data.name}" is being processed`)
    },
    onError: (err) => {
      toast.error(`Upload failed: ${err.message}`)
    },
  })
}

/** Delete mutation */
export function useDeleteFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => deleteFile(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FILES_KEY })
      qc.invalidateQueries({ queryKey: STATS_KEY })
      toast.success('File deleted')
    },
    onError: (err) => {
      toast.error(`Delete failed: ${err.message}`)
    },
  })
}

/** System stats */
export function useStats() {
  return useQuery({
    queryKey: STATS_KEY,
    queryFn:  getStats,
    refetchInterval: 10_000,
  })
}
