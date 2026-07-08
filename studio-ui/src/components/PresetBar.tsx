import { useCallback, useEffect, useState } from 'react'
import '../styles/preset-project-bar.css'

interface Preset {
  id: string
  name: string
  description: string
  builtin: boolean
  style_prompt: string
  video_length_minutes: number
  voice_speaker: string
  voice_language: string
  video_provider: string
  narration_style: string
  style_pack?: string | null
  default_visual_engine?: string | null
  sfx_style?: string | null
  tts_provider?: string | null
  voicebox_profile?: string | null
}

interface StylePack {
  id: string
  name: string
}

const SPEAKERS = ['serena', 'vivian', 'aiden', 'dylan', 'eric', 'ono_anna', 'ryan', 'sohee', 'uncle_fu']
const LANGUAGES = ['auto', 'english', 'chinese', 'japanese', 'korean', 'german', 'french', 'russian', 'portuguese', 'spanish', 'italian']
const LENGTHS = [0.5, 1, 2, 3, 5, 10]

function formatName(value: string): string {
  return value
    .split('_')
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatLength(minutes: number): string {
  if (minutes < 1) return `${Math.round(minutes * 60)} seconds`
  return minutes === 1 ? '1 minute' : `${minutes} minutes`
}

function dedupePresets(items: Preset[]): Preset[] {
  const byId = new Map<string, Preset>()
  for (const preset of items) {
    const existing = byId.get(preset.id)
    if (!existing || !preset.builtin) {
      byId.set(preset.id, preset)
    }
  }
  return Array.from(byId.values())
}

export function PresetBar({ onPresetChange }: { onPresetChange?: (preset: Preset) => void }) {
  const [presets, setPresets] = useState<Preset[]>([])
  const [activePreset, setActivePreset] = useState<Preset | null>(null)
  const [stylePacks, setStylePacks] = useState<StylePack[]>([])
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveName, setSaveName] = useState('')

  useEffect(() => {
    fetch('/api/style_packs')
      .then(r => r.json())
      .then(data => setStylePacks(data.style_packs || data.packs || []))
      .catch(() => setStylePacks([]))
  }, [])

  useEffect(() => {
    fetch('/api/presets')
      .then(r => r.json())
      .then(data => {
        const nextPresets = dedupePresets(data.presets || [])
        setPresets(nextPresets)
        if (!activePreset && nextPresets.length) {
          setActivePreset(nextPresets[0])
          onPresetChange?.(nextPresets[0])
        }
      })
      .catch(() => {})
  }, [])

  // Notify parent when preset changes
  useEffect(() => {
    if (activePreset) onPresetChange?.(activePreset)
  }, [activePreset])

  const selectPreset = useCallback((id: string) => {
    const p = presets.find(p => p.id === id)
    if (p) setActivePreset(p)
  }, [presets])

  const updateField = useCallback((field: string, value: string | number) => {
    if (!activePreset) return
    setActivePreset({ ...activePreset, [field]: value })
  }, [activePreset])

  const handleSave = useCallback(async () => {
    if (!activePreset || !saveName.trim()) return
    const id = saveName.trim().toLowerCase().replace(/\s+/g, '_')
    const body = { ...activePreset, id, name: saveName.trim(), builtin: false }
    await fetch('/api/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const r = await fetch('/api/presets')
    const data = await r.json()
    const nextPresets = dedupePresets(data.presets || [])
    setPresets(nextPresets)
    setSaving(false)
    setSaveName('')
  }, [activePreset, saveName])

  const handleDelete = useCallback(async (id: string) => {
    await fetch(`/api/presets/${id}`, { method: 'DELETE' })
    setPresets(prev => prev.filter(p => p.id !== id))
    if (activePreset?.id === id) setActivePreset(presets.filter(p => p.id !== id)[0] || null)
  }, [activePreset, presets])

  if (!activePreset) return null

  return (
    <div className="preset-bar">
      <div className="preset-bar-header" onClick={() => setExpanded(!expanded)}>
        <span className="bar-label">Style preset</span>
        <select
          className="bar-select"
          value={activePreset.id}
          onChange={e => selectPreset(e.target.value)}
          onClick={e => e.stopPropagation()}
          aria-label="Style preset"
        >
          {presets.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <div className="preset-bar-side">
          <span className="preset-bar-meta">
            {formatName(activePreset.voice_speaker)} · {formatName(activePreset.voice_language)} · {formatLength(activePreset.video_length_minutes)}
          </span>
          <button type="button" className="btn btn-ghost btn-sm" aria-expanded={expanded}>
            Customize
            <svg
              className={`preset-chevron${expanded ? ' open' : ''}`}
              width="10" height="6" viewBox="0 0 10 6" aria-hidden="true"
            >
              <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </div>

      {expanded && (
        <div className="preset-settings">
          <div className="preset-settings-prompts">
            <div className="field">
              <label className="field-label">Visual style</label>
              <textarea
                className="textarea"
                value={activePreset.style_prompt}
                onChange={e => updateField('style_prompt', e.target.value)}
                rows={3}
                placeholder="How generated images should look — e.g. cinematic watercolor, muted palette…"
              />
            </div>
            <div className="field">
              <label className="field-label">Narration style</label>
              <textarea
                className="textarea"
                value={activePreset.narration_style}
                onChange={e => updateField('narration_style', e.target.value)}
                rows={3}
                placeholder="How the script should be written — tone, pacing, point of view…"
              />
            </div>
          </div>
          <div className="preset-settings-grid">
            <div className="field">
              <label className="field-label">Voice</label>
              <select
                className="select"
                value={activePreset.voice_speaker}
                onChange={e => updateField('voice_speaker', e.target.value)}
              >
                {SPEAKERS.map(s => <option key={s} value={s}>{formatName(s)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Language</label>
              <select
                className="select"
                value={activePreset.voice_language}
                onChange={e => updateField('voice_language', e.target.value)}
              >
                {LANGUAGES.map(l => <option key={l} value={l}>{formatName(l)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Length</label>
              <select
                className="select"
                value={activePreset.video_length_minutes}
                onChange={e => updateField('video_length_minutes', Number(e.target.value))}
              >
                {LENGTHS.map(l => <option key={l} value={l}>{formatLength(l)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Motion</label>
              <select
                className="select"
                value={activePreset.video_provider}
                onChange={e => updateField('video_provider', e.target.value)}
              >
                <option value="ltx">LTX-2.3 action</option>
                <option value="kenburns">Ken Burns fallback</option>
              </select>
            </div>
            <div className="field">
              <label className="field-label">Style pack</label>
              <select
                className="select"
                value={activePreset.style_pack || ''}
                onChange={e => updateField('style_pack', e.target.value)}
              >
                <option value="">None</option>
                {stylePacks.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Visual engine</label>
              <select
                className="select"
                value={activePreset.default_visual_engine || ''}
                onChange={e => updateField('default_visual_engine', e.target.value)}
              >
                <option value="">Auto</option>
                <option value="collage">Collage</option>
                <option value="html">HTML</option>
                <option value="manim">Manim</option>
              </select>
            </div>
            <div className="field">
              <label className="field-label">Voice provider</label>
              <select
                className="select"
                value={activePreset.tts_provider || 'qwen'}
                onChange={e => updateField('tts_provider', e.target.value)}
              >
                <option value="qwen">Qwen3-TTS (local)</option>
                <option value="voicebox">Voicebox app</option>
                <option value="elevenlabs">ElevenLabs</option>
              </select>
            </div>
            {activePreset.tts_provider === 'voicebox' && (
              <div className="field">
                <label className="field-label">Voicebox profile</label>
                <input
                  className="input"
                  value={activePreset.voicebox_profile || ''}
                  onChange={e => updateField('voicebox_profile', e.target.value)}
                  placeholder="Narrator"
                />
              </div>
            )}
          </div>
          <div className="preset-settings-footer">
            {saving ? (
              <div className="preset-save-row">
                <input
                  className="input preset-save-input"
                  value={saveName}
                  onChange={e => setSaveName(e.target.value)}
                  placeholder="Name this preset…"
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button className="btn btn-primary" onClick={handleSave}>Save</button>
                <button className="btn btn-ghost" onClick={() => setSaving(false)}>Cancel</button>
              </div>
            ) : (
              <>
                <button className="btn btn-secondary" onClick={() => { setSaving(true); setSaveName(activePreset.name) }}>
                  Save as new preset
                </button>
                {!activePreset.builtin && (
                  <button className="btn btn-danger" onClick={() => handleDelete(activePreset.id)}>
                    Delete preset
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
