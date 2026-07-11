import { useCallback, useEffect, useRef, useState } from 'react'
import {
  EXTEND_CONTEXT_MAP, EXTEND_MODE_MAP, GEN_TYPE_TO_MODE, MODEL_MAP,
  RETAKE_MODE_MAP, TTS_LANGUAGES, TTS_SPEAKERS,
} from '../constants'
import { parseTimecode } from './formatters'
import '../styles/generate-panel.css'

/* ── Types ─────────────────────────────────────────────────────────────────── */

type GenMode = 'text-to-image' | 'text-to-speech' | 'text-to-video' | 'image-to-video' | 'audio-to-video' | 'retake' | 'extend' | 'video-hdr'
type Backend = 'local' | 'cloud'
type GenStatus = 'generating' | 'done' | 'failed'

interface Generation {
  id: string
  mode: GenMode
  status: GenStatus
  prompt: string
  created_at: number
  output_url: string | null
  thumbnail_url?: string | null
  error: string | null
  progress?: number
  progress_step?: string
}

interface FileUpload {
  file: File
  path: string | null
  previewUrl: string
  uploading: boolean
}

const API = '' // proxied via vite

// The backend never returns a `mode` field — it returns canonical `type`
// (e.g. "image"/"video" from the chat agent's classic generate tools, or the
// GenMode-shaped ids like "text-to-video" from every other one-shot path).
// Every ingestion point (poll + gallery load) must derive `mode` from it.
function toGeneration(raw: Record<string, unknown>): Generation {
  const type = String(raw.type ?? '')
  return { ...raw, mode: (GEN_TYPE_TO_MODE[type] ?? type) as GenMode } as Generation
}

const MODES: { key: GenMode; label: string; icon: React.ReactNode }[] = [
  { key: 'text-to-image', label: 'Text to Image', icon: <ImageGenIcon /> },
  { key: 'text-to-speech', label: 'Text to Speech', icon: <SpeechIcon /> },
  { key: 'text-to-video', label: 'Text to Video', icon: <TextIcon /> },
  { key: 'image-to-video', label: 'Image to Video', icon: <ImageIcon /> },
  { key: 'audio-to-video', label: 'Audio to Video', icon: <AudioIcon /> },
  { key: 'retake', label: 'Retake Video', icon: <RetakeIcon /> },
  { key: 'extend', label: 'Extend Video', icon: <ExtendIcon /> },
  { key: 'video-hdr', label: 'Video to HDR', icon: <HdrIcon /> },
]

const CREATE_MODE_KEYS: GenMode[] = ['text-to-image', 'text-to-speech', 'text-to-video', 'image-to-video', 'audio-to-video']

const SIDEBAR_GROUPS = [
  { title: 'Create', modes: MODES.filter(m => CREATE_MODE_KEYS.includes(m.key)) },
  { title: 'Edit', modes: MODES.filter(m => !CREATE_MODE_KEYS.includes(m.key)) },
]

const LOCAL_MODES: GenMode[] = ['text-to-image', 'text-to-speech', 'text-to-video', 'image-to-video', 'audio-to-video', 'retake', 'extend']

// Modes with a working cloud path (LTX Cloud). text-to-image/text-to-speech
// have no `backend`/`api_key` field in their request models at all, so the
// "Run on: Local / Cloud API" toggle is meaningless for them.
const CLOUD_CAPABLE_MODES: GenMode[] = ['text-to-video', 'image-to-video', 'audio-to-video', 'retake', 'extend', 'video-hdr']

const TTS_MODELS = ['0.6B (Fast)', '1.7B (Quality)']

const IMAGE_MODELS = ['FLUX Schnell', 'Z-Image Turbo']
const IMAGE_RESOLUTIONS = ['512x512', '768x768', '1024x1024', '1920x1080', '1080x1920']

const MODELS = ['LTX-2.3 Pro', 'LTX-2.3 Fast']
const DURATIONS = ['2 sec', '4 sec', '6 sec', '8 sec', '10 sec']
const RESOLUTIONS = ['720p', '1080p', '1440p', '4K']
const FPS_OPTIONS = ['24 fps', '25 fps', '30 fps', '50 fps']
const CAMERA_MOTIONS = ['None', 'Pan Left', 'Pan Right', 'Pan Up', 'Pan Down', 'Zoom In', 'Zoom Out', 'Dolly In', 'Dolly Out']
const RETAKE_MODES = ['Replace audio and video', 'Replace video only', 'Replace audio only']
const EXTEND_MODES = ['From the end', 'From the start']
const EXTEND_CONTEXTS = ['Auto', 'Full', 'Last 2 seconds']

const MODE_DESCRIPTIONS: Record<GenMode, string> = {
  'text-to-image': 'Generate an image from a text prompt using FLUX.',
  'text-to-speech': 'Generate natural speech from text using Qwen3-TTS.',
  'text-to-video': 'Generate a video from a text prompt.',
  'image-to-video': 'Animate an image into a video clip.',
  'audio-to-video': 'Generate a video driven by an audio track.',
  'retake': 'Replace a section of an existing video.',
  'extend': 'Extend a video from the start or end.',
  'video-hdr': 'Upscale a video to HDR quality.',
}

/* ── Main Component ────────────────────────────────────────────────────────── */

