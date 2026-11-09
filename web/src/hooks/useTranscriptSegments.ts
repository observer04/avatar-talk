import { useEffect, useState } from 'react'
import type { Room, TextStreamReader } from 'livekit-client'

/** LiveKit Agents transcription topic and stream attributes. */
const TOPIC = 'lk.transcription'
const SEGMENT_ID = 'lk.segment_id'
const FINAL = 'lk.transcription_final'

export interface TranscriptSegment {
  id: string
  /** Participant the text is attributed to (the user for STT, the agent for its speech). */
  identity: string
  text: string
  /** False while STT is still revising this segment. */
  final: boolean
}

/**
 * Collect `lk.transcription` text streams, in arrival order.
 *
 * Same semantics as `useTranscriptions` from @livekit/components-react: one
 * entry per `lk.segment_id`, and a new stream for an existing segment replaces
 * its text (STT revisions). It's reimplemented here because that hook doesn't
 * handle reader errors: hanging up while the agent is mid-sentence aborts the
 * open stream and surfaces as an uncaught DataStreamError.
 */
export function useTranscriptSegments(room: Room): TranscriptSegment[] {
  const [segments, setSegments] = useState<TranscriptSegment[]>([])

  useEffect(() => {
    const list: TranscriptSegment[] = []
    let scheduled = false
    const publish = () => {
      if (scheduled) return
      scheduled = true
      queueMicrotask(() => {
        scheduled = false
        setSegments(list.map((s) => ({ ...s })))
      })
    }

    const read = async (reader: TextStreamReader, identity: string) => {
      const attrs = reader.info.attributes ?? {}
      const id = attrs[SEGMENT_ID] || reader.info.id
      let seg = list.find((s) => s.id === id)
      if (!seg) {
        seg = { id, identity, text: '', final: false }
        list.push(seg)
      }
      seg.final = attrs[FINAL] !== 'false'
      let text = ''
      try {
        for await (const chunk of reader) {
          text += chunk
          seg.text = text
          publish()
        }
      } catch {
        // Stream aborted (e.g. the room disconnected mid-sentence); keep what arrived.
      }
      publish()
    }

    try {
      room.registerTextStreamHandler(TOPIC, (reader, participant) => void read(reader, participant.identity))
    } catch (err) {
      console.warn(`${TOPIC} handler already registered`, err)
      return
    }
    return () => room.unregisterTextStreamHandler(TOPIC)
  }, [room])

  return segments
}
