import {
  FileText,
  FileImage,
  FileVideo,
  FileAudio,
  FileCode,
  FileArchive,
  FileSpreadsheet,
  File,
  Presentation,
} from 'lucide-react'
import { format, parseISO, formatDistanceToNow } from 'date-fns'

/** Format bytes to human-readable string */
export function formatBytes(bytes, decimals = 1) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(decimals))} ${sizes[i]}`
}

/** Return a Lucide icon component based on file extension */
export function getFileIconComponent(filename) {
  const ext = (filename || '').split('.').pop().toLowerCase()
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'avif']
  const videoExts = ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'm4v', 'wmv', 'ogv']
  const audioExts = ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'opus', 'wma']
  const codeExts  = ['js', 'jsx', 'ts', 'tsx', 'py', 'go', 'rs', 'c', 'cpp', 'h', 'java', 'rb', 'php', 'sh', 'yaml', 'yml', 'json', 'toml', 'html', 'css', 'scss']
  const archiveExts = ['zip', 'tar', 'gz', 'bz2', 'xz', '7z', 'rar', 'zst']
  const sheetExts = ['xls', 'xlsx', 'csv', 'ods', 'numbers']
  const presentExts = ['ppt', 'pptx', 'odp', 'key']
  const docExts = ['pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'md']

  if (imageExts.includes(ext)) return FileImage
  if (videoExts.includes(ext)) return FileVideo
  if (audioExts.includes(ext)) return FileAudio
  if (codeExts.includes(ext))  return FileCode
  if (archiveExts.includes(ext)) return FileArchive
  if (sheetExts.includes(ext)) return FileSpreadsheet
  if (presentExts.includes(ext)) return Presentation
  if (docExts.includes(ext))   return FileText
  return File
}

/** Return a category string for the file type */
export function getFileType(filename) {
  const ext = (filename || '').split('.').pop().toLowerCase()
  const map = {
    image:    ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'avif'],
    video:    ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'm4v', 'wmv', 'ogv'],
    audio:    ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'opus', 'wma'],
    code:     ['js', 'jsx', 'ts', 'tsx', 'py', 'go', 'rs', 'c', 'cpp', 'h', 'java', 'rb', 'php', 'sh', 'yaml', 'yml', 'json', 'toml', 'html', 'css', 'scss'],
    archive:  ['zip', 'tar', 'gz', 'bz2', 'xz', '7z', 'rar', 'zst'],
    document: ['pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'md'],
    spreadsheet: ['xls', 'xlsx', 'csv', 'ods', 'numbers'],
    presentation: ['ppt', 'pptx', 'odp', 'key'],
  }
  for (const [type, exts] of Object.entries(map)) {
    if (exts.includes(ext)) return type
  }
  return 'file'
}

/** Format ISO date string to human-readable */
export function formatDate(dateString) {
  if (!dateString) return '—'
  try {
    const date = parseISO(dateString)
    return format(date, 'MMM d, yyyy')
  } catch {
    return dateString
  }
}

/** Format ISO date string to relative time */
export function formatRelativeDate(dateString) {
  if (!dateString) return '—'
  try {
    const date = parseISO(dateString)
    return formatDistanceToNow(date, { addSuffix: true })
  } catch {
    return dateString
  }
}

/** Return Tailwind color classes for a status badge */
export function getStatusColor(status) {
  switch (status) {
    case 'stored':
    case 'complete':
      return 'bg-emerald-100 text-emerald-700'
    case 'processing':
      return 'bg-amber-100 text-amber-700'
    case 'error':
      return 'bg-red-100 text-red-700'
    case 'uploading':
      return 'bg-blue-100 text-blue-700'
    default:
      return 'bg-surface-100 text-surface-500'
  }
}

/** Return a short display label for a status */
export function getStatusLabel(status) {
  switch (status) {
    case 'stored':   return 'Stored'
    case 'complete': return 'Stored'
    case 'processing': return 'Processing'
    case 'error':    return 'Error'
    case 'uploading': return 'Uploading'
    default: return status || 'Unknown'
  }
}

/** Get icon background color class by file type */
export function getIconColor(filename) {
  const type = getFileType(filename)
  switch (type) {
    case 'image':   return 'text-pink-500 bg-pink-50'
    case 'video':   return 'text-purple-500 bg-purple-50'
    case 'audio':   return 'text-indigo-500 bg-indigo-50'
    case 'code':    return 'text-emerald-500 bg-emerald-50'
    case 'archive': return 'text-amber-500 bg-amber-50'
    case 'document': return 'text-blue-500 bg-blue-50'
    case 'spreadsheet': return 'text-green-500 bg-green-50'
    case 'presentation': return 'text-orange-500 bg-orange-50'
    default:        return 'text-surface-500 bg-surface-100'
  }
}
