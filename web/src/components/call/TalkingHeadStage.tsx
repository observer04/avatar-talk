// Loaded with React.lazy so photoreal / voice calls never download Three.js.
import { useEffect, useRef, useState } from 'react'
import { useRoomContext } from '@livekit/components-react'
import type { TextStreamReader } from 'livekit-client'
import type { AvatarState } from '../../avatar/AvatarRenderer'
import { TalkingHeadRenderer } from '../../avatar/TalkingHeadRenderer'
import { AVATAR_CUE_TOPIC, cueLog } from './tracks'

interface Props {
  /** The agent's remote audio track. TalkingHead's Web Audio graph is its only playback path. */
  audioTrack: MediaStreamTrack | undefined
  speaking: boolean
  onReady(): void
  onError(message: string): void
}

function parseCue(text: string): AvatarState | null {
  try {
    const v: unknown = JSON.parse(text)
    if (!v || typeof v !== 'object') return null
    const { mood, gesture } = v as Record<string, unknown>
    return {
      mood: typeof mood === 'string' ? mood : undefined,
      gesture: typeof gesture === 'string' ? gesture : undefined,
    }
  } catch {
    return null
  }
}

export default function TalkingHeadStage({ audioTrack, speaking, onReady, onError }: Props) {
  const room = useRoomContext()
  const elRef = useRef<HTMLDivElement>(null)
  const [renderer, setRenderer] = useState<TalkingHeadRenderer | null>(null)
  const [audioLocked, setAudioLocked] = useState(false)
  const rendererRef = useRef<TalkingHeadRenderer | null>(null)
  /** Mood cue that arrived before the avatar finished loading. */
  const pendingMood = useRef<string | undefined>(undefined)
  const callbacks = useRef({ onReady, onError })
  useEffect(() => {
    callbacks.current = { onReady, onError }
  })

  // Build the 3D head once.
  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const r = new TalkingHeadRenderer()
    let cancelled = false
    r.mount(el)
      .then(() => {
        if (cancelled) return
        if (pendingMood.current) r.applyState({ mood: pendingMood.current })
        rendererRef.current = r
        setRenderer(r)
        callbacks.current.onReady()
      })
      .catch((err: unknown) => {
        if (!cancelled) callbacks.current.onError(`3D avatar failed to load: ${err instanceof Error ? err.message : String(err)}`)
      })
    return () => {
      cancelled = true
      rendererRef.current = null
      setRenderer(null)
      r.dispose()
      el.replaceChildren()
    }
  }, [])

  // The AudioContext is created after the user's click, so it normally starts
  // running; if the browser still blocks it, offer a tap-to-unlock button.
  useEffect(() => {
    const ctx = renderer?.audioContext
    if (!ctx) return
    const sync = () => setAudioLocked(ctx.state !== 'running')
    void ctx.resume().catch(() => {})
    sync()
    ctx.addEventListener('statechange', sync)
    return () => ctx.removeEventListener('statechange', sync)
  }, [renderer])

  useEffect(() => {
    if (renderer && audioTrack) renderer.attachBotAudio(audioTrack)
  }, [renderer, audioTrack])

  useEffect(() => {
    renderer?.setSpeaking(speaking)
  }, [renderer, speaking])

  // Body-language cues from the agent: text stream on `avatar.cue`.
  useEffect(() => {
    const handler = async (reader: TextStreamReader) => {
      let text: string
      try {
        text = await reader.readAll()
      } catch {
        return // stream aborted (room disconnected)
      }
      const cue = parseCue(text)
      if (!cue) return
      cueLog.count++
      cueLog.last = text
      const r = rendererRef.current
      if (r) r.applyState(cue)
      else if (cue.mood) pendingMood.current = cue.mood
    }
    try {
      room.registerTextStreamHandler(AVATAR_CUE_TOPIC, (reader) => void handler(reader))
    } catch (err) {
      console.warn('avatar.cue handler already registered', err)
      return
    }
    return () => room.unregisterTextStreamHandler(AVATAR_CUE_TOPIC)
  }, [room])

  return (
    <div className="talkinghead-stage">
      <div ref={elRef} className="talkinghead-canvas" />
      {audioLocked && (
        <button className="btn primary unlock-audio" onClick={() => void renderer?.unlockAudio()}>
          Tap to enable audio
        </button>
      )}
    </div>
  )
}
