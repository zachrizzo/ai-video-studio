// Voice options exposed by the local Qwen3-TTS backend; shared by the
// preset editor and the standalone text-to-speech generator.
export const TTS_SPEAKERS = ['serena', 'vivian', 'aiden', 'dylan', 'eric', 'ono_anna', 'ryan', 'sohee', 'uncle_fu']
export const TTS_LANGUAGES = ['auto', 'english', 'chinese', 'japanese', 'korean', 'german', 'french', 'russian', 'portuguese', 'spanish', 'italian']

// Generate tab (studio-ui/src/components/GeneratePanel.tsx): display-label ->
// backend contract value maps. The backend (src/studio/server.py request
// models, src/studio/ltx_api.py) only accepts these ids, not the display labels.

// LTX model display name -> backend model id (src/studio/ltx_api.py defaults
// to "ltx-2-3-pro"; "-fast" is the lower-latency sibling).
export const MODEL_MAP: Record<string, string> = {
  'LTX-2.3 Pro': 'ltx-2-3-pro',
  'LTX-2.3 Fast': 'ltx-2-3-fast',
}

// Retake "Replace" mode -> backend `mode` value. RetakeRequest.mode is an
// unconstrained str|None (src/studio/server.py) — these snake_case ids are a
// judgment call (no existing backend enum to match against).
export const RETAKE_MODE_MAP: Record<string, string> = {
  'Replace audio and video': 'replace_audio_and_video',
  'Replace video only': 'replace_video_only',
  'Replace audio only': 'replace_audio_only',
}

// Extend direction -> backend `mode` value (matches LTXClient.extend's own
// default of "from_end").
export const EXTEND_MODE_MAP: Record<string, string> = {
  'From the end': 'from_end',
  'From the start': 'from_start',
}

// Extend "Context" -> backend `context` (float seconds, or null to let the
// server pick). 'Full' has no dedicated backend flag, so it maps to null
// (same as 'Auto') — a judgment call documented here since there's nothing
// in ExtendRequest to distinguish the two.
export const EXTEND_CONTEXT_MAP: Record<string, number | null> = {
  'Auto': null,
  'Full': null,
  'Last 2 seconds': 2,
}

// Backend `type` (canonical, written by src/studio/generate.py) -> frontend
// GenMode. The chat agent's classic generate_image/generate_video tools
// still write type "image"/"video"; every other type is already a GenMode.
export const GEN_TYPE_TO_MODE: Record<string, string> = {
  image: 'text-to-image',
  video: 'text-to-video',
}
