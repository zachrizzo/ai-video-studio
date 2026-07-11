import { useCallback, useEffect, useState } from 'react'
import { fetchPresets, fetchStylePackDetail, fetchVoiceboxProfiles } from '../api'
import type { Preset, StylePackDetail, VoiceboxProfile } from '../api'
import { TTS_LANGUAGES, TTS_SPEAKERS } from '../constants'
import { Modal } from './Modal'
import '../styles/preset-project-bar.css'

interface StylePack {
  id: string
  name: string
}

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
  // Live draft edited inside the Customize modal — seeded from activePreset
  // when the modal opens, and only committed back to activePreset on Save.
  const [draft, setDraft] = useState<Preset | null>(null)
  const [stylePacks, setStylePacks] = useState<StylePack[]>([])
  const [showCustomize, setShowCustomize] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [packDetail, setPackDetail] = useState<StylePackDetail | null>(null)
  const [packDetailError, setPackDetailError] = useState<string | null>(null)
  const [packDetailLoading, setPackDetailLoading] = useState(false)
  const [voiceboxProfiles, setVoiceboxProfiles] = useState<VoiceboxProfile[]>([])
  const [voiceboxAvailable, setVoiceboxAvailable] = useState(false)

  useEffect(() => {
    fetch('/api/style_packs')
      .then(r => r.json())
      .then(data => setStylePacks(data.style_packs || data.packs || []))
      .catch(() => setStylePacks([]))
  }, [])

  // Refreshed each time the modal opens (not just once) so a profile created
  // in the Voicebox app while this page was already open still shows up.
  useEffect(() => {
    if (!showCustomize) return
    fetchVoiceboxProfiles()
      .then(res => {
        setVoiceboxProfiles(res.profiles)
        setVoiceboxAvailable(res.available)
      })
      .catch(() => { setVoiceboxProfiles([]); setVoiceboxAvailable(false) })
  }, [showCustomize])

  // Fetch the full style-pack detail (palette/type/motion/texture/flux
  // prompt) whenever the modal is open and the selected pack changes. Reads
  // from the draft (not the committed activePreset) so the preview follows
  // in-progress edits rather than the stale committed preset.
  useEffect(() => {
    if (!showCustomize) return
    const packId = draft?.style_pack
    if (!packId) {
      setPackDetail(null)
      setPackDetailError(null)
      return
    }
    let cancelled = false
    setPackDetailLoading(true)
    setPackDetailError(null)
    fetchStylePackDetail(packId)
      .then(detail => { if (!cancelled) setPackDetail(detail) })
      .catch(() => { if (!cancelled) setPackDetailError(`Couldn't load "${packId}" style pack detail.`) })
      .finally(() => { if (!cancelled) setPackDetailLoading(false) })
    return () => { cancelled = true }
  }, [showCustomize, draft?.style_pack])

  useEffect(() => {
    fetchPresets()
      .then(list => {
        const nextPresets = dedupePresets(list)
        setPresets(nextPresets)
        if (!activePreset && nextPresets.length) {
          setActivePreset(nextPresets[0])
          onPresetChange?.(nextPresets[0])
        }
      })
      .catch(() => {})
  }, [])

  // Notify parent when the committed preset changes — a top-level selection
  // or a successful save. Draft edits inside the modal never touch
  // activePreset until Save, so this no longer fires per keystroke.
  useEffect(() => {
    if (activePreset) onPresetChange?.(activePreset)
  }, [activePreset])

  const selectPreset = useCallback((id: string) => {
    const p = presets.find(p => p.id === id)
    if (p) setActivePreset(p)
  }, [presets])

  const openCustomize = useCallback(() => {
    if (!activePreset) return
    setDraft(activePreset)
    setSaveError(null)
    setShowCustomize(true)
  }, [activePreset])

  const closeCustomize = useCallback(() => {
    setShowCustomize(false)
    setSaving(false)
    setSaveError(null)
  }, [])

  const updateField = useCallback((field: string, value: string | number | boolean | null) => {
    setDraft(prev => (prev ? { ...prev, [field]: value } : prev))
  }, [])

  // Generation-quality overrides are all optional (undefined/null = "use the
  // pipeline's own default"). These helpers turn an empty select/input back
  // into null instead of an empty string, so "Auto" really means unset.
  const updateOptionalString = useCallback((field: string, raw: string) => {
    updateField(field, raw === '' ? null : raw)
  }, [updateField])
  const updateOptionalNumber = useCallback((field: string, raw: string) => {
    updateField(field, raw === '' ? null : Number(raw))
  }, [updateField])
  const updateOptionalBoolean = useCallback((field: string, raw: string) => {
    updateField(field, raw === '' ? null : raw === 'true')
  }, [updateField])

  const handleSave = useCallback(async () => {
    if (!draft || !saveName.trim()) return
    const id = saveName.trim().toLowerCase().replace(/\s+/g, '_')
    const body = { ...draft, id, name: saveName.trim(), builtin: false }
    setSaveError(null)
    try {
      const r = await fetch('/api/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        setSaveError(`Couldn't save preset (${r.status}).`)
        return
      }
      const nextPresets = dedupePresets(await fetchPresets())
      setPresets(nextPresets)
      const saved = nextPresets.find(p => p.id === id) || null
      if (saved) {
        setActivePreset(saved)
        setDraft(saved)
      }
      setSaving(false)
      setSaveName('')
      setShowCustomize(false)
    } catch {
      setSaveError("Couldn't save preset — check your connection and try again.")
    }
  }, [draft, saveName])

  const handleDelete = useCallback(async (id: string) => {
    await fetch(`/api/presets/${id}`, { method: 'DELETE' })
    setPresets(prev => prev.filter(p => p.id !== id))
    if (activePreset?.id === id) setActivePreset(presets.filter(p => p.id !== id)[0] || null)
  }, [activePreset, presets])

  if (!activePreset) return null

  return (
    <div className="preset-bar">
      <div className="preset-bar-header">
        <span className="bar-label">Style preset</span>
        <select
          className="bar-select"
          value={activePreset.id}
          onChange={e => selectPreset(e.target.value)}
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
          <button type="button" className="btn btn-ghost btn-sm" onClick={openCustomize}>
            Customize
          </button>
        </div>
      </div>

      {showCustomize && draft && (
        <Modal
          title={`Customize · ${draft.name}`}
          onClose={closeCustomize}
          footer={
            saving ? (
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
                <button className="btn btn-ghost" onClick={() => { setSaving(false); setSaveError(null) }}>Cancel</button>
                {saveError && <p className="error-state">{saveError}</p>}
              </div>
            ) : (
              <>
                <button className="btn btn-secondary" onClick={() => { setSaving(true); setSaveName(draft.name); setSaveError(null) }}>
                  Save as new preset
                </button>
                {!activePreset.builtin && (
                  <button className="btn btn-danger" onClick={() => handleDelete(activePreset.id)}>
                    Delete preset
                  </button>
                )}
              </>
            )
          }
        >
          <div className="preset-settings-prompts">
            <div className="field">
              <label className="field-label">Visual style</label>
              <textarea
                className="textarea"
                value={draft.style_prompt}
                onChange={e => updateField('style_prompt', e.target.value)}
                rows={3}
                placeholder="How generated images should look — e.g. cinematic watercolor, muted palette…"
              />
            </div>
            <div className="field">
              <label className="field-label">Narration style</label>
              <textarea
                className="textarea"
                value={draft.narration_style}
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
                value={draft.voice_speaker}
                onChange={e => updateField('voice_speaker', e.target.value)}
              >
                {TTS_SPEAKERS.map(s => <option key={s} value={s}>{formatName(s)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Language</label>
              <select
                className="select"
                value={draft.voice_language}
                onChange={e => updateField('voice_language', e.target.value)}
              >
                {TTS_LANGUAGES.map(l => <option key={l} value={l}>{formatName(l)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Length</label>
              <select
                className="select"
                value={draft.video_length_minutes}
                onChange={e => updateField('video_length_minutes', Number(e.target.value))}
              >
                {LENGTHS.map(l => <option key={l} value={l}>{formatLength(l)}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Motion</label>
              <select
                className="select"
                value={draft.video_provider}
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
                value={draft.style_pack || ''}
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
                value={draft.default_visual_engine || ''}
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
                value={draft.tts_provider || 'voicebox'}
                onChange={e => updateField('tts_provider', e.target.value)}
              >
                <option value="qwen">Qwen3-TTS (local)</option>
                <option value="voicebox">Voicebox app</option>
                <option value="elevenlabs">ElevenLabs</option>
              </select>
            </div>
            {draft.tts_provider === 'voicebox' && (
              <div className="field">
                <label className="field-label">Voicebox profile</label>
                {voiceboxAvailable && voiceboxProfiles.length > 0 ? (
                  <select
                    className="select"
                    value={draft.voicebox_profile || ''}
                    onChange={e => updateField('voicebox_profile', e.target.value)}
                  >
                    <option value="">Choose a profile…</option>
                    {voiceboxProfiles.map(p => (
                      <option key={p.id} value={p.name}>
                        {p.name}{p.default_engine ? ` (${formatName(p.default_engine)})` : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <>
                    <input
                      className="input"
                      value={draft.voicebox_profile || ''}
                      onChange={e => updateField('voicebox_profile', e.target.value)}
                      placeholder="Narrator"
                    />
                    <p className="advanced-settings-hint">
                      {voiceboxAvailable
                        ? 'No profiles found — create one in the Voicebox app first.'
                        : "Voicebox app isn't running — launch it to pick from your real profiles instead of typing a name."}
                    </p>
                  </>
                )}
              </div>
            )}
            {draft.tts_provider === 'qwen' && (
              <div className="field">
                <label className="field-label">Qwen model size</label>
                <select
                  className="select"
                  value={draft.qwen_model_size ?? ''}
                  onChange={e => updateOptionalString('qwen_model_size', e.target.value)}
                >
                  <option value="">Auto (0.6B)</option>
                  <option value="0.6B">0.6B (fast)</option>
                  <option value="1.7B">1.7B (higher quality)</option>
                </select>
              </div>
            )}
          </div>

          <div className="advanced-settings">
            <span className="section-title">Image generation</span>
            <p className="advanced-settings-hint">Leave on Auto to use the pipeline's own defaults.</p>
            <div className="preset-settings-grid">
              <div className="field">
                <label className="field-label">Image model</label>
                <select
                  className="select"
                  value={draft.image_model ?? ''}
                  onChange={e => updateOptionalString('image_model', e.target.value)}
                >
                  <option value="">Auto (Z-Image Turbo)</option>
                  <option value="z-image-turbo">Z-Image Turbo</option>
                  <option value="schnell">FLUX Schnell (faster, lower quality)</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label">Steps</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={draft.image_steps ?? ''}
                  onChange={e => updateOptionalNumber('image_steps', e.target.value)}
                  placeholder="Auto (8)"
                />
              </div>
              <div className="field">
                <label className="field-label">Quantize</label>
                <select
                  className="select"
                  value={draft.image_quantize ?? ''}
                  onChange={e => updateOptionalNumber('image_quantize', e.target.value)}
                >
                  <option value="">Auto (4)</option>
                  <option value={4}>4 (lower memory)</option>
                  <option value={8}>8 (higher fidelity)</option>
                </select>
              </div>
            </div>
          </div>

          <div className="advanced-settings">
            <span className="section-title">Video motion (LTX)</span>
            <p className="advanced-settings-hint">Only applies when Motion above is set to LTX-2.3 action.</p>
            <div className="preset-settings-grid">
              <div className="field">
                <label className="field-label">LTX steps</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={draft.ltx_steps ?? ''}
                  onChange={e => updateOptionalNumber('ltx_steps', e.target.value)}
                  placeholder="Auto (30)"
                />
              </div>
              <div className="field">
                <label className="field-label">Resolution</label>
                <input
                  className="input"
                  value={draft.ltx_resolution ?? ''}
                  onChange={e => updateOptionalString('ltx_resolution', e.target.value)}
                  placeholder="Auto (704x448)"
                />
              </div>
              <div className="field">
                <label className="field-label">Clip length (s)</label>
                <input
                  className="input"
                  type="number"
                  min={0.5}
                  step={0.5}
                  value={draft.ltx_clip_seconds ?? ''}
                  onChange={e => updateOptionalNumber('ltx_clip_seconds', e.target.value)}
                  placeholder="Auto (3.0)"
                />
              </div>
              <div className="field">
                <label className="field-label">CFG scale</label>
                <input
                  className="input"
                  type="number"
                  step={0.1}
                  value={draft.ltx_cfg_scale ?? ''}
                  onChange={e => updateOptionalNumber('ltx_cfg_scale', e.target.value)}
                  placeholder="Auto (3.0)"
                />
              </div>
              <div className="field">
                <label className="field-label">STG scale</label>
                <input
                  className="input"
                  type="number"
                  step={0.1}
                  value={draft.ltx_stg_scale ?? ''}
                  onChange={e => updateOptionalNumber('ltx_stg_scale', e.target.value)}
                  placeholder="Auto (1.0)"
                />
              </div>
              <div className="field">
                <label className="field-label">Prefer extend</label>
                <select
                  className="select"
                  value={draft.ltx_prefer_extend === null || draft.ltx_prefer_extend === undefined ? '' : String(draft.ltx_prefer_extend)}
                  onChange={e => updateOptionalBoolean('ltx_prefer_extend', e.target.value)}
                >
                  <option value="">Auto (off)</option>
                  <option value="true">On</option>
                  <option value="false">Off</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label">Ken Burns fallback</label>
                <select
                  className="select"
                  value={draft.video_fallback_to_kenburns === null || draft.video_fallback_to_kenburns === undefined ? '' : String(draft.video_fallback_to_kenburns)}
                  onChange={e => updateOptionalBoolean('video_fallback_to_kenburns', e.target.value)}
                >
                  <option value="">Auto (on)</option>
                  <option value="true">On — use Ken Burns if LTX fails</option>
                  <option value="false">Off — fail instead of falling back</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label">Ken Burns zoom</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  step={0.01}
                  value={draft.kenburns_zoom ?? ''}
                  onChange={e => updateOptionalNumber('kenburns_zoom', e.target.value)}
                  placeholder="Auto (1.12)"
                />
              </div>
            </div>
          </div>

          <div className="style-pack-detail">
            <span className="section-title">Style pack detail</span>
            {!draft.style_pack && (
              <p className="style-pack-empty">No style pack selected — images use only the visual style prompt above.</p>
            )}
            {draft.style_pack && packDetailLoading && (
              <div className="skeleton style-pack-skeleton" aria-hidden="true" />
            )}
            {draft.style_pack && packDetailError && (
              <p className="style-pack-empty error-state">{packDetailError}</p>
            )}
            {packDetail && !packDetailLoading && (
              <div className="style-pack-grid">
                <div className="style-pack-row">
                  <span className="field-label">Palette</span>
                  <div className="palette-swatches">
                    {Object.entries(packDetail.palette).map(([role, hex]) => (
                      <div className="palette-swatch" key={role} title={`${role}: ${hex}`}>
                        <span className="palette-swatch-color" style={{ background: hex }} />
                        <span className="palette-swatch-label">{role}</span>
                        <span className="palette-swatch-hex">{hex}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="style-pack-row">
                  <span className="field-label">Typography</span>
                  <div className="style-pack-kv">
                    {Object.entries(packDetail.type).map(([role, font]) => (
                      <span key={role}><b>{role}</b> {font}</span>
                    ))}
                  </div>
                </div>
                <div className="style-pack-row">
                  <span className="field-label">Motion</span>
                  <div className="style-pack-kv">
                    {Object.entries(packDetail.motion).map(([key, value]) => (
                      <span key={key}><b>{formatName(key)}</b> {String(value)}</span>
                    ))}
                  </div>
                </div>
                <div className="style-pack-row">
                  <span className="field-label">Texture</span>
                  <div className="style-pack-kv">
                    {Object.entries(packDetail.texture).map(([key, value]) => (
                      <span key={key}><b>{formatName(key)}</b> {String(value)}</span>
                    ))}
                  </div>
                </div>
                {(packDetail.flux_prefix || packDetail.flux_suffix) && (
                  <div className="style-pack-row style-pack-row-wide">
                    <span className="field-label">Image style (FLUX)</span>
                    <p className="style-pack-flux">
                      {packDetail.flux_prefix}
                      {packDetail.flux_suffix && <><br />{packDetail.flux_suffix}</>}
                    </p>
                  </div>
                )}
                {packDetail.fonts.length > 0 && (
                  <div className="style-pack-row">
                    <span className="field-label">Bundled fonts</span>
                    <div className="style-pack-kv">
                      {packDetail.fonts.map(f => <span key={f}>{f}</span>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}
