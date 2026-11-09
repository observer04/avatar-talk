import { useState, type CSSProperties } from 'react'
import {
  useConnectionState,
  useLocalParticipant,
  useTrackVolume,
  useVoiceAssistant,
} from '@livekit/components-react'
import { ConnectionState, type LocalAudioTrack } from 'livekit-client'
import { MicIcon, MicOffIcon, PhoneOffIcon } from '../icons'
import type { CallPhase } from './AvatarStage'

interface Props {
  onEnd(): void
  onError(message: string): void
}

export function CallControls({ onEnd, onError }: Props) {
  const { localParticipant, isMicrophoneEnabled, microphoneTrack } = useLocalParticipant()
  const connection = useConnectionState()
  const [pending, setPending] = useState(false)
  const micTrack = microphoneTrack?.track as LocalAudioTrack | undefined
  const level = useTrackVolume(isMicrophoneEnabled ? micTrack : undefined)
  const connected = connection === ConnectionState.Connected

  const toggleMic = async () => {
    setPending(true)
    try {
      await localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)
    } catch (err) {
      onError(`Microphone error: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="call-controls" role="toolbar" aria-label="Call controls">
      <button
        className={`round-btn ${connected && !isMicrophoneEnabled ? 'muted' : ''}`}
        onClick={() => void toggleMic()}
        disabled={!connected || pending}
        aria-pressed={!isMicrophoneEnabled}
        aria-label={isMicrophoneEnabled ? 'Mute microphone' : 'Unmute microphone'}
        title={isMicrophoneEnabled ? 'Mute' : 'Unmute'}
        style={{ '--mic-level': Math.min(1, level * 4) } as CSSProperties}
      >
        {isMicrophoneEnabled ? <MicIcon /> : <MicOffIcon />}
      </button>
      <button className="round-btn end" onClick={onEnd} aria-label="End call" title="End call">
        <PhoneOffIcon />
      </button>
    </div>
  )
}

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  [ConnectionState.Disconnected]: 'Disconnected',
  [ConnectionState.Connecting]: 'Connecting',
  [ConnectionState.Connected]: 'Connected',
  [ConnectionState.Reconnecting]: 'Reconnecting',
  [ConnectionState.SignalReconnecting]: 'Reconnecting',
}

/** Header pill: transport state, then the agent's state once it is live. */
export function ConnectionPill({ phase }: { phase: CallPhase }) {
  const connection = useConnectionState()
  const { state, agent } = useVoiceAssistant()
  let label: string
  let tone: 'idle' | 'busy' | 'ok' | 'bad'
  if (phase === 'starting') [label, tone] = ['Starting', 'busy']
  else if (phase === 'error') [label, tone] = ['Error', 'bad']
  else if (phase === 'ended') [label, tone] = ['Call ended', 'idle']
  else if (connection !== ConnectionState.Connected) {
    label = CONNECTION_LABELS[connection]
    tone = connection === ConnectionState.Disconnected ? 'idle' : 'busy'
  } else if (!agent) [label, tone] = ['Waiting for agent', 'busy']
  else [label, tone] = [`Live · ${state}`, 'ok']
  return (
    <span className={`conn-pill tone-${tone}`} role="status">
      <span className="dot" aria-hidden />
      {label}
    </span>
  )
}
