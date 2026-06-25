// ─── Shared Types ────────────────────────────────────────────────────────────

export type SegmentStatus = 'done' | 'pending' | 'generating' | 'failed'

export interface Cue {
  timestamp_hint: string
  description: string
}

export interface Segment {
  segment_id: string
  section_title: string
  narration_text: string
  cues: Cue[]
  status: SegmentStatus
  image_url: string | null
  clip_url: string | null
  scene_url: string | null
  audio_url: string | null
  duration_seconds: number
}

export interface RunSummary {
  id: string
  title: string
  segment_count: number
  has_final_video: boolean
  final_video_url: string | null
}

export interface RunDetail {
  id: string
  title: string
  final_video_url: string | null
  total_duration_seconds: number
  segments: Segment[]
}

// ─── Fetch Helpers ────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(path)
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
