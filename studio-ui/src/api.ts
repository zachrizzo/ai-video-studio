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
