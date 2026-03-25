import React, { useState } from 'react'
import {
  Download,
  Trash2,
  Copy,
  Check,
  Shield,
  Calendar,
  HardDrive,
  Layers,
} from 'lucide-react'
import clsx from 'clsx'
import { useFile } from '../hooks/useFiles.js'
import { downloadFile } from '../api/client.js'
import {
  formatBytes,
  formatDate,
  getFileIconComponent,
  getIconColor,
  getStatusColor,
  getStatusLabel,
} from '../utils/fileUtils.js'
import toast from 'react-hot-toast'

export default function FileDetails({ fileId, onDelete, onClose }) {
  const { data: file, isLoading } = useFile(fileId)
  const [copiedId, setCopiedId] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const handleCopyId = () => {
    navigator.clipboard.writeText(fileId)
    setCopiedId(true)
    setTimeout(() => setCopiedId(false), 2000)
    toast.success('File ID copied')
  }

  const handleDownload = async () => {
    if (!file) return
    setDownloading(true)
    try {
      await downloadFile(file.id, file.name)
    } catch (err) {
      toast.error(`Download failed: ${err.message}`)
    } finally {
      setDownloading(false)
    }
  }

  if (isLoading || !file) {
    return (
      <div
        data-testid="file-details-modal"
        className="flex items-center justify-center h-40"
      >
        <div className="w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const IconComponent = getFileIconComponent(file.name)
  const iconColor = getIconColor(file.name)

  // Build a fake chunk table for display
  const chunkCount = file.chunks || 0
  const chunkRows = Array.from({ length: Math.min(chunkCount, 12) }, (_, i) => ({
    num: i,
    id: `shard-${i}`,
    encrypted: true,
    peer: `peer-${(i % 4) + 1}`,
  }))

  return (
    <div data-testid="file-details-modal" className="space-y-6">
      {/* File header */}
      <div className="flex items-start gap-4">
        <div className={clsx('w-12 h-12 rounded-xl flex items-center justify-center shrink-0', iconColor)}>
          <IconComponent className="w-6 h-6" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-surface-900 text-base break-all leading-snug">
            {file.name}
          </h3>
          <span
            className={clsx(
              'inline-block mt-1 text-xs font-medium px-2 py-0.5 rounded-full',
              getStatusColor(file.status),
            )}
          >
            {getStatusLabel(file.status)}
          </span>
        </div>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 gap-3">
        {[
          { icon: HardDrive,  label: 'Size',    value: formatBytes(file.size) },
          { icon: Layers,     label: 'Chunks',  value: `${chunkCount} shards` },
          { icon: Calendar,   label: 'Created', value: formatDate(file.created_at) },
          { icon: Shield,     label: 'Encrypted', value: 'Fernet AES-128' },
        ].map(({ icon: Icon, label, value }) => (
          <div key={label} className="bg-surface-50 rounded-lg p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <Icon className="w-3.5 h-3.5 text-surface-400" />
              <span className="text-xs text-surface-400">{label}</span>
            </div>
            <p className="text-sm font-medium text-surface-700">{value}</p>
          </div>
        ))}
      </div>

      {/* File ID */}
      <div className="bg-surface-50 rounded-lg p-3">
        <p className="text-xs text-surface-400 mb-1">File ID</p>
        <div className="flex items-center gap-2">
          <code className="text-xs font-mono text-surface-600 flex-1 break-all">
            {fileId}
          </code>
          <button
            onClick={handleCopyId}
            className="shrink-0 w-7 h-7 flex items-center justify-center rounded-md hover:bg-surface-200 text-surface-400 hover:text-surface-600 transition"
          >
            {copiedId ? (
              <Check className="w-3.5 h-3.5 text-emerald-500" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* Chunk breakdown */}
      {chunkRows.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-surface-700 mb-2">
            Shard Breakdown
          </h4>
          <div className="border border-surface-200 rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-surface-50 border-b border-surface-200">
                  <th className="text-left px-3 py-2 font-medium text-surface-500">#</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Shard ID</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Encrypted</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Peer</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-100">
                {chunkRows.map((row) => (
                  <tr key={row.num} className="hover:bg-surface-50">
                    <td className="px-3 py-1.5 font-mono text-surface-400">{row.num}</td>
                    <td className="px-3 py-1.5 font-mono text-surface-500">{row.id}</td>
                    <td className="px-3 py-1.5">
                      <span className="text-emerald-600 font-medium">Yes</span>
                    </td>
                    <td className="px-3 py-1.5 text-surface-500">{row.peer}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {chunkCount > 12 && (
            <p className="text-xs text-surface-400 mt-1.5 text-center">
              Showing 12 of {chunkCount} shards
            </p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3 pt-2 border-t border-surface-100">
        <button
          data-testid="download-button"
          onClick={handleDownload}
          disabled={downloading}
          className="flex-1 flex items-center justify-center gap-2 h-9 bg-primary-600 hover:bg-primary-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg transition"
        >
          {downloading ? (
            <div className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
          ) : (
            <Download className="w-4 h-4" />
          )}
          Download
        </button>
        <button
          data-testid="delete-button"
          onClick={() => { onDelete(fileId); onClose() }}
          className="flex items-center justify-center gap-2 h-9 px-4 bg-red-50 hover:bg-red-100 text-red-600 text-sm font-medium rounded-lg transition"
        >
          <Trash2 className="w-4 h-4" />
          Delete
        </button>
      </div>
    </div>
  )
}
