// Line icons drawn at 24x24 so every glyph shares one optical weight.
const base = { fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }

function Svg({ children, ...rest }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" {...rest}>
      {children}
    </svg>
  )
}

export const DashboardIcon = () => (
  <Svg>
    <rect x="4" y="4" width="7" height="7" rx="1.5" {...base} />
    <rect x="13" y="4" width="7" height="7" rx="1.5" {...base} />
    <rect x="4" y="13" width="7" height="7" rx="1.5" {...base} />
    <rect x="13" y="13" width="7" height="7" rx="1.5" {...base} />
  </Svg>
)

export const ChatIcon = () => (
  <Svg>
    <path
      d="M5 6.5A3.5 3.5 0 0 1 8.5 3h7A3.5 3.5 0 0 1 19 6.5v4A3.5 3.5 0 0 1 15.5 14H11l-5 4v-4.3A3.5 3.5 0 0 1 3 10.25V6.5Z"
      {...base}
    />
  </Svg>
)

export const SkillIcon = () => (
  <Svg>
    <path d="M6 4.5h8.5L18 8v11.5H6V4.5Z" {...base} />
    <path d="M14 4.5V8h4M8.5 12h7M8.5 15.5h7" {...base} />
  </Svg>
)

export const ChevronDown = () => (
  <Svg width="16" height="16">
    <path d="M6 9l6 6 6-6" {...base} strokeWidth={2.5} />
  </Svg>
)

export const ChevronUp = () => (
  <Svg width="16" height="16">
    <path d="M6 15l6-6 6 6" {...base} strokeWidth={2.5} />
  </Svg>
)

export const ChevronRight = () => (
  <Svg>
    <path d="M9 6l6 6-6 6" {...base} strokeWidth={2.5} />
  </Svg>
)

export const FolderIcon = () => (
  <Svg>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4L11 8.5h8.5A1.5 1.5 0 0 1 21 10v7.5A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-10Z" {...base} />
  </Svg>
)

export const FileIcon = () => (
  <Svg>
    <path d="M7 3.5h7L18 7.5v13H7V3.5Z" {...base} />
    <path d="M14 3.5V8h4" {...base} />
  </Svg>
)

export const ImageIcon = () => (
  <Svg>
    <rect x="3.5" y="5" width="17" height="14" rx="2" {...base} />
    <circle cx="9" cy="10" r="1.6" {...base} />
    <path d="M4 17l4.5-4.5 3.5 3.5 3-2.5L20 18" {...base} />
  </Svg>
)

export const VideoIcon = () => (
  <Svg>
    <rect x="3" y="6" width="12.5" height="12" rx="2" {...base} />
    <path d="M15.5 10.5 21 7.5v9l-5.5-3v-3Z" {...base} />
  </Svg>
)

export const AudioIcon = () => (
  <Svg>
    <path d="M9 17V6l10-2v11" {...base} />
    <circle cx="6.5" cy="17" r="2.5" {...base} />
    <circle cx="16.5" cy="15" r="2.5" {...base} />
  </Svg>
)

export const ArchiveIcon = () => (
  <Svg>
    <rect x="3.5" y="4.5" width="17" height="4.5" rx="1.5" {...base} />
    <path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9" {...base} />
    <path d="M10.5 13h3" {...base} />
  </Svg>
)

export const CodeIcon = () => (
  <Svg>
    <path d="m9 8-5 4 5 4M15 8l5 4-5 4" {...base} />
  </Svg>
)

export const DocIcon = () => (
  <Svg>
    <path d="M7 3.5h7L18 7.5v13H7V3.5Z" {...base} />
    <path d="M14 3.5V8h4M9.5 12h6M9.5 15.5h6" {...base} />
  </Svg>
)

export const ListIcon = () => (
  <Svg>
    <path d="M8 6.5h12M8 12h12M8 17.5h12M4 6.5h.01M4 12h.01M4 17.5h.01" {...base} />
  </Svg>
)

export const GridIcon = () => (
  <Svg>
    <rect x="4" y="4" width="7" height="7" rx="1.5" {...base} />
    <rect x="13" y="4" width="7" height="7" rx="1.5" {...base} />
    <rect x="4" y="13" width="7" height="7" rx="1.5" {...base} />
    <rect x="13" y="13" width="7" height="7" rx="1.5" {...base} />
  </Svg>
)

export const UploadIcon = () => (
  <Svg>
    <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" {...base} />
    <path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" {...base} />
  </Svg>
)

export const DownloadIcon = () => (
  <Svg>
    <path d="M12 4v12m0 0 4.5-4.5M12 16l-4.5-4.5" {...base} />
    <path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" {...base} />
  </Svg>
)

export const NewFolderIcon = () => (
  <Svg>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4L11 8.5h8.5A1.5 1.5 0 0 1 21 10v7.5A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-10Z" {...base} />
    <path d="M12 11.5v5M9.5 14h5" {...base} />
  </Svg>
)

export const TrashIcon = () => (
  <Svg>
    <path d="M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5 7.5 20h9l1-13.5M10.5 10v6M13.5 10v6" {...base} />
  </Svg>
)

export const CloseIcon = () => (
  <Svg>
    <path d="m6 6 12 12M18 6 6 18" {...base} />
  </Svg>
)

export const SendIcon = () => (
  <Svg>
    <path d="M4 12 20 4l-4 16-4-7-8-1Z" {...base} />
  </Svg>
)

export const RefreshIcon = () => (
  <Svg>
    <path d="M20 11a8 8 0 1 0-2.3 5.7" {...base} />
    <path d="M20 5v6h-6" {...base} />
  </Svg>
)

export const PencilIcon = () => (
  <Svg>
    <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" {...base} />
  </Svg>
)

const KIND_ICONS = {
  image: ImageIcon,
  video: VideoIcon,
  audio: AudioIcon,
  archive: ArchiveIcon,
  code: CodeIcon,
  doc: DocIcon,
  file: FileIcon,
}

export function KindIcon({ kind }) {
  const Component = KIND_ICONS[kind] ?? FileIcon
  return <Component />
}
