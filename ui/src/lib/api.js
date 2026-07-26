// Single place that knows how to reach the node. The UI is served by the same
// FastAPI process in production, so relative paths are the default; VITE_API_BASE
// lets a dev server or desktop shell point at a remote node instead.
const CONFIGURED_BASE = (import.meta.env.VITE_API_BASE ?? '').trim().replace(/\/+$/, '')

export function apiBase() {
  return CONFIGURED_BASE
}

export function apiUrl(path) {
  return `${apiBase()}${path}`
}

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers ?? {}),
    },
  })
  if (!response.ok) {
    const raw = await response.text()
    let message = raw
    try {
      const parsed = JSON.parse(raw)
      message = parsed.detail || parsed.reply || parsed.error || raw
    } catch {
      // Keep the raw body — it is usually already a readable message.
    }
    throw new Error(message || `HTTP ${response.status}`)
  }
  if (response.status === 204) return null
  return response.json()
}

// ── files ───────────────────────────────────────────────────────────
export const getFileTree = () => request('/api/files/tree')
export const browseFolder = (path = '') =>
  request(`/api/files/browse?path=${encodeURIComponent(path)}`)
export const getFile = (id) => request(`/api/files/${id}`)
export const deleteFile = (id) => request(`/api/files/${id}`, { method: 'DELETE' })
export const updateFile = (id, patch) =>
  request(`/api/files/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })
export const createFolder = (path) =>
  request('/api/folders', { method: 'POST', body: JSON.stringify({ path }) })
export const deleteFolder = (path) =>
  request(`/api/folders?path=${encodeURIComponent(path)}`, { method: 'DELETE' })

export function downloadUrl(id) {
  return apiUrl(`/api/files/${id}/download`)
}

export async function uploadFile(file, folder = '', onProgress) {
  // XHR rather than fetch: upload progress events have no fetch equivalent.
  const form = new FormData()
  form.append('file', file)
  form.append('folder', folder)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl('/api/files/upload'))
    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch {
          resolve({})
        }
        return
      }
      let message = `Upload failed (HTTP ${xhr.status})`
      try {
        message = JSON.parse(xhr.responseText).detail || message
      } catch {
        // Leave the status-based message.
      }
      reject(new Error(message))
    }
    xhr.onerror = () => reject(new Error('Upload failed: network error'))
    xhr.send(form)
  })
}

// ── system / config / agent ─────────────────────────────────────────
export const getSystemOverview = () => request('/api/system/overview')
export const getStats = () => request('/api/stats')
export const getPeers = () => request('/api/peers')
export const getConfig = () => request('/api/config')
export const putConfig = (updates) =>
  request('/api/config', { method: 'PUT', body: JSON.stringify({ updates }) })
export const getConfigAudit = (limit = 12) => request(`/api/config/audit?limit=${limit}`)
export const getProviders = () => request('/api/agent/providers')

export const postChat = (payload) =>
  request('/api/chat', { method: 'POST', body: JSON.stringify(payload) })

export function eventsSocket() {
  const base = apiBase()
  if (base) return new WebSocket(`${base.replace(/^http/i, 'ws')}/ws`)
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return new WebSocket(`${scheme}://${window.location.host}/ws`)
}
