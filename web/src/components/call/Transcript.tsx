import { useEffect, useMemo, useRef } from 'react'
import { useRoomContext } from '@livekit/components-react'
import { useTranscriptSegments, type TranscriptSegment } from '../../hooks/useTranscriptSegments'

interface Bubble {
  key: string
  role: 'user' | 'agent'
  text: string
  interim: boolean
}

/**
 * LiveKit Agents publishes transcripts as `lk.transcription` text streams: the
 * user's STT (from the agent, attributed to the user) and the agent's speech.
 * Consecutive segments from the same speaker merge into one bubble.
 */
function toBubbles(segments: TranscriptSegment[], localIdentity: string): Bubble[] {
  const out: Bubble[] = []
  for (const s of segments) {
    const text = s.text.trim()
    if (!text) continue
    const role = s.identity === localIdentity ? 'user' : 'agent'
    const interim = !s.final
    const last = out[out.length - 1]
    if (last && last.role === role) {
      last.text = `${last.text} ${text}`
      last.interim = interim
    } else {
      out.push({ key: s.id, role, text, interim })
    }
  }
  return out
}

export function Transcript({ agentName }: { agentName: string }) {
  const room = useRoomContext()
  const segments = useTranscriptSegments(room)
  const localIdentity = room.localParticipant.identity
  const bubbles = useMemo(() => toBubbles(segments, localIdentity), [segments, localIdentity])

  const listRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useEffect(() => {
    const el = listRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [bubbles])

  return (
    <aside className="transcript" aria-label="Transcript">
      <h2>Transcript</h2>
      <div
        ref={listRef}
        className="transcript-list"
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
      >
        {bubbles.length === 0 && <p className="muted transcript-empty">Say hello. The conversation will appear here.</p>}
        {bubbles.map((b) => (
          <div key={b.key} className={`bubble ${b.role}${b.interim ? ' interim' : ''}`}>
            <span className="who">{b.role === 'user' ? 'You' : agentName}</span>
            <p>{b.text}</p>
          </div>
        ))}
      </div>
    </aside>
  )
}
