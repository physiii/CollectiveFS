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

  // Use real shard data from the API, or fall back to basic count
  const chunkCount = file.chunks || 0
  const shardList = file.shard_list || []
  const dataShards = 8 // RS default
  const parityShards = chunkCount - dataShards
  const availableShards = shardList.filter(s => s.available).length
  const totalShardSize = shardList.reduce((acc, s) => acc + (s.size || 0), 0)

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

      {/* Shard overview bar */}
      {chunkCount > 0 && (
        <div data-testid="shard-overview">
          <h4 className="text-sm font-medium text-surface-700 mb-2">
            Shard Distribution
          </h4>
          {/* Visual shard bar */}
          <div data-testid="shard-bar" className="flex gap-0.5 mb-3 h-6 rounded-lg overflow-hidden">
            {shardList.length > 0 ? shardList.map((shard, i) => (
              <div
                key={i}
                data-testid={`shard-block-${i}`}
                className={clsx(
                  'flex-1 relative group cursor-default transition-all',
                  shard.available
                    ? i < dataShards
                      ? 'bg-primary-500 hover:bg-primary-400'
                      : 'bg-amber-500 hover:bg-amber-400'
                    : 'bg-red-400 hover:bg-red-300',
                )}
                title={`Shard ${i} — ${i < dataShards ? 'Data' : 'Parity'} — ${shard.available ? 'Available' : 'Missing'} — ${shard.peer}`}
              >
                <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-white/80">
                  {i}
                </span>
              </div>
            )) : Array.from({ length: chunkCount }, (_, i) => (
              <div
                key={i}
                className={clsx(
                  'flex-1',
                  i < dataShards ? 'bg-primary-300' : 'bg-amber-300',
                )}
              />
            ))}
          </div>

          {/* Legend */}
          <div className="flex gap-4 text-xs text-surface-500 mb-3">
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-sm bg-primary-500" /> Data ({Math.min(dataShards, chunkCount)})
            </span>
            {parityShards > 0 && (
              <span className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> Parity ({parityShards})
              </span>
            )}
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-sm bg-emerald-500" /> {availableShards}/{chunkCount} available
            </span>
          </div>

          {/* Shard table */}
          <div className="border border-surface-200 rounded-lg overflow-hidden">
            <table data-testid="shard-table" className="w-full text-xs">
              <thead>
                <tr className="bg-surface-50 border-b border-surface-200">
                  <th className="text-left px-3 py-2 font-medium text-surface-500">#</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Type</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Size</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Status</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Encrypted</th>
                  <th className="text-left px-3 py-2 font-medium text-surface-500">Peer</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-100">
                {(shardList.length > 0 ? shardList : Array.from({ length: Math.min(chunkCount, 12) }, (_, i) => ({
                  num: i, id: `shard-${i}`, size: 0, encrypted: false, available: true, peer: `peer-${(i % 3) + 1}`,
                }))).slice(0, 16).map((shard) => (
                  <tr key={shard.num} data-testid={`shard-row-${shard.num}`} className="hover:bg-surface-50">
                    <td className="px-3 py-1.5 font-mono text-surface-400">{shard.num}</td>
                    <td className="px-3 py-1.5">
                      <span className={clsx(
                        'inline-block px-1.5 py-0.5 rounded text-[10px] font-medium',
                        shard.num < dataShards
                          ? 'bg-primary-50 text-primary-700'
                          : 'bg-amber-50 text-amber-700',
                      )}>
                        {shard.num < dataShards ? 'DATA' : 'PARITY'}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 font-mono text-surface-500">
                      {shard.size > 0 ? formatBytes(shard.size) : '—'}
                    </td>
                    <td className="px-3 py-1.5">
                      {shard.available ? (
                        <span className="flex items-center gap-1 text-emerald-600 font-medium">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                          Available
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-red-500 font-medium">
                          <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                          Missing
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className={shard.encrypted ? 'text-emerald-600 font-medium' : 'text-surface-400'}>
                        {shard.encrypted ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-surface-500">{shard.peer}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {chunkCount > 16 && (
            <p className="text-xs text-surface-400 mt-1.5 text-center">
              Showing 16 of {chunkCount} shards
            </p>
          )}

          {/* Total shard storage */}
          {totalShardSize > 0 && (
            <div className="mt-2 text-xs text-surface-400 text-center">
              Total shard storage: {formatBytes(totalShardSize)} ({((totalShardSize / (file.size || 1)) * 100).toFixed(0)}% of original)
            </div>
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
