import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { RoomAudioRenderer, StartAudio, useTracks, useVoiceAssistant } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { PHOTO_BACKENDS, type Persona, type SessionStart } from '../../api/types'
import { PersonaPortrait } from '../PersonaPortrait'
import { DebugOverlay } from './DebugOverlay'
import { VideoStage } from './VideoStage'
import { VoiceStage } from './VoiceStage'
import { AGENT_LIVE_STATES, pickAvatarVideo } from './tracks'

const TalkingHeadStage = lazy(() => import('./TalkingHeadStage'))

/**
 * How long to wait for a photoreal avatar's video once the agent is live
 * before treating the call as voice-only (the agent falls back to its own
 * audio when the avatar fails to join).
 */
const VIDEO_GRACE_MS = 6000

export type StageMode = 'video' | 'browser3d' | 'awaiting-video' | 'voice'
export type CallPhase = 'starting' | 'connecting' | 'connected' | 'ended' | 'error'

interface Props {
  persona: Persona
  phase: CallPhase
  session: SessionStart | null
  /** Show the `?debug=1` overlay. */
  debug: boolean
  onError(message: string): void
}

export function AvatarStage({ persona, phase, session, debug, onError }: Props) {
  const assistant = useVoiceAssistant()
  const cameraTracks = useTracks([Track.Source.Camera], { onlySubscribed: true })
  const video = pickAvatarVideo(cameraTracks)
  const backend = persona.avatar.backend
  const photoBackend = PHOTO_BACKENDS.includes(backend)
  const agentLive = AGENT_LIVE_STATES.has(assistant.state)

  const [videoGaveUp, setVideoGaveUp] = useState(false)
  const [threeFailed, setThreeFailed] = useState(false)
  const [visualReady, setVisualReady] = useState(false)

  const hasVideo = video !== undefined
  useEffect(() => {
    if (!photoBackend || !agentLive || hasVideo) return
    const t = setTimeout(() => setVideoGaveUp(true), VIDEO_GRACE_MS)
    return () => clearTimeout(t)
  }, [photoBackend, agentLive, hasVideo])

  // Any remote camera wins; otherwise the persona's backend decides.
  const mode: StageMode = video
    ? 'video'
    : backend === 'browser3d' && !threeFailed
      ? 'browser3d'
      : photoBackend && !videoGaveUp
        ? 'awaiting-video'
        : 'voice'

  const onThreeReady = useCallback(() => setVisualReady(true), [])
  const onThreeError = useCallback(
    (msg: string) => {
      setThreeFailed(true)
      onError(`${msg}. Continuing with voice only.`)
    },
    [onError],
  )

  const agentPresent = assistant.agent !== undefined && assistant.state !== 'connecting' && assistant.state !== 'initializing'
  const visualPending = (mode === 'video' || mode === 'browser3d') && !visualReady

  let loading: string | null = null
  if (phase === 'starting') loading = 'Starting session…'
  else if (phase === 'connecting') loading = 'Connecting…'
  else if (phase === 'connected') {
    if (!agentPresent) loading = `Waiting for ${persona.name}…`
    else if (mode === 'awaiting-video') loading = 'Starting video avatar…'
    else if (visualPending) loading = mode === 'browser3d' ? 'Loading 3D avatar…' : 'Starting video…'
  }

  return (
    <div className={`avatar-stage mode-${mode}`} data-mode={mode}>
      <PersonaPortrait persona={persona} className="stage-backdrop" />

      {mode === 'video' && video && <VideoStage trackRef={video} onReady={setVisualReady} />}
      {mode === 'browser3d' && (
        <Suspense fallback={null}>
          <TalkingHeadStage
            audioTrack={assistant.audioTrack?.publication.track?.mediaStreamTrack}
            speaking={assistant.state === 'speaking'}
            onReady={onThreeReady}
            onError={onThreeError}
          />
        </Suspense>
      )}
      {mode === 'voice' && <VoiceStage persona={persona} />}
      {mode === 'voice' && photoBackend && agentLive && (
        <div className="stage-note" role="status">
          The video avatar is still warming up. Keep talking: the face takes over when it is ready.
        </div>
      )}

      {/* browser3d plays the agent audio through TalkingHead's Web Audio graph
          (lip-sync); mounting RoomAudioRenderer too would play it twice. */}
      {mode !== 'browser3d' && (
        <>
          <RoomAudioRenderer />
          <StartAudio label="Tap to enable audio" className="btn primary unlock-audio" />
        </>
      )}

      {loading && phase !== 'ended' && phase !== 'error' && (
        <div className="stage-loading" role="status" aria-live="polite">
          <div className="loading-portrait">
            <PersonaPortrait persona={persona} />
            <div className="shimmer" aria-hidden />
          </div>
          <p>{loading}</p>
          {mode === 'awaiting-video' && phase === 'connected' && agentPresent && (
            <small>Photoreal avatars can take a few seconds to join.</small>
          )}
        </div>
      )}

      {debug && <DebugOverlay session={session} mode={mode} />}

      {phase === 'connected' && agentPresent && !loading && (
        <div className={`agent-state-chip state-${assistant.state}`} aria-live="polite">
          <span className="dot" aria-hidden />
          {assistant.state}
        </div>
      )}
    </div>
  )
}