export function GeneratePanel({ style }: { style?: React.CSSProperties }) {
  const [mode, setMode] = useState<GenMode>('text-to-video')
  const [backend, setBackend] = useState<Backend>('local')
  const [apiKey, setApiKey] = useState('')
  const [generating, setGenerating] = useState(false)
  const [generations, setGenerations] = useState<Generation[]>([])
  const [selectedGen, setSelectedGen] = useState<Generation | null>(null)
  const pollRef = useRef<Map<string, number>>(new Map())

  // Form state
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState(MODELS[0])
  const [duration, setDuration] = useState(DURATIONS[1])
  const [resolution, setResolution] = useState(RESOLUTIONS[0])
  const [fps, setFps] = useState(FPS_OPTIONS[0])
  const [generateAudio, setGenerateAudio] = useState(true)
  const [cameraMotion, setCameraMotion] = useState(CAMERA_MOTIONS[0])

  // Text-to-image
  const [imageModel, setImageModel] = useState(IMAGE_MODELS[0])
  const [imageResolution, setImageResolution] = useState(IMAGE_RESOLUTIONS[3]) // 1920x1080

  // Text-to-speech
  const [ttsSpeaker, setTtsSpeaker] = useState(TTS_SPEAKERS[0])
  const [ttsLanguage, setTtsLanguage] = useState(TTS_LANGUAGES[0])
  const [ttsModel, setTtsModel] = useState(TTS_MODELS[0])
  const [ttsInstruct, setTtsInstruct] = useState('')
  const [ttsRefAudio, setTtsRefAudio] = useState<FileUpload | null>(null)

  // Image-to-video
  const [firstFrame, setFirstFrame] = useState<FileUpload | null>(null)

  // Audio-to-video
  const [audioFile, setAudioFile] = useState<FileUpload | null>(null)
  const [imageFile, setImageFile] = useState<FileUpload | null>(null)
  const [guidanceScale, setGuidanceScale] = useState(75)

  // Retake
  const [retakeVideo, setRetakeVideo] = useState<FileUpload | null>(null)
  const [startTime, setStartTime] = useState('00:00.00')
  const [retakeDuration, setRetakeDuration] = useState('00:02.00')
  const [retakeMode, setRetakeMode] = useState(RETAKE_MODES[0])

  // Extend
  const [extendVideo, setExtendVideo] = useState<FileUpload | null>(null)
  const [extendMode, setExtendMode] = useState(EXTEND_MODES[0])
  const [extendDuration, setExtendDuration] = useState('00:05.00')
  const [extendContext, setExtendContext] = useState(EXTEND_CONTEXTS[0])

  // HDR
  const [hdrVideo, setHdrVideo] = useState<FileUpload | null>(null)

  // Poll generation until done/failed. fallbackMode is used only if the
  // gen_id never resolves (404s out) and we have to synthesize a failed
  // Generation without ever having seen the backend's real `type`.
  const pollGeneration = useCallback((genId: string, fallbackMode: GenMode) => {
    let consecutiveNotFound = 0
    const stop = (interval: number) => {
      window.clearInterval(interval)
      pollRef.current.delete(genId)
      if (pollRef.current.size === 0) setGenerating(false)
    }
    const interval = window.setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/generate/${genId}`)
        if (r.status === 404) {
          consecutiveNotFound += 1
          // ~30s of 404s (1.5s tick) means this gen_id will never resolve —
          // stop polling instead of spinning forever (previously the classic
          // /api/generate/undefined case when a POST never returned an id).
          if (consecutiveNotFound >= 20) {
            const error = 'Generation not found — it may have failed to start.'
            setGenerations(prev => {
              const idx = prev.findIndex(g => g.id === genId)
              if (idx >= 0) {
                const next = [...prev]
                next[idx] = { ...next[idx], status: 'failed', error }
                return next
              }
              const failed: Generation = {
                id: genId, mode: fallbackMode, status: 'failed', prompt: '',
                created_at: Date.now() / 1000, output_url: null, error,
              }
              return [failed, ...prev]
            })
            setSelectedGen(prev => (prev?.id === genId ? { ...prev, status: 'failed', error } : prev))
            stop(interval)
          }
          return
        }
        if (!r.ok) return
        consecutiveNotFound = 0
        const gen = toGeneration(await r.json())
        setGenerations(prev => {
          const idx = prev.findIndex(g => g.id === genId)
          if (idx >= 0) {
            const next = [...prev]
            next[idx] = gen
            return next
          }
          return [gen, ...prev]
        })
        if (gen.status === 'done' || gen.status === 'failed') {
          stop(interval)
          if (gen.status === 'done') setSelectedGen(gen)
        }
      } catch { /* retry next tick */ }
    }, 1500)
    pollRef.current.set(genId, interval)
  }, [])

  // Load past generations
  useEffect(() => {
    fetch(`${API}/api/generations`)
      .then(r => r.json())
      .then(data => {
        const raw: Record<string, unknown>[] = data.generations || []
        const gens = raw.map(toGeneration)
        setGenerations(gens)
        const inProgress = gens.filter(g => g.status === 'generating')
        if (inProgress.length > 0) {
          setGenerating(true)
          inProgress.forEach(g => pollGeneration(g.id, g.mode))
        }
      })
      .catch(() => {})
  }, [pollGeneration])

  // Cleanup polls
  useEffect(() => {
    return () => {
      pollRef.current.forEach(id => window.clearInterval(id))
    }
  }, [])

  // File upload helper
  const uploadFile = useCallback(async (file: File): Promise<string | null> => {
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await fetch(`${API}/api/upload`, { method: 'POST', body: form })
      if (!r.ok) return null
      const data = await r.json()
      return data.path
    } catch {
      return null
    }
  }, [])

  // Handle file selection for a drop zone
  const handleFileSelect = useCallback(
    (setter: React.Dispatch<React.SetStateAction<FileUpload | null>>) =>
      async (file: File) => {
        const previewUrl = URL.createObjectURL(file)
        setter({ file, path: null, previewUrl, uploading: true })
        const path = await uploadFile(file)
        setter(prev => (prev && prev.file === file) ? { ...prev, path, uploading: false } : prev)
      },
    [uploadFile]
  )

  // Delete a generation
  const handleDelete = useCallback(async (genId: string) => {
    await fetch(`${API}/api/generate/${genId}`, { method: 'DELETE' })
    setGenerations(prev => prev.filter(g => g.id !== genId))
    if (selectedGen?.id === genId) setSelectedGen(null)
  }, [selectedGen])

  // Stop a running generation
  const handleStop = useCallback(async (genId: string) => {
    await fetch(`${API}/api/generate/${genId}/stop`, { method: 'POST' })
    // Clear the poll
    const interval = pollRef.current.get(genId)
    if (interval) {
      window.clearInterval(interval)
      pollRef.current.delete(genId)
    }
    setGenerations(prev => prev.filter(g => g.id !== genId))
    if (selectedGen?.id === genId) setSelectedGen(null)
    if (pollRef.current.size === 0) setGenerating(false)
  }, [selectedGen])

  // Clear form
  const handleClear = useCallback(() => {
    setPrompt('')
    setModel(MODELS[0])
    setDuration(DURATIONS[1])
    setResolution(RESOLUTIONS[0])
    setFps(FPS_OPTIONS[0])
    setGenerateAudio(true)
    setCameraMotion(CAMERA_MOTIONS[0])
    setFirstFrame(null)
    setAudioFile(null)
    setImageFile(null)
    setGuidanceScale(75)
    setRetakeVideo(null)
    setStartTime('00:00.00')
    setRetakeDuration('00:02.00')
    setRetakeMode(RETAKE_MODES[0])
    setExtendVideo(null)
    setExtendMode(EXTEND_MODES[0])
    setExtendDuration('00:05.00')
    setExtendContext(EXTEND_CONTEXTS[0])
    setHdrVideo(null)
    setImageModel(IMAGE_MODELS[0])
    setImageResolution(IMAGE_RESOLUTIONS[3])
  }, [])

  // Parse numeric values from display strings like "4 sec" -> 4, "24 fps" -> 24
  const parseDuration = (s: string) => parseInt(s) || 4
  const parseFps = (s: string) => parseInt(s) || 24
  const parseResolution = (s: string) => {
    const map: Record<string, string> = { '720p': '1280x720', '1080p': '1920x1080', '1440p': '2560x1440', '4K': '3840x2160' }
    return map[s] || '1280x720'
  }
  const parseCameraMotion = (s: string) => s === 'None' ? null : s.toLowerCase().replace(/ /g, '_')

  // Generate
  const handleGenerate = useCallback(async () => {
    if (generating) return
    const modeSupportsCloud = CLOUD_CAPABLE_MODES.includes(mode)
    if (modeSupportsCloud && backend === 'cloud' && !apiKey.trim()) return

    let endpoint = ''
    let body: Record<string, unknown> = { backend }
    if (modeSupportsCloud && backend === 'cloud') {
      body.api_key = apiKey.trim()
    }

    const mappedModel = MODEL_MAP[model] || model

    switch (mode) {
      case 'text-to-image':
        if (!prompt.trim()) return
        endpoint = '/api/generate/image'
        body = { prompt: prompt.trim() }
        break
      case 'text-to-speech':
        if (!prompt.trim()) return
        endpoint = '/api/generate/tts'
        body = {
          text: prompt.trim(),
          speaker: ttsSpeaker,
          language: ttsLanguage,
          model_size: ttsModel.startsWith('1.7') ? '1.7B' : '0.6B',
          instruct: ttsInstruct.trim() || null,
          ref_audio: ttsRefAudio?.path || null,
        }
        break
      case 'text-to-video':
        if (!prompt.trim()) return
        endpoint = '/api/generate/text-to-video'
        body = {
          ...body,
          prompt: prompt.trim(),
          model: mappedModel,
          duration: parseDuration(duration),
          resolution: parseResolution(resolution),
          fps: parseFps(fps),
          camera_motion: parseCameraMotion(cameraMotion),
          generate_audio: generateAudio,
        }
        break
      case 'image-to-video':
        if (!firstFrame?.path) return
        endpoint = '/api/generate/image-to-video'
        body = {
          ...body,
          image_path: firstFrame.path,
          prompt: prompt.trim(),
          model: mappedModel,
          duration: parseDuration(duration),
          resolution: parseResolution(resolution),
          fps: parseFps(fps),
          camera_motion: parseCameraMotion(cameraMotion),
          generate_audio: generateAudio,
        }
        break
      case 'audio-to-video':
        if (!audioFile?.path) return
        endpoint = '/api/generate/audio-to-video'
        body = {
          ...body,
          audio_uri: audioFile.path,
          image_uri: imageFile?.path || null,
          prompt: prompt.trim(),
          model: mappedModel,
          resolution: parseResolution(resolution),
          guidance_scale: guidanceScale,
        }
        break
      case 'retake':
        if (!retakeVideo?.path) return
        endpoint = '/api/generate/retake'
        body = {
          ...body,
          video_uri: retakeVideo.path,
          start_time: parseTimecode(startTime),
          duration: parseTimecode(retakeDuration),
          prompt: prompt.trim(),
          model: mappedModel,
          resolution: parseResolution(resolution),
          mode: RETAKE_MODE_MAP[retakeMode] || retakeMode,
        }
        break
      case 'extend':
        if (!extendVideo?.path) return
        endpoint = '/api/generate/extend'
        body = {
          ...body,
          video_uri: extendVideo.path,
          prompt: prompt.trim(),
          model: mappedModel,
          mode: EXTEND_MODE_MAP[extendMode] || extendMode,
          duration: parseTimecode(extendDuration),
          context: EXTEND_CONTEXT_MAP[extendContext] ?? null,
        }
        break
      case 'video-hdr':
        if (!hdrVideo?.path) return
        endpoint = '/api/generate/video-hdr'
        body = { ...body, video_uri: hdrVideo.path }
        break
    }

    setGenerating(true)
    try {
      const r = await fetch(`${API}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        let detail = `Request failed (${r.status})`
        try {
          const errBody = await r.json()
          if (typeof errBody?.detail === 'string') {
            detail = errBody.detail
          } else if (Array.isArray(errBody?.detail)) {
            // FastAPI 422 validation errors: [{loc, msg, type}, ...]
            const msgs = errBody.detail.map((d: { msg?: string }) => d.msg).filter(Boolean)
            if (msgs.length) detail = msgs.join('; ')
          }
        } catch { /* non-JSON error body, keep default detail */ }
        const failed: Generation = {
          id: `local-${Date.now()}`,
          mode,
          status: 'failed',
          prompt: prompt.trim(),
          created_at: Date.now() / 1000,
          output_url: null,
          error: detail,
        }
        setGenerations(prev => [failed, ...prev])
        setSelectedGen(failed)
        setGenerating(false)
        return
      }
      const { id } = await r.json()
      if (!id) {
        setGenerating(false)
        return
      }
      const placeholder: Generation = {
        id,
        mode,
        status: 'generating',
        prompt: prompt.trim(),
        created_at: Date.now() / 1000,
        output_url: null,
        error: null,
      }
      setGenerations(prev => [placeholder, ...prev])
      pollGeneration(id, mode)
    } catch {
      setGenerating(false)
    }
  }, [
    generating, backend, apiKey, mode, prompt, model, duration, resolution, fps,
    cameraMotion, generateAudio, firstFrame, audioFile, imageFile,
    guidanceScale, retakeVideo, startTime, retakeDuration, retakeMode,
    extendVideo, extendMode, extendDuration, extendContext, hdrVideo, pollGeneration,
  ])

  const modeSupportsCloud = CLOUD_CAPABLE_MODES.includes(mode)
  const isLocalUnavailable = backend === 'local' && !LOCAL_MODES.includes(mode)
  const needsApiKey = modeSupportsCloud && backend === 'cloud' && !apiKey.trim()

  const canGenerate = (() => {
    if (generating || isLocalUnavailable || needsApiKey) return false
    switch (mode) {
      case 'text-to-image': return !!prompt.trim()
      case 'text-to-speech': return !!prompt.trim()
      case 'text-to-video': return !!prompt.trim()
      case 'image-to-video': return !!firstFrame?.path
      case 'audio-to-video': return !!audioFile?.path
      case 'retake': return !!retakeVideo?.path
      case 'extend': return !!extendVideo?.path
      case 'video-hdr': return !!hdrVideo?.path
      default: return false
    }
  })()

  return (
    <div className="gen-panel" style={style}>
      {/* ── Sidebar ── */}
      <nav className="gen-sidebar">
        {SIDEBAR_GROUPS.map(group => (
          <div className="gen-sidebar-group" key={group.title}>
            <div className="gen-sidebar-title">{group.title}</div>
            {group.modes.map(m => (
              <button
                key={m.key}
                className={`gen-sidebar-item ${mode === m.key ? 'active' : ''}`}
                onClick={() => setMode(m.key)}
              >
                <span className="gen-sidebar-icon">{m.icon}</span>
                <span className="gen-sidebar-label">{m.label}</span>
                {!LOCAL_MODES.includes(m.key) && <span className="gen-sidebar-cloud">Cloud</span>}
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* ── Form Side ── */}
      <div className="gen-form-side">
        <div className="gen-form-header">
          <h2 className="gen-form-title">{MODES.find(m => m.key === mode)?.label}</h2>
          <p className="gen-form-desc">{MODE_DESCRIPTIONS[mode]}</p>
        </div>

        <div className="gen-form-scroll">
          {/* Backend selector — hidden for modes with no cloud path at all
              (text-to-image/text-to-speech have no backend/api_key field). */}
          {modeSupportsCloud && (
            <>
              <div className="gen-backend">
                <div className="gen-backend-row">
                  <span className="field-label">Run on</span>
                  <div className="gen-toggle-group">
                    <button
                      className={`gen-toggle-btn ${backend === 'local' ? 'active' : ''}`}
                      onClick={() => setBackend('local')}
                    >
                      Local
                    </button>
                    <button
                      className={`gen-toggle-btn ${backend === 'cloud' ? 'active' : ''}`}
                      onClick={() => setBackend('cloud')}
                    >
                      Cloud API
                    </button>
                  </div>
                </div>
                {backend === 'cloud' && (
                  <div className="field">
                    <label className="field-label">API key</label>
                    <input
                      type="password"
                      className="input gen-mono"
                      value={apiKey}
                      onChange={e => setApiKey(e.target.value)}
                      placeholder="Paste your LTX Cloud API key"
                    />
                  </div>
                )}
              </div>

              <hr className="divider" />
            </>
          )}

          {isLocalUnavailable ? (
            <div className="empty-state">
              <span className="empty-state-icon"><LockIcon /></span>
              <div className="empty-state-title">Runs on LTX Cloud</div>
              <p className="empty-state-desc">
                {MODES.find(m => m.key === mode)?.label} isn't available locally yet — switch to the Cloud API to use it.
              </p>
              <button className="btn btn-secondary btn-sm" onClick={() => setBackend('cloud')}>
                Switch to Cloud
              </button>
            </div>
          ) : (
            <>
              {/* ── Mode-specific fields ── */}
              {mode === 'text-to-image' && (
                <>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe the image you want to create..." />
                  <Section title="Settings">
                    <div className="gen-row">
                      <FormField label="Model">
                        <SelectField value={imageModel} options={IMAGE_MODELS} onChange={setImageModel} />
                      </FormField>
                      <FormField label="Resolution">
                        <SelectField value={imageResolution} options={IMAGE_RESOLUTIONS} onChange={setImageResolution} />
                      </FormField>
                    </div>
                  </Section>
                </>
              )}

              {mode === 'text-to-speech' && (
                <>
                  <PromptField label="Text" value={prompt} onChange={setPrompt} placeholder="Type the text you want spoken..." />
                  <Section title="Voice">
                    <div className="gen-row">
                      <FormField label="Speaker">
                        <SelectField value={ttsSpeaker} options={TTS_SPEAKERS} onChange={setTtsSpeaker} />
                      </FormField>
                      <FormField label="Language">
                        <SelectField value={ttsLanguage} options={TTS_LANGUAGES} onChange={setTtsLanguage} />
                      </FormField>
                    </div>
                    <FormField label="Model size">
                      <SelectField value={ttsModel} options={TTS_MODELS} onChange={setTtsModel} />
                    </FormField>
                  </Section>
                  <MoreOptions>
                    <FormField label="Style instruction">
                      <input
                        type="text"
                        className="input"
                        value={ttsInstruct}
                        onChange={e => setTtsInstruct(e.target.value)}
                        placeholder="e.g. Speak in an excited tone"
                      />
                    </FormField>
                    <FormField label="Voice clone audio">
                      <DropZone
                        accept="audio/*"
                        file={ttsRefAudio}
                        onFile={handleFileSelect(setTtsRefAudio)}
                        onClear={() => setTtsRefAudio(null)}
                        placeholder="Drop a short voice sample to clone"
                      />
                    </FormField>
                  </MoreOptions>
                </>
              )}

              {mode === 'text-to-video' && (
                <>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe the video you want to create..." />
                  <Section title="Settings">
                    <FormField label="Model">
                      <SelectField value={model} options={MODELS} onChange={setModel} />
                    </FormField>
                    <div className="gen-row">
                      <FormField label="Duration">
                        <SelectField value={duration} options={DURATIONS} onChange={setDuration} />
                      </FormField>
                      <FormField label="Resolution">
                        <SelectField value={resolution} options={RESOLUTIONS} onChange={setResolution} />
                      </FormField>
                    </div>
                    <FormField label="Camera motion">
                      <SelectField value={cameraMotion} options={CAMERA_MOTIONS} onChange={setCameraMotion} />
                    </FormField>
                  </Section>
                  <MoreOptions>
                    <FormField label="Frame rate">
                      <SelectField value={fps} options={FPS_OPTIONS} onChange={setFps} />
                    </FormField>
                    <FormField label="Generate audio">
                      <div className="gen-toggle-group">
                        <button className={`gen-toggle-btn ${generateAudio ? 'active' : ''}`} onClick={() => setGenerateAudio(true)}>On</button>
                        <button className={`gen-toggle-btn ${!generateAudio ? 'active' : ''}`} onClick={() => setGenerateAudio(false)}>Off</button>
                      </div>
                    </FormField>
                  </MoreOptions>
                </>
              )}

              {mode === 'image-to-video' && (
                <>
                  <FormField label="First frame">
                    <DropZone
                      accept="image/*"
                      file={firstFrame}
                      onFile={handleFileSelect(setFirstFrame)}
                      onClear={() => setFirstFrame(null)}
                      placeholder="Drop an image here, or click to browse"
                    />
                  </FormField>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe how the image should move..." />
                  <Section title="Settings">
                    <FormField label="Model">
                      <SelectField value={model} options={MODELS} onChange={setModel} />
                    </FormField>
                    <div className="gen-row">
                      <FormField label="Duration">
                        <SelectField value={duration} options={DURATIONS} onChange={setDuration} />
                      </FormField>
                      <FormField label="Resolution">
                        <SelectField value={resolution} options={RESOLUTIONS} onChange={setResolution} />
                      </FormField>
                    </div>
                    <FormField label="Camera motion">
                      <SelectField value={cameraMotion} options={CAMERA_MOTIONS} onChange={setCameraMotion} />
                    </FormField>
                  </Section>
                  <MoreOptions>
                    <FormField label="Frame rate">
                      <SelectField value={fps} options={FPS_OPTIONS} onChange={setFps} />
                    </FormField>
                    <FormField label="Generate audio">
                      <div className="gen-toggle-group">
                        <button className={`gen-toggle-btn ${generateAudio ? 'active' : ''}`} onClick={() => setGenerateAudio(true)}>On</button>
                        <button className={`gen-toggle-btn ${!generateAudio ? 'active' : ''}`} onClick={() => setGenerateAudio(false)}>Off</button>
                      </div>
                    </FormField>
                  </MoreOptions>
                </>
              )}

              {mode === 'audio-to-video' && (
                <>
                  <FormField label="Audio">
                    <DropZone
                      accept="audio/*"
                      file={audioFile}
                      onFile={handleFileSelect(setAudioFile)}
                      onClear={() => setAudioFile(null)}
                      placeholder="Drop an audio file here, or click to browse"
                    />
                  </FormField>
                  <FormField label="Image (optional)">
                    <DropZone
                      accept="image/*"
                      file={imageFile}
                      onFile={handleFileSelect(setImageFile)}
                      onClear={() => setImageFile(null)}
                      placeholder="Drop an image here, or click to browse"
                    />
                  </FormField>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe the video (optional if an image is provided)..." />
                  <Section title="Settings">
                    <div className="gen-row">
                      <FormField label="Model">
                        <SelectField value={model} options={MODELS} onChange={setModel} />
                      </FormField>
                      <FormField label="Resolution">
                        <SelectField value={resolution} options={RESOLUTIONS} onChange={setResolution} />
                      </FormField>
                    </div>
                  </Section>
                  <MoreOptions>
                    <FormField label="Guidance scale">
                      <div className="gen-number-row">
                        <input
                          type="number"
                          className="input gen-input-number"
                          value={guidanceScale}
                          onChange={e => setGuidanceScale(Number(e.target.value))}
                          min={1}
                          max={200}
                        />
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => setGuidanceScale(75)}
                          disabled={guidanceScale === 75}
                        >
                          Reset to default
                        </button>
                      </div>
                    </FormField>
                  </MoreOptions>
                </>
              )}

              {mode === 'retake' && (
                <>
                  <FormField label="Video">
                    <DropZone
                      accept="video/*"
                      file={retakeVideo}
                      onFile={handleFileSelect(setRetakeVideo)}
                      onClear={() => setRetakeVideo(null)}
                      placeholder="Drop a video here, or click to browse"
                    />
                  </FormField>
                  <div className="gen-row">
                    <FormField label="Start time">
                      <input
                        type="text"
                        className="input gen-mono"
                        value={startTime}
                        onChange={e => setStartTime(e.target.value)}
                        placeholder="00:00.00"
                      />
                    </FormField>
                    <FormField label="Duration">
                      <input
                        type="text"
                        className="input gen-mono"
                        value={retakeDuration}
                        onChange={e => setRetakeDuration(e.target.value)}
                        placeholder="00:02.00"
                      />
                    </FormField>
                  </div>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe what the new section should look like..." />
                  <Section title="Settings">
                    <FormField label="Replace">
                      <SelectField value={retakeMode} options={RETAKE_MODES} onChange={setRetakeMode} />
                    </FormField>
                  </Section>
                  <MoreOptions>
                    <div className="gen-row">
                      <FormField label="Model">
                        <SelectField value={model} options={MODELS} onChange={setModel} />
                      </FormField>
                      <FormField label="Resolution">
                        <SelectField value={resolution} options={RESOLUTIONS} onChange={setResolution} />
                      </FormField>
                    </div>
                  </MoreOptions>
                </>
              )}

              {mode === 'extend' && (
                <>
                  <FormField label="Video">
                    <DropZone
                      accept="video/*"
                      file={extendVideo}
                      onFile={handleFileSelect(setExtendVideo)}
                      onClear={() => setExtendVideo(null)}
                      placeholder="Drop a video here, or click to browse"
                    />
                  </FormField>
                  <PromptField value={prompt} onChange={setPrompt} placeholder="Describe what should happen in the extended video..." />
                  <Section title="Settings">
                    <div className="gen-row">
                      <FormField label="Direction">
                        <SelectField value={extendMode} options={EXTEND_MODES} onChange={setExtendMode} />
                      </FormField>
                      <FormField label="Duration">
                        <input
                          type="text"
                          className="input gen-mono"
                          value={extendDuration}
                          onChange={e => setExtendDuration(e.target.value)}
                          placeholder="00:05.00"
                        />
                      </FormField>
                    </div>
                  </Section>
                  <MoreOptions>
                    <FormField label="Model">
                      <SelectField value={model} options={MODELS} onChange={setModel} />
                    </FormField>
                    <FormField label="Context">
                      <SelectField value={extendContext} options={EXTEND_CONTEXTS} onChange={setExtendContext} />
                    </FormField>
                  </MoreOptions>
                </>
              )}

              {mode === 'video-hdr' && (
                <FormField label="Video">
                  <DropZone
                    accept="video/*"
                    file={hdrVideo}
                    onFile={handleFileSelect(setHdrVideo)}
                    onClear={() => setHdrVideo(null)}
                    placeholder="Drop a video here, or click to browse"
                  />
                </FormField>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="gen-form-footer">
          <button className="btn btn-ghost" onClick={handleClear} disabled={generating}>
            Clear
          </button>
          <button
            className="btn btn-primary gen-generate"
            onClick={handleGenerate}
            disabled={!canGenerate}
          >
            {generating ? (
              <><span className="spinner gen-generate-spinner" /> Generating…</>
            ) : (
              <><SparkleIcon /> Generate</>
            )}
          </button>
        </div>
      </div>

      {/* ── Output Side ── */}
      <div className="gen-output-side">
        <div className="gen-output-header">
          <span className="section-title">Output</span>
          <div className="gen-output-actions">
            {selectedGen?.status === 'generating' && (
              <button className="btn btn-sm btn-secondary gen-stop" onClick={() => handleStop(selectedGen.id)}>
                <StopIcon /> Stop
              </button>
            )}
            {selectedGen?.status === 'done' && selectedGen.output_url && (
              <a href={selectedGen.output_url} download className="btn btn-sm btn-secondary gen-download">
                <DownloadIcon /> Download
              </a>
            )}
            {selectedGen && selectedGen.status !== 'generating' && (
              <button className="btn btn-sm btn-danger" onClick={() => handleDelete(selectedGen.id)}>
                <TrashIcon /> Delete
              </button>
            )}
          </div>
        </div>

        <div className="gen-output-area">
          {selectedGen ? (
            <div className="gen-output-player">
              {selectedGen.status === 'generating' && (
                <GeneratingStatus gen={selectedGen} />
              )}
              {selectedGen.status === 'done' && selectedGen.output_url && (
                selectedGen.output_url.endsWith('.png') || selectedGen.output_url.endsWith('.jpg') ? (
                  <img
                    src={selectedGen.output_url}
                    className="gen-output-video"
                    alt={selectedGen.prompt}
                  />
                ) : selectedGen.output_url.endsWith('.wav') || selectedGen.output_url.endsWith('.mp3') ? (
                  <div className="gen-output-audio-wrap">
                    <SpeechIcon />
                    <audio src={selectedGen.output_url} controls autoPlay className="gen-output-audio" />
                  </div>
                ) : (
                  <video
                    src={selectedGen.output_url}
                    className="gen-output-video"
                    controls
                    autoPlay
                    loop
                  />
                )
              )}
              {selectedGen.status === 'failed' && (
                <div className="gen-output-error">
                  <FailIcon />
                  <div className="gen-output-error-title">Something went wrong</div>
                  <span>{selectedGen.error || 'The generation failed — try again, or tweak your settings.'}</span>
                </div>
              )}
              {selectedGen.prompt && (
                <p className="gen-output-prompt">{selectedGen.prompt}</p>
              )}
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-state-icon"><VideoPlaceholderIcon /></span>
              <div className="empty-state-title">Nothing here yet</div>
              <p className="empty-state-desc">Pick a mode on the left, describe what you want, and your result will show up here.</p>
            </div>
          )}
        </div>

        {/* Gallery strip */}
        {generations.length > 0 && (
          <div className="gen-gallery-strip">
            <div className="gen-gallery-strip-header">
              <span className="section-title">Recent</span>
              <span className="badge badge-neutral">{generations.length}</span>
            </div>
            <div className="gen-gallery-strip-scroll">
              {generations.map(gen => (
                <div
                  key={gen.id}
                  className={`gen-gallery-thumb ${gen.status} ${selectedGen?.id === gen.id ? 'selected' : ''}`}
                  onClick={() => gen.status === 'done' && setSelectedGen(gen)}
                >
                  <div className="gen-gallery-thumb-media">
                    {gen.status === 'generating' && (
                      <div className="gen-gallery-thumb-loading">
                        <span className="spinner" />
                      </div>
                    )}
                    {gen.status === 'done' && gen.output_url && (
                      gen.output_url.endsWith('.png') || gen.output_url.endsWith('.jpg') ? (
                        <img src={gen.output_url} alt="" loading="lazy" />
                      ) : (
                        <video src={gen.output_url} muted playsInline
                          onMouseEnter={e => (e.target as HTMLVideoElement).play()}
                          onMouseLeave={e => { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0 }}
                        />
                      )
                    )}
                    {gen.status === 'failed' && (
                      <div className="gen-gallery-thumb-failed"><FailIcon /></div>
                    )}
                    <button
                      className="gen-gallery-thumb-delete"
                      onClick={e => { e.stopPropagation(); gen.status === 'generating' ? handleStop(gen.id) : handleDelete(gen.id) }}
                      title={gen.status === 'generating' ? 'Stop' : 'Delete'}
                    >
                      {gen.status === 'generating' ? <StopIcon /> : <TrashIcon />}
                    </button>
                  </div>
                  <span className="gen-gallery-thumb-label">
                    {gen.prompt ? gen.prompt.slice(0, 30) + (gen.prompt.length > 30 ? '...' : '') : gen.mode}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Generating Status with live timer + ETA ──────────────────────────────── */

function GeneratingStatus({ gen }: { gen: Generation }) {
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  const elapsed = Math.max(0, Math.floor(now / 1000 - gen.created_at))
  const progress = gen.progress || 0

  const noun = gen.mode === 'text-to-image' ? 'image'
    : gen.mode === 'text-to-speech' ? 'audio'
    : 'video'

  // Estimate remaining time based on progress percentage
  let etaText = ''
  if (progress > 5 && progress < 100) {
    const estimated = Math.round((elapsed / progress) * (100 - progress))
    etaText = estimated > 60
      ? `about ${Math.floor(estimated / 60)}m ${estimated % 60}s left`
      : `about ${estimated}s left`
  }

  const elapsedMin = Math.floor(elapsed / 60)
  const elapsedSec = elapsed % 60
  const elapsedText = elapsedMin > 0
    ? `${elapsedMin}m ${elapsedSec.toString().padStart(2, '0')}s`
    : `${elapsedSec}s`

  return (
    <div className="gen-output-loading">
      <span className="spinner gen-loading-spinner" />
      <div className="gen-loading-title">Generating your {noun}</div>
      <div className="gen-loading-sub">
        {etaText ? `Almost done — ${etaText}` : 'Hang tight — this can take a little while'}
      </div>
      {progress > 0 && (
        <div className="gen-progress-bar">
          <div
            className="gen-progress-fill"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      <div className="gen-timer-row">
        {progress > 0 && <span className="gen-progress-pct">{progress}%</span>}
        <span className="gen-timer-elapsed">{elapsedText} elapsed</span>
      </div>
      {gen.progress_step && (
        <div className="gen-loading-step">{gen.progress_step}</div>
      )}
    </div>
  )
}

/* ── Sub-components ────────────────────────────────────────────────────────── */

function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="gen-section">
      {title && <div className="section-title">{title}</div>}
      {children}
    </section>
  )
}

function MoreOptions({ children }: { children: React.ReactNode }) {
  return (
    <details className="gen-more">
      <summary className="gen-more-summary">
        <span className="gen-more-chevron"><ChevronIcon /></span>
        More options
      </summary>
      <div className="gen-more-body">{children}</div>
    </details>
  )
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
    </div>
  )
}

function PromptField({
  label = 'Prompt',
  value,
  onChange,
  placeholder,
}: {
  label?: string
  value: string
  onChange: (v: string) => void
  placeholder: string
}) {
  return (
    <div className="field">
      <div className="gen-field-label-row">
        <label className="field-label">{label}</label>
        <span className="gen-char-count">{value.length} / 5000</span>
      </div>
      <textarea
        className="textarea gen-prompt-textarea"
        value={value}
        onChange={e => {
          if (e.target.value.length <= 5000) onChange(e.target.value)
        }}
        placeholder={placeholder}
        rows={5}
      />
    </div>
  )
}

function SelectField({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <select
      className="select"
      value={value}
      onChange={e => onChange(e.target.value)}
    >
      {options.map(opt => (
        <option key={opt} value={opt}>{opt}</option>
      ))}
    </select>
  )
}

function DropZone({
  accept,
  file,
  onFile,
  onClear,
  placeholder,
}: {
  accept: string
  file: FileUpload | null
  onFile: (f: File) => void
  onClear: () => void
  placeholder: string
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const dropped = e.dataTransfer.files[0]
      if (dropped) onFile(dropped)
    },
    [onFile]
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setDragging(false)
  }, [])

  if (file) {
    const isImage = file.file.type.startsWith('image/')
    const isVideo = file.file.type.startsWith('video/')
    return (
      <div className="gen-dropzone-preview">
        {isImage && <img src={file.previewUrl} alt="" className="gen-dropzone-preview-img" />}
        {isVideo && <video src={file.previewUrl} className="gen-dropzone-preview-img" muted playsInline />}
        {!isImage && !isVideo && (
          <div className="gen-dropzone-preview-file">
            <AudioIcon />
            <span>{file.file.name}</span>
          </div>
        )}
        <div className="gen-dropzone-preview-info">
          <span className="gen-dropzone-filename">{file.file.name}</span>
          {file.uploading && <span className="gen-dropzone-uploading"><span className="spinner" /> Uploading…</span>}
          {file.path && <span className="badge badge-success">Ready</span>}
          <button className="btn btn-danger btn-sm" onClick={onClear}>Remove</button>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`gen-dropzone ${dragging ? 'dragging' : ''}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="gen-dropzone-input"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
        }}
      />
      <UploadIcon />
      <span>{placeholder}</span>
    </div>
  )
}

/* ── SVG Icons ─────────────────────────────────────────────────────────────── */

function ChevronIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4.5 2.5L8 6l-3.5 3.5" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
      <rect x="3" y="3" width="10" height="10" rx="1" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 4h12M5 4V2.5a.5.5 0 01.5-.5h5a.5.5 0 01.5.5V4M6.5 7v4M9.5 7v4M3.5 4l.5 9.5a1 1 0 001 .5h6a1 1 0 001-.5L12.5 4" />
    </svg>
  )
}

function SpeechIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 2v14" />
      <path d="M5 5v8" />
      <path d="M1 8v2" />
      <path d="M13 5v8" />
      <path d="M17 8v2" />
    </svg>
  )
}

function ImageGenIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="14" height="14" rx="2" />
      <path d="M9 6v6M6 9h6" />
    </svg>
  )
}

function TextIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 5h12M3 9h8M3 13h10" />
    </svg>
  )
}

function ImageIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="14" height="14" rx="2" />
      <circle cx="6" cy="6" r="1.5" />
      <path d="M16 12l-4-4L4 16" />
    </svg>
  )
}

function AudioIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7v4h3l4 3V4L6 7H3z" />
      <path d="M13 6.5a3.5 3.5 0 010 5" />
      <path d="M15 4.5a6 6 0 010 9" />
    </svg>
  )
}

function RetakeIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9a6 6 0 1011.5-2.5" />
      <path d="M14 2v5h-5" />
    </svg>
  )
}

function ExtendIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9h12" />
      <path d="M12 5l4 4-4 4" />
      <path d="M2 5l-1 4 1 4" />
    </svg>
  )
}

function HdrIcon() {
  return (
    <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="14" height="12" rx="2" />
      <path d="M6 7v4M6 9h2M8 7v4" />
      <path d="M11 7v4h1a2 2 0 000-4h-1z" />
    </svg>
  )
}

function SparkleIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
      <path d="M8 0l1.5 4.5L14 6l-4.5 1.5L8 12l-1.5-4.5L2 6l4.5-1.5z" />
    </svg>
  )
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2v8m0 0l-3-3m3 3l3-3M3 12h10" />
    </svg>
  )
}

function UploadIcon() {
  return (
    <svg viewBox="0 0 18 18" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 12V4m0 0L6 7m3-3l3 3" />
      <path d="M3 13v1a2 2 0 002 2h8a2 2 0 002-2v-1" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg viewBox="0 0 18 18" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="8" width="10" height="8" rx="2" />
      <path d="M6 8V5a3 3 0 016 0v3" />
    </svg>
  )
}

function FailIcon() {
  return (
    <svg viewBox="0 0 16 16" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="8" cy="8" r="6" />
      <path d="M6 6l4 4m0-4l-4 4" />
    </svg>
  )
}

function VideoPlaceholderIcon() {
  return (
    <svg viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" opacity="0.3">
      <rect x="4" y="10" width="32" height="28" rx="4" />
      <path d="M36 20l8-5v18l-8-5" />
      <path d="M16 20v8l6-4z" fill="currentColor" opacity="0.4" />
    </svg>
  )
}
