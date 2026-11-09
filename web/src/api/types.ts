// Mirrors avatar_core.schemas (packages/core) and services/api. See docs/api.md.

export type AvatarBackend = 'simli' | 'flashhead' | 'browser3d' | 'none'
export type AvatarStatus = 'not_required' | 'pending' | 'provisioning' | 'ready' | 'failed'

export const AVATAR_BACKENDS: AvatarBackend[] = ['simli', 'flashhead', 'browser3d', 'none']
/** Backends that build the face from the persona photo (photo required). */
export const PHOTO_BACKENDS: AvatarBackend[] = ['simli', 'flashhead']

export const BACKEND_LABELS: Record<AvatarBackend, string> = {
  simli: 'Simli',
  flashhead: 'FlashHead',
  browser3d: '3D avatar',
  none: 'Voice only',
}

export interface VoiceConfig {
  provider: string
  model: string
  voice_id?: string | null
  speed?: number | null
  language?: string
}

export interface AvatarConfig {
  backend: AvatarBackend
  status: AvatarStatus
  photo_key: string | null
  simli_face_id: string | null
  error: string | null
}

export interface Persona {
  id: string
  name: string
  system_prompt: string
  greeting: string | null
  voice: VoiceConfig
  avatar: AvatarConfig
  tools: string[]
  max_session_minutes: number
  created_at: string
}

export interface PersonaCreate {
  name: string
  system_prompt?: string
  greeting?: string | null
  voice?: VoiceConfig
  avatar_backend: AvatarBackend
  tools?: string[]
}

export interface SessionStart {
  session_id: string
  room: string
  livekit_url: string
  token: string
  persona: Persona
}

/** A persona can start a session once its avatar needs nothing more. */
export function isCallable(p: Persona): boolean {
  return p.avatar.status === 'ready' || p.avatar.status === 'not_required'
}
