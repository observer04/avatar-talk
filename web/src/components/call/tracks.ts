import { ParticipantKind, type Participant } from 'livekit-client'
import type { AgentState, TrackReference } from '@livekit/components-react'

/** Identities the photoreal avatar workers join with (services/agent/src/agent/avatars). */
export const AVATAR_IDENTITIES = ['simli-avatar-agent', 'flashhead-avatar']

/** Text-stream topic for browser3d body-language cues (avatar_core.AVATAR_CUE_TOPIC). */
export const AVATAR_CUE_TOPIC = 'avatar.cue'

/** Last body-language cues received, for the `?debug=1` overlay. */
export const cueLog: { count: number; last: string | null } = { count: 0, last: null }

/** Agent states in which the agent session is up and talking to us. */
export const AGENT_LIVE_STATES: ReadonlySet<AgentState> = new Set(['idle', 'listening', 'thinking', 'speaking'])

/**
 * Choose the remote video to show as the avatar: a known avatar worker first,
 * then any agent-kind participant, then any remote camera at all.
 */
export function pickAvatarVideo(tracks: TrackReference[]): TrackReference | undefined {
  const remote = tracks.filter((t) => !t.participant.isLocal && t.publication.track && !t.publication.isMuted)
  return (
    remote.find((t) => AVATAR_IDENTITIES.includes(t.participant.identity)) ??
    remote.find((t) => t.participant.kind === ParticipantKind.AGENT) ??
    remote[0]
  )
}

const KIND_NAMES: Record<number, string> = {
  [ParticipantKind.STANDARD]: 'standard',
  [ParticipantKind.INGRESS]: 'ingress',
  [ParticipantKind.EGRESS]: 'egress',
  [ParticipantKind.SIP]: 'sip',
  [ParticipantKind.AGENT]: 'agent',
}

export function kindName(p: Participant): string {
  return KIND_NAMES[p.kind] ?? String(p.kind)
}
