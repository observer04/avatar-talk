import type { TransportState } from '@pipecat-ai/client-js'

const LABELS: Partial<Record<TransportState, string>> = {
  disconnected: 'Offline',
  initializing: 'Starting…',
  initialized: 'Starting…',
  authenticating: 'Starting…',
  authenticated: 'Starting…',
  connecting: 'Connecting…',
  connected: 'Waiting for avatar…',
  ready: 'Live',
  disconnecting: 'Leaving…',
  error: 'Error',
}

interface Props {
  state: TransportState
  avatarReady: boolean
  micEnabled: boolean
  onConnect(): void
  onDisconnect(): void
  onToggleMic(): void
}

export function Controls({ state, avatarReady, micEnabled, onConnect, onDisconnect, onToggleMic }: Props) {
  const idle = state === 'disconnected' || state === 'error'
  const live = state === 'ready'
  return (
    <div className="controls">
      <span className={`status status-${state}`}>
        <span className="dot" aria-hidden />
        {LABELS[state] ?? state}
      </span>
      <button className="btn secondary" onClick={onToggleMic} disabled={!live} aria-pressed={!micEnabled}>
        {micEnabled ? 'Mute' : 'Unmute'}
      </button>
      {idle ? (
        <button className="btn primary" onClick={onConnect} disabled={!avatarReady}>
          {avatarReady ? 'Start conversation' : 'Loading avatar…'}
        </button>
      ) : (
        <button className="btn danger" onClick={onDisconnect} disabled={state === 'disconnecting'}>
          End
        </button>
      )}
    </div>
  )
}
