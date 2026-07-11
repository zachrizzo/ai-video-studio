export function formatDuration(s: number): string {
  if (!s) return ''
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

// Parses a "mm:ss.ss" timecode (as used by the Generate tab's retake/extend
// time fields) into float seconds for the backend's float time fields.
// Falls back to 0 for anything that doesn't parse as mm:ss.
export function parseTimecode(s: string): number {
  const match = /^(\d+):(\d+(?:\.\d+)?)$/.exec(s.trim())
  if (!match) return 0
  const minutes = parseInt(match[1], 10)
  const seconds = parseFloat(match[2])
  return minutes * 60 + seconds
}
