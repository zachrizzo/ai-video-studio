// ─── Shared Types ────────────────────────────────────────────────────────────

export type SegmentStatus =
  | 'approved'
  | 'done'
  | 'needs_review'
  | 'qa_failed'
  | 'pending'
  | 'generating'
  | 'failed'

export interface QACheck {
  id: string
  severity: 'error' | 'warning' | 'info'
  message: string
  segment_id?: string
  path?: string
  details?: Record<string, unknown>
}

export interface SegmentQA {
  status: 'passed' | 'warning' | 'failed'
  checks: QACheck[]
}

export interface RunQA {
  status: 'passed' | 'warning' | 'failed'
  summary: {
    errors: number
    warnings: number
    info: number
  }
  checks: QACheck[]
  segments: Record<string, SegmentQA>
}

export type RunProductionState = 'idle' | 'running' | 'done' | 'failed' | 'stalled' | 'stopped'

export interface RunProductionStatus {
  run_id: string
  mode?: string
  force_video?: boolean
  segment_ids?: string
  status: RunProductionState
  step: string | null
  step_label: string
  progress: number
  total_steps: number
  started_at: number | null
  finished_at: number | null
  updated_at: number
  error: string | null
  logs: string[]
  final_video_url?: string | null
}

export interface Cue {
  timestamp_hint: string
  description: string
}

export interface StoryboardFrame {
  frame_id: string
  beat_id: string
  description: string | null
  shot_type: string | null
  composition: string | null
  action: string | null
  camera_motion: string | null
  transition: string | null
  duration_seconds: number | null
  continuity_notes: string[]
  asset_notes: string[]
}

export interface Segment {
  segment_id: string
  section_title: string
  narration_text: string
  cues: Cue[]
  status: SegmentStatus
  image_url: string | null
  image_urls?: string[]
  clip_url: string | null
  clip_urls?: string[]
  scene_url: string | null
  audio_url: string | null
  duration_seconds: number
  visual_count?: number
  storyboard?: StoryboardFrame[]
  qa?: SegmentQA | null
}

export interface RunSummary {
  id: string
  title: string
  segment_count: number
  has_final_video: boolean
  final_video_url: string | null
  qa_status?: 'passed' | 'warning' | 'failed' | null
  production?: RunProductionStatus | null
  project_id?: string
}

export interface ProjectConversation {
  id: string
  title: string
  claude_session_id?: string | null
  created_at: number
  updated_at?: number
}

export interface Project {
  id: string
  name: string
  created_at: number
  run_ids: string[]
  conversations: ProjectConversation[]
}

export interface Preset {
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
  // Generation-quality overrides — undefined/null means "use the pipeline's
  // own default", never a value this UI invents on its own.
  image_model?: string | null
  image_steps?: number | null
  image_quantize?: number | null
  ltx_steps?: number | null
  ltx_resolution?: string | null
  ltx_clip_seconds?: number | null
  ltx_cfg_scale?: number | null
  ltx_stg_scale?: number | null
  ltx_prefer_extend?: boolean | null
  video_fallback_to_kenburns?: boolean | null
  kenburns_zoom?: number | null
  qwen_model_size?: string | null
}

export interface RunDetail {
  id: string
  title: string
  final_video_url: string | null
  total_duration_seconds: number
  qa?: RunQA | null
  production?: RunProductionStatus | null
  segments: Segment[]
}

// ─── Fetch Helpers ────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    throw new Error(`API ${path} → ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const data = await apiFetch<{ runs: RunSummary[] }>('/api/runs')
  return data.runs
}

export async function fetchRun(runId: string): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/api/runs/${runId}`)
}

export async function fetchProjects(): Promise<Project[]> {
  const data = await apiFetch<{ projects: Project[] }>('/api/projects')
  return data.projects
}

export async function fetchPresets(): Promise<Preset[]> {
  const data = await apiFetch<{ presets: Preset[] }>('/api/presets')
  return data.presets ?? []
}

export interface StylePackDetail {
  id: string
  name: string
  description: string
  palette: Record<string, string>
  type: Record<string, string>
  motion: Record<string, number | string>
  texture: Record<string, number | string>
  flux_prefix: string
  flux_suffix: string
  fonts: string[]
}

export async function fetchStylePackDetail(id: string): Promise<StylePackDetail> {
  return apiFetch<StylePackDetail>(`/api/style_packs/${id}`)
}

export interface VoiceboxProfile {
  id: string
  name: string
  default_engine: string | null
}

export interface VoiceboxProfilesResponse {
  profiles: VoiceboxProfile[]
  available: boolean
  message: string | null
}

/** Voicebox being unreachable is a normal, expected state (app not running),
 * not an error — this never throws; check `available` instead. */
export async function fetchVoiceboxProfiles(): Promise<VoiceboxProfilesResponse> {
  return apiFetch<VoiceboxProfilesResponse>('/api/voicebox/profiles')
}

export async function createProject(name: string): Promise<Project> {
  return apiFetch<Project>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export async function renameProject(projectId: string, name: string): Promise<void> {
  await apiFetch(`/api/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export async function deleteProject(projectId: string): Promise<void> {
  await apiFetch(`/api/projects/${projectId}`, { method: 'DELETE' })
}

export async function assignRunToProject(projectId: string, runId: string): Promise<void> {
  await apiFetch(`/api/projects/${projectId}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  })
}

export async function upsertProjectConversation(
  projectId: string,
  conversation: { id: string; title?: string; claude_session_id?: string | null },
): Promise<ProjectConversation> {
  return apiFetch<ProjectConversation>(`/api/projects/${projectId}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(conversation),
  })
}

export interface ServerToolActivity {
  name: string
  summary: string
  status: 'done' | 'failed'
}

export interface ServerChatMessage {
  role: 'user' | 'assistant' | 'error'
  text: string
  tools: ServerToolActivity[]
}

export async function fetchConversationMessages(
  conversationId: string,
): Promise<ServerChatMessage[]> {
  const data = await apiFetch<{ messages: ServerChatMessage[] }>(
    `/api/conversations/${conversationId}/messages`,
  )
  return data.messages
}

export async function deleteProjectConversation(
  projectId: string,
  conversationId: string,
): Promise<void> {
  await apiFetch(`/api/projects/${projectId}/conversations/${conversationId}`, {
    method: 'DELETE',
  })
}

export async function fetchRunProduction(runId: string): Promise<RunProductionStatus> {
  return apiFetch<RunProductionStatus>(`/api/runs/${runId}/production`)
}

export interface StartRunProductionOptions {
  mode?: 'full' | 'videos' | 'clips'
  force_video?: boolean
  segment_ids?: string
  speed?: number
}

export async function startRunProduction(
  runId: string,
  options?: StartRunProductionOptions,
): Promise<RunProductionStatus> {
  return apiFetch<RunProductionStatus>(`/api/runs/${runId}/produce`, {
    method: 'POST',
    headers: options ? { 'Content-Type': 'application/json' } : undefined,
    body: options ? JSON.stringify(options) : undefined,
  })
}

export async function stopRunProduction(runId: string): Promise<RunProductionStatus> {
  return apiFetch<RunProductionStatus>(`/api/runs/${runId}/produce/stop`, {
    method: 'POST',
  })
}
