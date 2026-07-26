import { useState } from 'react'

import SectionChat from './SectionChat'
import SectionSkillDoc from './SectionSkillDoc'
import { ChatIcon, ChevronDown, ChevronUp, DashboardIcon, SkillIcon } from './icons'

const COLLAPSE_PREFIX = 'collectivefs:section-collapsed:'

export default function SectionCard({
  title,
  icon,
  sectionId,
  skillId,
  callSign,
  badge,
  description,
  children,
  context,
  skillMarkdown,
  suggestions,
  onOpen,
  onConfigChanged,
  wide = false,
  startCollapsed = false,
  full = false,
}) {
  const [view, setView] = useState('dashboard')
  const storageKey = COLLAPSE_PREFIX + sectionId
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === 'undefined') return startCollapsed
    const stored = window.localStorage.getItem(storageKey)
    if (stored !== null) return stored === '1'
    return startCollapsed
  })

  function onCardClick(event) {
    if (collapsed) return
    if (!onOpen || view !== 'dashboard') return
    if (event.target.closest("button, a, input, textarea, select, [role='button']")) return
    onOpen()
  }

  function toggleCollapse(event) {
    event.stopPropagation()
    setCollapsed((previous) => {
      const next = !previous
      window.localStorage.setItem(storageKey, next ? '1' : '0')
      return next
    })
  }

  return (
    <article
      className={`section-card ${collapsed ? 'collapsed' : ''} ${wide ? 'wide' : ''} ${full ? 'full' : ''}`}
      data-testid={`section-${sectionId}`}
      onClick={onCardClick}
    >
      <header className="section-card-header" onClick={collapsed ? toggleCollapse : undefined}>
        <button
          className="section-title-button"
          type="button"
          onClick={collapsed ? undefined : onOpen}
          disabled={collapsed ? false : !onOpen}
          title={onOpen ? `Open ${title}` : title}
        >
          <span className="section-card-icon">{icon}</span>
          <span className="section-card-title">{title}</span>
          <span className="section-card-call">· {badge || callSign}</span>
        </button>

        {collapsed ? (
          <button
            type="button"
            className="section-collapse-arrow"
            onClick={toggleCollapse}
            aria-label="Expand section"
            title="Expand"
          >
            <ChevronDown />
          </button>
        ) : (
          <div className="section-toggle" aria-label={`${title} view selector`}>
            <button
              type="button"
              className={view === 'dashboard' ? 'active' : ''}
              onClick={() => setView('dashboard')}
              aria-label={`Show ${title} dashboard`}
              aria-pressed={view === 'dashboard'}
              title="Dashboard"
            >
              <DashboardIcon />
            </button>
            <button
              type="button"
              className={view === 'chat' ? 'active' : ''}
              onClick={() => setView('chat')}
              aria-label={`Chat with ${callSign}`}
              aria-pressed={view === 'chat'}
              title="Chat"
            >
              <ChatIcon />
            </button>
            <button
              type="button"
              className={view === 'skill' ? 'active' : ''}
              onClick={() => setView('skill')}
              aria-label={`Show ${title} skill`}
              aria-pressed={view === 'skill'}
              title="Skill"
            >
              <SkillIcon />
            </button>
            <button
              type="button"
              className="section-collapse-arrow"
              onClick={toggleCollapse}
              aria-label="Collapse section"
              title="Collapse"
            >
              <ChevronUp />
            </button>
          </div>
        )}
      </header>

      {!collapsed && description && <p className="section-description">{description}</p>}
      {!collapsed && (
        <div className="section-card-body">
          <div className={view === 'dashboard' ? 'section-pane active' : 'section-pane'}>{children}</div>
          <div className={view === 'chat' ? 'section-pane active' : 'section-pane'}>
            <SectionChat
              sectionId={sectionId}
              skillId={skillId}
              callSign={callSign}
              context={context}
              suggestions={suggestions}
              onConfigChanged={onConfigChanged}
            />
          </div>
          <div className={view === 'skill' ? 'section-pane active' : 'section-pane'}>
            <SectionSkillDoc markdown={skillMarkdown} />
          </div>
        </div>
      )}
    </article>
  )
}
