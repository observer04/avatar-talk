import { useEffect, useRef, useState } from 'react'
import {
  useConnectionState,
  useParticipants,
  useTracks,
  useVoiceAssistant,
} from '@livekit/components-react'
import { RemoteVideoTrack, Track } from 'livekit-client'
import type { SessionStart } from '../../api/types'
import { cueLog, kindName, pickAvatarVideo } from './tracks'

interface VideoStats {
  sid: string
  fps: number
  width?: number
  height?: number
  codec?: string
  dropped: number
  decoder?: string
}

/** `?debug=1`: agent state, participants and avatar-video receive stats. */
export function DebugOverlay({ session, mode }: { session: SessionStart | null; mode: string }) {
  const connection = useConnectionState()
  const assistant = useVoiceAssistant()
  const participants = useParticipants()
  const video = pickAvatarVideo(useTracks([Track.Source.Camera], { onlySubscribed: true }))
  const track = video?.publication.track
  const [allStats, setStats] = useState<VideoStats | null>(null)
  const stats = allStats && allStats.sid === track?.sid ? allStats : null
  const [open, setOpen] = useState(true)
  const prev = useRef<{ frames: number; t: number } | null>(null)
  const [, setTick] = useState(0)

  // Re-render once a second so non-reactive values (cue log) stay current.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    prev.current = null
    if (!(track instanceof RemoteVideoTrack)) return
    const sid = track.sid ?? ''
    const timer = setInterval(() => {
      void track.getReceiverStats().then((s) => {
        if (!s) return
        const now = s.timestamp
        const p = prev.current
        const fps = p && now > p.t ? ((s.framesDecoded - p.frames) * 1000) / (now - p.t) : 0
        prev.current = { frames: s.framesDecoded, t: now }
        setStats({
          sid,
          fps,
          width: s.frameWidth,
          height: s.frameHeight,
          codec: s.mimeType?.replace(/^video\//, ''),
          dropped: s.framesDropped,
          decoder: s.decoderImplementation,
        })
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [track])

  return (
    <div className={`debug-overlay ${open ? '' : 'collapsed'}`} data-testid="debug-overlay">
      <button className="debug-toggle" onClick={() => setOpen((o) => !o)}>
        debug {open ? '▾' : '▸'}
      </button>
      {open && (
        <dl>
          <dt>conn</dt>
          <dd>{connection}</dd>
          <dt>agent</dt>
          <dd data-testid="debug-agent-state">{assistant.state}</dd>
          <dt>mode</dt>
          <dd>{mode}</dd>
          <dt>room</dt>
          <dd className="mono">{session?.room ?? '—'}</dd>
          <dt>session</dt>
          <dd className="mono">{session?.session_id ?? '—'}</dd>
          <dt>video</dt>
          <dd data-testid="debug-video">
            {video
              ? stats
                ? `${video.participant.identity} ${stats.width ?? '?'}×${stats.height ?? '?'} @ ${stats.fps.toFixed(1)} fps${
                    stats.codec ? ` ${stats.codec}` : ''
                  }${stats.dropped ? `, ${stats.dropped} dropped` : ''}`
                : `${video.participant.identity} (measuring…)`
              : 'none'}
          </dd>
          <dt>cues</dt>
          <dd data-testid="debug-cues">{cueLog.count ? `${cueLog.count}, last ${cueLog.last}` : '0'}</dd>
          <dt>people</dt>
          <dd>
            <ul>
              {participants.map((p) => (
                <li key={p.identity}>
                  <span className="mono">{p.identity}</span> <em>{p.isLocal ? 'local' : kindName(p)}</em>
                  {[...p.trackPublications.values()].map((pub) => (
                    <span key={pub.trackSid} className={`tag ${pub.isMuted ? 'off' : ''}`}>
                      {pub.source}
                      {pub.isSubscribed || p.isLocal ? '' : ' (unsub)'}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          </dd>
        </dl>
      )}
    </div>
  )
}
