import { useCallback, useEffect, useState } from 'react'
import { RoomContext } from '@livekit/components-react'
import { DisconnectReason, Room, RoomEvent } from 'livekit-client'
import { api, sessionErrorMessage } from '../../api/client'
import { BACKEND_LABELS, type Persona, type SessionStart } from '../../api/types'
import { PersonaPortrait } from '../PersonaPortrait'
import { ArrowLeftIcon } from '../icons'
import { AvatarStage, type CallPhase } from './AvatarStage'
import { CallControls, ConnectionPill } from './CallControls'
import { Transcript } from './Transcript'

interface Props {
  persona: Persona
  debug: boolean
  onExit(): void
  /** Start a fresh call with the same persona (remounts with a new Room). */
  onRestart(): void
}

function createRoom(): Room {
  return new Room({
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  })
}

/** One call: POST /v1/sessions → join the LiveKit room with the mic on. */
export default function CallScreen({ persona: initialPersona, debug, onExit, onRestart }: Props) {
  const [room] = useState(createRoom)
  const [phase, setPhase] = useState<CallPhase>('starting')
  const [session, setSession] = useState<SessionStart | null>(null)
  const [fatal, setFatal] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const persona = session?.persona ?? initialPersona

  useEffect(() => {
    let cancelled = false
    const onDisconnected = (reason?: DisconnectReason) => {
      if (cancelled) return
      setPhase('ended')
      if (reason !== undefined && reason !== DisconnectReason.CLIENT_INITIATED) {
        const name: unknown = DisconnectReason[reason]
        setNotice(`The call ended (${typeof name === 'string' ? name.toLowerCase().replace(/_/g, ' ') : reason}).`)
      }
    }
    const onMediaError = (err: Error) => setNotice(`Microphone error: ${err.message}`)
    room.on(RoomEvent.Disconnected, onDisconnected)
    room.on(RoomEvent.MediaDevicesError, onMediaError)

    void (async () => {
      let s: SessionStart
      try {
        s = await api.startSession(initialPersona.id)
      } catch (err) {
        if (!cancelled) {
          setFatal(sessionErrorMessage(err))
          setPhase('error')
        }
        return
      }
      if (cancelled) return
      setSession(s)
      setPhase('connecting')
      try {
        await room.connect(s.livekit_url, s.token)
      } catch (err) {
        if (!cancelled) {
          setFatal(`Could not join the call: ${err instanceof Error ? err.message : String(err)}`)
          setPhase('error')
        }
        return
      }
      if (cancelled) return void room.disconnect()
      setPhase('connected')
      try {
        await room.localParticipant.setMicrophoneEnabled(true)
      } catch (err) {
        if (!cancelled) {
          setNotice(`Microphone unavailable (${err instanceof Error ? err.message : String(err)}). You can still listen.`)
        }
      }
    })()

    return () => {
      cancelled = true
      room.off(RoomEvent.Disconnected, onDisconnected)
      room.off(RoomEvent.MediaDevicesError, onMediaError)
      void room.disconnect()
    }
  }, [room, initialPersona.id])

  const end = useCallback(() => {
    void room.disconnect()
    onExit()
  }, [room, onExit])

  const showNotice = useCallback((msg: string) => setNotice(msg), [])

  return (
    <RoomContext.Provider value={room}>
      <div className="call-page">
        <header className="call-header">
          <button className="btn ghost icon-only" onClick={end} aria-label="Back to personas" title="Back">
            <ArrowLeftIcon />
          </button>
          <PersonaPortrait persona={persona} className="header-portrait" />
          <div className="call-title">
            <h1>{persona.name}</h1>
            <span>{BACKEND_LABELS[persona.avatar.backend] ?? persona.avatar.backend}</span>
          </div>
          <ConnectionPill phase={phase} />
        </header>

        {notice && (
          <div className="banner warn" role="alert">
            <span>{notice}</span>
            <button className="link" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}

        <main className="call-layout">
          <section className="stage-wrap">
            <AvatarStage persona={persona} phase={phase} session={session} debug={debug} onError={showNotice} />

            {(phase === 'error' || phase === 'ended') && (
              <div className="stage-message">
                <h2>{phase === 'error' ? "Couldn't start the call" : 'Call ended'}</h2>
                {fatal && <p>{fatal}</p>}
                <div className="stage-message-actions">
                  <button className="btn ghost" onClick={onExit}>
                    Back to personas
                  </button>
                  <button
                    className="btn primary"
                    onClick={onRestart}
                  >
                    {phase === 'error' ? 'Try again' : 'Call again'}
                  </button>
                </div>
              </div>
            )}

            {phase !== 'error' && phase !== 'ended' && <CallControls onEnd={end} onError={showNotice} />}
          </section>
          <Transcript agentName={persona.name} />
        </main>
      </div>
    </RoomContext.Provider>
  )
}
