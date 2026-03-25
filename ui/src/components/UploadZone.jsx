import React, { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { UploadCloud, X, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { uploadFile } from '../api/client.js'
import { useQueryClient } from '@tanstack/react-query'
import { formatBytes } from '../utils/fileUtils.js'

const STAGE_LABELS = ['Uploading', 'Encoding', 'Encrypting', 'Distributing', 'Complete']

function UploadItem({ item }) {
  const stageIndex = STAGE_LABELS.indexOf(item.stage)
  const pct = item.progress ?? 0

  return (
    <div className="flex items-start gap-3 p-3 bg-white border border-surface-200 rounded-lg shadow-card">
      <div className="shrink-0 mt-0.5">
        {item.status === 'complete' ? (
          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
        ) : item.status === 'error' ? (
          <AlertCircle className="w-4 h-4 text-red-500" />
        ) : (
          <Loader2 className="w-4 h-4 text-primary-500 animate-spin" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-surface-800 truncate">{item.name}</p>
        <p className="text-xs text-surface-400 mt-0.5">{formatBytes(item.size)}</p>

        {/* Progress */}
        {item.status !== 'complete' && item.status !== 'error' && (
          <div className="mt-2">
            <div className="h-1 bg-surface-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-500 rounded-full transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="flex gap-1.5 mt-1.5">
              {STAGE_LABELS.slice(0, -1).map((s, i) => (
                <span
                  key={s}
                  className={clsx(
                    'text-[10px] font-medium',
                    i < stageIndex
                      ? 'text-emerald-500'
                      : i === stageIndex
                      ? 'text-primary-500'
                      : 'text-surface-300',
                  )}
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
        )}

        {item.status === 'error' && (
          <p className="text-xs text-red-500 mt-1">{item.errorMsg}</p>
        )}
      </div>
    </div>
  )
}

export default function UploadZone({ children, triggerRef }) {
  const [uploads, setUploads] = useState([])
  const qc = useQueryClient()

  const updateUpload = useCallback((id, patch) => {
    setUploads((prev) =>
      prev.map((u) => (u.id === id ? { ...u, ...patch } : u)),
    )
  }, [])

  const processFile = useCallback(
    async (file) => {
      const uid = crypto.randomUUID()
      setUploads((prev) => [
        {
          id: uid,
          name: file.name,
          size: file.size,
          status: 'uploading',
          stage: 'Uploading',
          progress: 0,
        },
        ...prev,
      ])

      try {
        await uploadFile(file, (pct) => {
          updateUpload(uid, { progress: pct * 0.3, stage: 'Uploading' })
        })

        // Simulate pipeline stages
        const stages = [
          { stage: 'Encoding',     progress: 40 },
          { stage: 'Encrypting',   progress: 65 },
          { stage: 'Distributing', progress: 85 },
        ]
        for (const s of stages) {
          await new Promise((r) => setTimeout(r, 600))
          updateUpload(uid, s)
        }

        await new Promise((r) => setTimeout(r, 800))
        updateUpload(uid, { stage: 'Complete', progress: 100, status: 'complete' })
        qc.invalidateQueries({ queryKey: ['files'] })
        qc.invalidateQueries({ queryKey: ['stats'] })
        toast.success(`"${file.name}" stored successfully`)

        // Remove after 4 s
        setTimeout(() => {
          setUploads((prev) => prev.filter((u) => u.id !== uid))
        }, 4_000)
      } catch (err) {
        updateUpload(uid, { status: 'error', errorMsg: err.message })
        toast.error(`Upload failed: ${err.message}`)
      }
    },
    [qc, updateUpload],
  )

  const onDrop = useCallback(
    (acceptedFiles) => {
      acceptedFiles.forEach(processFile)
    },
    [processFile],
  )

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    noClick: true,
    noKeyboard: true,
  })

  // Expose open() via ref so TopBar can trigger it
  React.useEffect(() => {
    if (triggerRef) triggerRef.current = open
  }, [open, triggerRef])

  return (
    <div
      data-testid="upload-zone"
      {...getRootProps()}
      className={clsx(
        'flex-1 flex flex-col min-h-0 relative transition-all',
        isDragActive && 'ring-2 ring-inset ring-primary-400 bg-primary-50/40',
      )}
    >
      <input {...getInputProps()} />

      {/* Drag overlay */}
      {isDragActive && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-primary-50/80 backdrop-blur-sm rounded-lg pointer-events-none">
          <UploadCloud className="w-12 h-12 text-primary-400 mb-3" />
          <p className="text-lg font-semibold text-primary-600">Drop files to upload</p>
          <p className="text-sm text-primary-400 mt-1">
            Files will be encoded, encrypted, and distributed
          </p>
        </div>
      )}

      {/* Children (FileBrowser content) */}
      {children}

      {/* Upload progress tray */}
      {uploads.length > 0 && (
        <div className="absolute bottom-4 right-4 z-30 w-72 space-y-2">
          <div className="flex items-center justify-between mb-1 px-1">
            <span className="text-xs font-medium text-surface-600">
              {uploads.filter((u) => u.status !== 'complete').length} uploading
            </span>
            <button
              onClick={() => setUploads([])}
              className="text-xs text-surface-400 hover:text-surface-600 transition"
            >
              Clear
            </button>
          </div>
          {uploads.map((u) => (
            <UploadItem key={u.id} item={u} />
          ))}
        </div>
      )}
    </div>
  )
}
