import { useCallback, useEffect, useRef, useState } from 'react'
import { createProject, deleteProject, fetchProjects, renameProject } from '../api'
import type { Project } from '../api'

const PROJECT_STORAGE_KEY = 'vs_current_project'

interface ProjectBarProps {
  /** Reports the selected project (id) and full record up to App. */
  onProjectChange: (project: Project | null) => void
  /** Bumped by App whenever runs change so counts stay fresh. */
  refreshToken?: number
}

export function ProjectBar({ onProjectChange, refreshToken }: ProjectBarProps) {
  const [projects, setProjects] = useState<Project[]>([])
  const [currentId, setCurrentId] = useState<string>(
    () => localStorage.getItem(PROJECT_STORAGE_KEY) || 'default',
  )
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const currentIdRef = useRef(currentId)
  useEffect(() => { currentIdRef.current = currentId }, [currentId])

  const load = useCallback(async () => {
    const requestedId = currentId
    try {
      const list = await fetchProjects()
      setProjects(list)
      const found = list.find(p => p.id === currentId) || list[0] || null
      if (found && found.id !== currentId) setCurrentId(found.id)
      if (currentIdRef.current === requestedId) onProjectChange(found)
    } catch {
      if (currentIdRef.current === requestedId) onProjectChange(null)
    }
  }, [currentId, onProjectChange])

  useEffect(() => { load() }, [load, refreshToken])

  useEffect(() => {
    localStorage.setItem(PROJECT_STORAGE_KEY, currentId)
  }, [currentId])

  const current = projects.find(p => p.id === currentId) || null

  const handleSelect = useCallback((id: string) => {
    currentIdRef.current = id
    setCurrentId(id)
    const proj = projects.find(p => p.id === id) || null
    onProjectChange(proj)
  }, [onProjectChange, projects])

  const handleCreate = useCallback(async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const proj = await createProject(name)
      setNewName('')
      setCreating(false)
      const list = await fetchProjects()
      setProjects(list)
      setCurrentId(proj.id)
      onProjectChange(list.find(p => p.id === proj.id) || proj)
    } catch { /* leave the input open on failure */ }
  }, [newName, onProjectChange])

  const handleRename = useCallback(async () => {
    const name = renameValue.trim()
    if (!name || !current) return
    try {
      await renameProject(current.id, name)
      setRenaming(false)
      await load()
    } catch { /* keep editing */ }
  }, [current, load, renameValue])

  const handleDelete = useCallback(async () => {
    if (!current || current.id === 'default') return
    if (!window.confirm(`Delete project "${current.name}"? Its videos move to ${projects[0]?.name || 'the default project'}.`)) return
    try {
      await deleteProject(current.id)
      setCurrentId('default')
      await load()
    } catch { /* ignore */ }
  }, [current, load, projects])

  return (
    <div className="project-bar">
      <span className="project-bar-label">Project</span>
      <select
        className="project-select"
        value={currentId}
        onChange={e => handleSelect(e.target.value)}
      >
        {projects.map(p => (
          <option key={p.id} value={p.id}>
            {p.name} · {p.run_ids.length} video{p.run_ids.length === 1 ? '' : 's'} · {p.conversations.length} chat{p.conversations.length === 1 ? '' : 's'}
          </option>
        ))}
      </select>

      {creating ? (
        <span className="project-bar-edit">
          <input
            className="project-name-input"
            autoFocus
            placeholder="Project name…"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') handleCreate()
              if (e.key === 'Escape') { setCreating(false); setNewName('') }
            }}
          />
          <button className="project-bar-btn" onClick={handleCreate} disabled={!newName.trim()}>Create</button>
          <button className="project-bar-btn" onClick={() => { setCreating(false); setNewName('') }}>Cancel</button>
        </span>
      ) : renaming ? (
        <span className="project-bar-edit">
          <input
            className="project-name-input"
            autoFocus
            value={renameValue}
            onChange={e => setRenameValue(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') handleRename()
              if (e.key === 'Escape') setRenaming(false)
            }}
          />
          <button className="project-bar-btn" onClick={handleRename} disabled={!renameValue.trim()}>Rename</button>
          <button className="project-bar-btn" onClick={() => setRenaming(false)}>Cancel</button>
        </span>
      ) : (
        <span className="project-bar-actions">
          <button className="project-bar-btn" onClick={() => setCreating(true)} title="New project">+ New project</button>
          {current && (
            <button
              className="project-bar-btn"
              onClick={() => { setRenaming(true); setRenameValue(current.name) }}
              title="Rename project"
            >Rename</button>
          )}
          {current && current.id !== 'default' && (
            <button className="project-bar-btn danger" onClick={handleDelete} title="Delete project">Delete</button>
          )}
        </span>
      )}
    </div>
  )
}
