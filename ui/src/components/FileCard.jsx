import React from 'react'
import { Download, Trash2, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import {
  formatBytes,
  formatRelativeDate,
  getFileIconComponent,
  getIconColor,
  getStatusColor,
  getStatusLabel,
} from '../utils/fileUtils.js'

export default function FileCard({ file, onDownload, onDelete, onClick }) {
  const isProcessing = file.status === 'processing' || file.status === 'uploading'
  const IconComponent = getFileIconComponent(file.name)
  const iconColor = getIconColor(file.name)

  const handleDownload = (e) => {
    e.stopPropagation()
    onDownload(file)
  }

  const handleDelete = (e) => {
    e.stopPropagation()
    onDelete(file.id)
  }

  return (
    <div
      data-testid="file-card"
      onClick={() => onClick(file)}
      className="group relative bg-white rounded-xl border border-surface-200 p-4 cursor-pointer hover:border-primary-300 hover:shadow-card-hover transition-all duration-150 flex flex-col gap-3"
    >
      {/* Icon */}
      <div className="flex items-start justify-between">
        <div
          className={clsx(
            'w-10 h-10 rounded-lg flex items-center justify-center',
            iconColor,
          )}
        >
          {isProcessing ? (
            <Loader2 className="w-5 h-5 animate-spin text-amber-500" />
          ) : (
            <IconComponent className="w-5 h-5" />
          )}
        </div>

        {/* Hover actions */}
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {!isProcessing && (
            <button
              data-testid="download-button"
              onClick={handleDownload}
              title="Download"
              className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-primary-50 text-surface-400 hover:text-primary-600 transition"
            >
              <Download className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            data-testid="delete-button"
            onClick={handleDelete}
            title="Delete"
            className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-red-50 text-surface-400 hover:text-red-500 transition"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Name */}
      <div className="flex-1 min-w-0">
        <p
          className="text-sm font-medium text-surface-800 truncate leading-snug"
          title={file.name}
        >
          {file.name}
        </p>
        <p className="text-xs text-surface-400 mt-0.5">
          {formatRelativeDate(file.created_at)}
        </p>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-surface-500">{formatBytes(file.size)}</span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-surface-400">{file.chunks} shards</span>
          <span
            className={clsx(
              'text-[11px] font-medium px-1.5 py-0.5 rounded-full',
              getStatusColor(file.status),
            )}
          >
            {getStatusLabel(file.status)}
          </span>
        </div>
      </div>

      {/* Processing progress bar */}
      {isProcessing && (
        <div className="absolute bottom-0 left-0 right-0 h-0.5 rounded-b-xl overflow-hidden bg-surface-100">
          <div className="h-full bg-amber-400 animate-[progress_2s_ease-in-out_infinite]" style={{ width: '60%' }} />
        </div>
      )}
    </div>
  )
}
