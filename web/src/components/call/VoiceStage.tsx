import type { CSSProperties } from 'react'
import { useMultibandTrackVolume, useVoiceAssistant } from '@livekit/components-react'
import type { Persona } from '../../api/types'
import { usePersonaPhoto } from '../../hooks/usePersonaPhoto'
import { PersonaPortrait } from '../PersonaPortrait'

const BANDS = 9

/** Voice-only persona: the photo (or a glowing orb) that breathes with the agent's voice. */
export function VoiceStage({ persona }: { persona: Persona }) {
  const { audioTrack, state } = useVoiceAssistant()
  const bands = useMultibandTrackVolume(audioTrack, { bands: BANDS })
  const hasPhoto = usePersonaPhoto(persona) !== null
  const level = bands.length ? Math.min(1, (bands.reduce((a, b) => a + b, 0) / bands.length) * 1.6) : 0

  return (
    <div className={`voice-stage agent-${state}`} style={{ '--level': level } as CSSProperties}>
      <div className="voice-core">
        <div className="voice-ring ring-1" aria-hidden />
        <div className="voice-ring ring-2" aria-hidden />
        {hasPhoto ? <PersonaPortrait persona={persona} className="voice-photo" /> : <div className="voice-orb" aria-hidden />}
      </div>
      <div className="voice-bars" aria-hidden>
        {Array.from({ length: BANDS }, (_, i) => (
          <span key={i} style={{ '--h': Math.max(0.08, Math.min(1, (bands[i] ?? 0) * 1.8)) } as CSSProperties} />
        ))}
      </div>
    </div>
  )
}
