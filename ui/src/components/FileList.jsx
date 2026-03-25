import React from 'react'
import { Download, Trash2, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import {
  formatBytes,
  formatDate,
  getFileIconComponent,
  getIconColor,
  getStatusColor,
  getStatusLabel,
} from '../utils/fileUtils.js'

export default function FileList({ files, onDownload, onDelete, onFileClick }) {
  return (
    <div className="px-4 pb-4">
      <div className="bg-white border border-surface-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-surface-100 bg-surface-50">
              <th className="text-left px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wide">
                Name
              </th>
              <th className="text-right px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wide hidden sm:table-cell">
                Size
              </th>
              <th className="text-right px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wide hidden md:table-cell">
                Chunks
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wide hidden lg:table-cell">
                Date
              </th>
              <th className="text-left px-4 py-3 text-xs font-medium text-surface-500 uppercase tracking-wide">
                Status
              </th>
              <th className="px-4 py-3 w-20" />
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-100">
            {files.map((file) => {
              const IconComponent = getFileIconComponent(file.name)
              const iconColor = getIconColor(file.name)
              const isProcessing =
                file.status === 'processing' || file.status === 'uploading'
              return (
                <tr
                  key={file.id}
                  data-testid="file-list-row"
                  onClick={() => onFileClick(file)}
                  className="hover:bg-surface-50 cursor-pointer transition-colors group"
                >
                  {/* Name */}
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-3">
                      <div
                        className={clsx(
                          'w-7 h-7 rounded-md flex items-center justify-center shrink-0',
                          iconColor,
                        )}
                      >
                        {isProcessing ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-500" />
                        ) : (
                          <IconComponent className="w-3.5 h-3.5" />
                        )}
                      </div>
                      <span
                        className="font-medium text-surface-800 truncate max-w-[200px]"
                        title={file.name}
                      >
                        {file.name}
                      </span>
                    </div>
                  </td>

                  {/* Size */}
                  <td className="px-4 py-2.5 text-right text-surface-500 hidden sm:table-cell">
                    {formatBytes(file.size)}
                  </td>

                  {/* Chunks */}
                  <td className="px-4 py-2.5 text-right text-surface-500 hidden md:table-cell">
                    {file.chunks}
                  </td>

                  {/* Date */}
                  <td className="px-4 py-2.5 text-surface-400 text-xs hidden lg:table-cell">
                    {formatDate(file.created_at)}
                  </td>

                  {/* Status */}
                  <td className="px-4 py-2.5">
                    <span
                      className={clsx(
                        'text-xs font-medium px-2 py-0.5 rounded-full',
                        getStatusColor(file.status),
                      )}
                    >
                      {getStatusLabel(file.status)}
                    </span>
                  </td>

                  {/* Actions */}
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {!isProcessing && (
                        <button
                          data-testid="download-button"
                          onClick={(e) => { e.stopPropagation(); onDownload(file) }}
                          title="Download"
                          className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-primary-50 text-surface-400 hover:text-primary-600 transition"
                        >
                          <Download className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <button
                        data-testid="delete-button"
                        onClick={(e) => { e.stopPropagation(); onDelete(file.id) }}
                        title="Delete"
                        className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-red-50 text-surface-400 hover:text-red-500 transition"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
