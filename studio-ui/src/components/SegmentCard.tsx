import { useRef } from 'react'
import type { Segment, SegmentStatus } from '../api'

// ── Status Badge ─────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<SegmentStatus, string> = {
  done: 'done',
  pending: 'pending',
  generating: 'rendering',
  failed: 'failed',
}

function StatusBadge({ status }: { status: SegmentStatus }) {
  return (
    <span className={`status-badge ${status}`}>
      <span className="status-dot" />
      {STATUS_LABELS[status]}
    </span>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDuration(s: number): string {
  if (!s) return ''
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function zeroPad(n: number): string {
  return String(n).padStart(2, '0')
}

// ── AudioButton ────────────────────────────────────────────────────────────────

function AudioButton({ src }: { src: string }) {
  const audioRef = useRef<HTMLAudioElement>(null)

  function toggle() {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      audio.play().catch(() => {/* ignore */})
    } else {
      audio.pause()
    }
  }

  return (
    <>
      <audio ref={audioRef} src={src} preload="none" />
      <button className="audio-btn" onClick={toggle} title="Play narration audio">
        <svg width="11" height="12" viewBox="0 0 11 12" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M1 1.5L10 6L1 10.5V1.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
        </svg>
        narration.mp3
      </button>
    </>
  )
}

// ── SegmentCard ────────────────────────────────────────────────────────────────

interface SegmentCardProps {
  segment: Segment
  index: number
}

export function SegmentCard({ segment, index }: SegmentCardProps) {
  const {
    section_title,
    narration_text,
    cues,
    status,
    image_url,
    clip_url,
    scene_url,
    audio_url,
    duration_seconds,
  } = segment

  const videoSrc = clip_url || scene_url || null

  return (
    <div className="segment-card">
      {/* Header */}
      <div className="segment-card-header">
        <span className="segment-index">SEG {zeroPad(index + 1)}</span>
        <span className="segment-title" title={section_title}>· {section_title}</span>
        <StatusBadge status={status} />
        {duration_seconds > 0 && (
          <span className="segment-duration">{formatDuration(duration_seconds)}</span>
        )}
      </div>

      {/* Body — 3 columns */}
      <div className="segment-body">
        {/* Col 1: Script */}
        <div className="seg-col">
          <span className="seg-col-label">Script</span>
          <p className="narration-text">{narration_text}</p>
          {cues.length > 0 && (
            <div className="cues-list">
              {cues.map((cue, i) => (
                <div className="cue-item" key={i}>
                  <span className="cue-timestamp">{cue.timestamp_hint}</span>
                  <span className="cue-desc">{cue.description}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Col 2: Image */}
        <div className="seg-col">
          <span className="seg-col-label">Image</span>
          {image_url ? (
            <img
              className="seg-image"
              src={image_url}
              alt={`Frame for ${section_title}`}
              loading="lazy"
            />
          ) : (
            <div className="media-placeholder">no image yet</div>
          )}
        </div>

        {/* Col 3: Clip */}
        <div className="seg-col">
          <span className="seg-col-label">Clip</span>
          {videoSrc ? (
            <video
              className="seg-video"
              src={videoSrc}
              controls
              preload="metadata"
              title={section_title}
            />
          ) : (
            <div className="media-placeholder">no clip yet</div>
          )}
          {audio_url && <AudioButton src={audio_url} />}
        </div>
      </div>
    </div>
  )
}
