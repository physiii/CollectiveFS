import React, { useState } from 'react'
import { Save, RotateCcw, AlertTriangle, Cloud, FolderSync, Link, Shield, Share2 } from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'

const ENCRYPTION_OPTIONS = [
  { value: 'fernet',    label: 'Fernet (AES-128-CBC + HMAC-SHA256)' },
  { value: 'chacha20',  label: 'ChaCha20-Poly1305 (coming soon)',   disabled: true },
]

export default function Settings() {
  const [storagePath, setStoragePath]    = useState('~/.collective')
  const [dataShards,  setDataShards]     = useState(8)
  const [parityShards, setParityShards]  = useState(4)
  const [encryption,  setEncryption]     = useState('fernet')

  // S3
  const [s3Region,    setS3Region]       = useState('')
  const [s3Bucket,    setS3Bucket]       = useState('')
  const [s3AccessKey, setS3AccessKey]    = useState('')

  // Local sync
  const [syncPath,    setSyncPath]       = useState('')

  // URL import
  const [importUrl,   setImportUrl]      = useState('')
  const [importing,   setImporting]      = useState(false)

  const handleSave = () => {
    toast.success('Settings saved (in-memory only for this session)')
  }

  const handleReset = () => {
    if (!window.confirm('Reset all settings to defaults?')) return
    setStoragePath('~/.collective')
    setDataShards(8)
    setParityShards(4)
    setEncryption('fernet')
    setS3Region('')
    setS3Bucket('')
    setS3AccessKey('')
    setSyncPath('')
    setImportUrl('')
    toast('Settings reset to defaults')
  }

  const handleSync = () => {
    if (!syncPath) { toast.error('Enter a folder path first'); return }
    toast.success(`Syncing ${syncPath}…`)
  }

  const handleImport = async () => {
    if (!importUrl) { toast.error('Enter a URL first'); return }
    setImporting(true)
    await new Promise((r) => setTimeout(r, 1500))
    setImporting(false)
    toast.success('Import queued')
    setImportUrl('')
  }

  return (
    <div data-testid="settings-panel" className="max-w-2xl mx-auto px-4 py-6 space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-surface-900">Settings</h2>
        <p className="text-sm text-surface-500 mt-0.5">
          Configure storage, erasure coding, encryption, and integrations.
        </p>
      </div>

      {/* Storage Path */}
      <Section icon={FolderSync} title="Storage">
        <Field label="Collective storage path">
          <input
            type="text"
            value={storagePath}
            onChange={(e) => setStoragePath(e.target.value)}
            className={inputCls}
            placeholder="~/.collective"
          />
        </Field>
      </Section>

      {/* Erasure Coding */}
      <Section icon={Share2} title="Erasure Coding">
        <div className="grid grid-cols-2 gap-6">
          <Field label={`Data shards: ${dataShards}`}>
            <input
              data-testid="settings-erasure-data"
              type="range"
              min={4}
              max={16}
              value={dataShards}
              onChange={(e) => setDataShards(Number(e.target.value))}
              className="w-full accent-primary-600"
            />
            <div className="flex justify-between text-xs text-surface-400 mt-1">
              <span>4</span><span>16</span>
            </div>
          </Field>

          <Field label={`Parity shards: ${parityShards}`}>
            <input
              data-testid="settings-erasure-parity"
              type="range"
              min={2}
              max={8}
              value={parityShards}
              onChange={(e) => setParityShards(Number(e.target.value))}
              className="w-full accent-accent-600"
            />
            <div className="flex justify-between text-xs text-surface-400 mt-1">
              <span>2</span><span>8</span>
            </div>
          </Field>
        </div>
        <p className="text-xs text-surface-400 mt-2">
          Current scheme: <span className="font-medium text-surface-600">Reed-Solomon {dataShards}+{parityShards}</span> — can survive up to {parityShards} lost shards.
        </p>
      </Section>

      {/* Encryption */}
      <Section icon={Shield} title="Encryption">
        <Field label="Encryption scheme">
          <select
            value={encryption}
            onChange={(e) => setEncryption(e.target.value)}
            className={inputCls}
          >
            {ENCRYPTION_OPTIONS.map(({ value, label, disabled }) => (
              <option key={value} value={value} disabled={disabled}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </Section>

      {/* S3 Sync */}
      <Section icon={Cloud} title="S3 Sync" testId="settings-s3-section">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Region">
            <input
              type="text"
              value={s3Region}
              onChange={(e) => setS3Region(e.target.value)}
              className={inputCls}
              placeholder="us-east-1"
            />
          </Field>
          <Field label="Bucket">
            <input
              type="text"
              value={s3Bucket}
              onChange={(e) => setS3Bucket(e.target.value)}
              className={inputCls}
              placeholder="my-collective-bucket"
            />
          </Field>
        </div>
        <Field label="Access Key ID" className="mt-4">
          <input
            type="password"
            value={s3AccessKey}
            onChange={(e) => setS3AccessKey(e.target.value)}
            className={inputCls}
            placeholder="AKIA…"
          />
        </Field>
      </Section>

      {/* Local Folder Sync */}
      <Section icon={FolderSync} title="Local Folder Sync">
        <div className="flex gap-2">
          <input
            type="text"
            value={syncPath}
            onChange={(e) => setSyncPath(e.target.value)}
            className={clsx(inputCls, 'flex-1')}
            placeholder="/home/user/Documents"
          />
          <button
            onClick={handleSync}
            className="px-4 h-9 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded-lg transition"
          >
            Sync
          </button>
        </div>
      </Section>

      {/* URL Import */}
      <Section icon={Link} title="URL Import">
        <div className="flex gap-2">
          <input
            type="url"
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            className={clsx(inputCls, 'flex-1')}
            placeholder="https://example.com/file.zip"
          />
          <button
            onClick={handleImport}
            disabled={importing}
            className="px-4 h-9 bg-primary-600 hover:bg-primary-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg transition"
          >
            {importing ? 'Importing…' : 'Import'}
          </button>
        </div>
      </Section>

      {/* Actions */}
      <div className="flex gap-3">
        <button
          onClick={handleSave}
          className="flex items-center gap-2 h-9 px-5 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded-lg transition"
        >
          <Save className="w-4 h-4" />
          Save Settings
        </button>
      </div>

      {/* Danger Zone */}
      <div className="border border-red-200 rounded-xl p-4 bg-red-50/50">
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <h3 className="text-sm font-semibold text-red-700">Danger Zone</h3>
        </div>
        <p className="text-xs text-red-500 mb-3">
          Resetting settings cannot be undone. Your stored files will not be affected.
        </p>
        <button
          onClick={handleReset}
          className="flex items-center gap-2 h-8 px-4 border border-red-300 bg-white hover:bg-red-50 text-red-600 text-sm font-medium rounded-lg transition"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset all settings
        </button>
      </div>
    </div>
  )
}

// ---- Small helpers ----

function Section({ icon: Icon, title, children, testId }) {
  return (
    <div
      data-testid={testId}
      className="bg-white border border-surface-200 rounded-xl p-5"
    >
      <div className="flex items-center gap-2 mb-4">
        <Icon className="w-4 h-4 text-surface-400" />
        <h3 className="text-sm font-semibold text-surface-800">{title}</h3>
      </div>
      {children}
    </div>
  )
}

function Field({ label, children, className }) {
  return (
    <div className={className}>
      <label className="block text-xs font-medium text-surface-500 mb-1.5">{label}</label>
      {children}
    </div>
  )
}

const inputCls =
  'h-9 w-full px-3 text-sm bg-surface-50 border border-surface-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500/30 focus:border-primary-400 transition'
