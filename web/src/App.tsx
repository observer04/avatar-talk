import { useCallback, useEffect, useMemo, useState } from 'react'
import type { TransportState } from '@pipecat-ai/client-js'
import type { AvatarRenderer } from './avatar/AvatarRenderer'
import { TalkingHeadRenderer } from './avatar/TalkingHeadRenderer'
import { VideoTrackRenderer } from './avatar/VideoTrackRenderer'
import { AgentSession } from './rtc/client'
import { addBotOutput, addUserText, type Entry } from './state/transcript'
import { CallView } from './components/CallView'
import { Controls } from './components/Controls'
import { Transcript } from './components/Transcript'

/** `?renderer=video` switches to a server-rendered (photoreal) avatar track. */
function createRenderer(): AvatarRenderer {
  const mode = new URLSearchParams(location.search).get('renderer')
  return mode === 'video' ? new VideoTrackRenderer() : new TalkingHeadRenderer()
}

export default function App() {
  const renderer = useMemo(() => createRenderer(), [])
  const [state, setState] = useState<TransportState>('disconnected')
  const [avatarReady, setAvatarReady] = useState(false)
  const [micEnabled, setMicEnabled] = useState(true)
  const [botSpeaking, setBotSpeaking] = useState(false)
  const [userSpeaking, setUserSpeaking] = useState(false)
  const [entries, setEntries] = useState<Entry[]>([])
  const [error, setError] = useState<string | null>(null)

  const session = useMemo(
    () =>
      new AgentSession({
        onStateChange: (s) => {
          setState(s)
          if (s === 'disconnected') {
            setBotSpeaking(false)
            setUserSpeaking(false)
            renderer.setSpeaking(false)
          }
        },
        onBotAudioTrack: (t) => renderer.attachBotAudio(t),
        onBotVideoTrack: (t) => renderer.attachBotVideo?.(t),
        onAvatarState: (a) => renderer.applyState(a),
        onBotSpeaking: (on) => {
          setBotSpeaking(on)
          renderer.setSpeaking(on)
        },
        onUserSpeaking: setUserSpeaking,
        onUserText: (text, final) => setEntries((e) => addUserText(e, text, final)),
        onBotOutput: (d) => setEntries((e) => addBotOutput(e, d)),
        onError: setError,
      }),
    [renderer],
  )
  useEffect(() => () => void session.disconnect().catch(() => {}), [session])

  const connect = useCallback(async () => {
    setError(null)
    try {
      await renderer.unlockAudio() // must run inside the click gesture
      await session.connect()
      setMicEnabled(session.micEnabled)
    } catch (err) {
      setError(`Could not reach the bot: ${err instanceof Error ? err.message : String(err)}`)
      setState('disconnected')
    }
  }, [renderer, session])

  const disconnect = useCallback(() => {
    void session.disconnect().catch(() => {})
  }, [session])

  const toggleMic = useCallback(() => {
    const next = !micEnabled
    session.setMicEnabled(next)
    setMicEnabled(next)
  }, [micEnabled, session])

  return (
    <div className="app">
      <header className="topbar">
        <h1>Avatar Talk</h1>
        <Controls
          state={state}
          avatarReady={avatarReady}
          micEnabled={micEnabled}
          onConnect={connect}
          onDisconnect={disconnect}
          onToggleMic={toggleMic}
        />
      </header>
      {error && (
        <div className="error" role="alert">
          {error}
          <button className="link" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}
      <main className="layout">
        <CallView
          renderer={renderer}
          onReady={() => setAvatarReady(true)}
          onError={setError}
          botSpeaking={botSpeaking}
          userSpeaking={userSpeaking}
        />
        <Transcript entries={entries} />
      </main>
    </div>
  )
}
