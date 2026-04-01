import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60_000,
  headers: { 'Content-Type': 'application/json' },
})

// Response interceptor for unified error handling
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg =
      err.response?.data?.detail ||
      err.response?.data?.message ||
      err.message ||
      'Unknown error'
    return Promise.reject(new Error(msg))
  },
)

/** GET /api/files */
export async function getFiles() {
  const { data } = await api.get('/files')
  return data
}

/** GET /api/files/:id */
export async function getFile(id) {
  const { data } = await api.get(`/files/${id}`)
  return data
}

/**
 * POST /api/files/upload
 * @param {File} file
 * @param {(pct: number) => void} onProgress
 */
export async function uploadFile(file, onProgress) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress(e) {
      if (e.total && onProgress) {
        onProgress(Math.round((e.loaded * 100) / e.total))
      }
    },
  })
  return data
}

/** DELETE /api/files/:id */
export async function deleteFile(id) {
  const { data } = await api.delete(`/files/${id}`)
  return data
}

/**
 * GET /api/files/:id/download – triggers a native browser download.
 * Uses a direct link instead of axios to avoid timeout and memory issues
 * with large files.
 * @param {string} id
 * @param {string} filename
 */
export async function downloadFile(id, filename) {
  const a = document.createElement('a')
  a.href = `/api/files/${id}/download`
  a.download = filename || 'download'
  a.click()
}

/** GET /api/stats */
export async function getStats() {
  const { data } = await api.get('/stats')
  return data
}

/** GET /api/health */
export async function getHealth() {
  const { data } = await api.get('/health')
  return data
}

export default api
