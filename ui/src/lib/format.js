export function fmtBytes(value) {
  if (value == null || Number.isNaN(value)) return 'n/a'
  if (Math.abs(value) < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB', 'PB']
  let current = value / 1024
  let unit = units[0]
  for (let index = 1; index < units.length && Math.abs(current) >= 1024; index++) {
    current /= 1024
    unit = units[index]
  }
  return `${current >= 10 ? current.toFixed(0) : current.toFixed(1)} ${unit}`
}

export function fmtPercent(value) {
  if (value == null || Number.isNaN(value)) return 'n/a'
  return `${value.toFixed(value >= 10 ? 0 : 1)}%`
}

export function fmtUptime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return 'n/a'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  if (days > 0) return `${days}d ${hours}h`
  const minutes = Math.floor((seconds % 3600) / 60)
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
}

export function fmtDate(value) {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = Date.now()
  const delta = (now - date.getTime()) / 1000
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  if (delta < 86400 * 7) return `${Math.floor(delta / 86400)}d ago`
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function statusTone(status) {
  const lowered = String(status ?? '').toLowerCase()
  if (['healthy', 'ok', 'online', 'ready', 'running', 'stored', 'complete'].includes(lowered)) return 'ok'
  if (['warning', 'warn', 'starting', 'degraded', 'processing', 'probation'].includes(lowered)) return 'warn'
  if (['critical', 'unhealthy', 'missing', 'offline', 'error', 'evicted', 'suspended'].includes(lowered)) return 'bad'
  return 'neutral'
}

const EXT_KINDS = {
  image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'heic', 'avif'],
  video: ['mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v'],
  audio: ['mp3', 'flac', 'wav', 'ogg', 'm4a', 'aac'],
  archive: ['zip', 'tar', 'gz', 'bz2', 'xz', 'rar', '7z', 'zst'],
  code: ['js', 'jsx', 'ts', 'tsx', 'py', 'go', 'rs', 'c', 'h', 'cpp', 'java', 'rb', 'sh', 'json', 'yml', 'yaml', 'toml'],
  doc: ['md', 'txt', 'pdf', 'doc', 'docx', 'rtf', 'odt', 'csv', 'xls', 'xlsx'],
}

export function fileKind(name) {
  const ext = String(name ?? '').split('.').pop()?.toLowerCase() ?? ''
  for (const [kind, extensions] of Object.entries(EXT_KINDS)) {
    if (extensions.includes(ext)) return kind
  }
  return 'file'
}
