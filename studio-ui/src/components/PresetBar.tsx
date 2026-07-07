import { useCallback, useEffect, useState } from 'react'

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
        <div className="preset-bar-left">
          <span className="preset-bar-icon">{expanded ? '▾' : '▸'}</span>
          <span className="preset-bar-label">Preset</span>
          <select
            className="preset-select"
            value={activePreset.id}
            onChange={e => selectPreset(e.target.value)}
            onClick={e => e.stopPropagation()}
          >
            {presets.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div className="preset-bar-right">
          <span className="preset-bar-meta">
            {activePreset.voice_speaker} · {activePreset.voice_language} · {activePreset.video_length_minutes}min
          </span>
        </div>
      </div>

      {expanded && (
        <div className="preset-settings">
          <div className="preset-row">
            <label className="preset-field-label">Style Prompt</label>
            <textarea
              className="preset-textarea"
              value={activePreset.style_prompt}
              onChange={e => updateField('style_prompt', e.target.value)}
              rows={2}
              placeholder="Image generation style prefix..."
            />
          </div>
          <div className="preset-row">
            <label className="preset-field-label">Narration Style</label>
            <textarea
              className="preset-textarea"
              value={activePreset.narration_style}
              onChange={e => updateField('narration_style', e.target.value)}
              rows={2}
              placeholder="How the script should be written..."
            />
          </div>
          <div className="preset-grid">
            <div className="preset-field">
              <label className="preset-field-label">Voice</label>
              <select
                className="preset-select-sm"
                value={activePreset.voice_speaker}
                onChange={e => updateField('voice_speaker', e.target.value)}
              >
                {SPEAKERS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Language</label>
              <select
                className="preset-select-sm"
                value={activePreset.voice_language}
                onChange={e => updateField('voice_language', e.target.value)}
              >
                {LANGUAGES.map(l => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Length</label>
              <select
                className="preset-select-sm"
                value={activePreset.video_length_minutes}
                onChange={e => updateField('video_length_minutes', Number(e.target.value))}
              >
                {LENGTHS.map(l => <option key={l} value={l}>{l} min</option>)}
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Motion</label>
              <select
                className="preset-select-sm"
                value={activePreset.video_provider}
                onChange={e => updateField('video_provider', e.target.value)}
              >
                <option value="ltx">LTX-2.3 action</option>
                <option value="kenburns">Ken Burns fallback</option>
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Style pack</label>
              <select
                className="preset-select-sm"
                value={activePreset.style_pack || ''}
                onChange={e => updateField('style_pack', e.target.value)}
              >
                <option value="">none</option>
                {stylePacks.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Engine</label>
              <select
                className="preset-select-sm"
                value={activePreset.default_visual_engine || ''}
                onChange={e => updateField('default_visual_engine', e.target.value)}
              >
                <option value="">auto</option>
                <option value="collage">collage</option>
                <option value="html">html</option>
                <option value="manim">manim</option>
              </select>
            </div>
            <div className="preset-field">
              <label className="preset-field-label">Voice provider</label>
              <select
                className="preset-select-sm"
                value={activePreset.tts_provider || 'qwen'}
                onChange={e => updateField('tts_provider', e.target.value)}
              >
                <option value="qwen">Qwen3-TTS (local)</option>
                <option value="voicebox">Voicebox app</option>
                <option value="elevenlabs">ElevenLabs</option>
              </select>
            </div>
            {activePreset.tts_provider === 'voicebox' && (
              <div className="preset-field">
                <label className="preset-field-label">Voicebox profile</label>
                <input
                  className="preset-save-input"
                  value={activePreset.voicebox_profile || ''}
                  onChange={e => updateField('voicebox_profile', e.target.value)}
                  placeholder="Narrator"
                />
              </div>
            )}
          </div>
          <div className="preset-actions">
            {saving ? (
              <div className="preset-save-row">
                <input
                  className="preset-save-input"
                  value={saveName}
                  onChange={e => setSaveName(e.target.value)}
                  placeholder="Preset name..."
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button className="preset-save-btn" onClick={handleSave}>Save</button>
                <button className="preset-cancel-btn" onClick={() => setSaving(false)}>Cancel</button>
              </div>
            ) : (
              <>
                <button className="preset-save-btn" onClick={() => { setSaving(true); setSaveName(activePreset.name) }}>
                  Save as Preset
                </button>
                {!activePreset.builtin && (
                  <button className="preset-delete-btn" onClick={() => handleDelete(activePreset.id)}>
                    Delete
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
