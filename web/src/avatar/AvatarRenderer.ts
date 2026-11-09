/**
 * Renderer-agnostic avatar contract. The call UI only talks to this interface,
 * so a photoreal backend (Simli / self-hosted MuseTalk or FlashHead streaming a
 * video track) can replace the in-browser 3D head without touching the rest.
 */

/** Payload of the server's `{type: "avatar", ...}` RTVI server message. */
export interface AvatarState {
  mood?: string
  gesture?: string
}

export interface AvatarRenderer {
  /** Create the visual inside `el`. Resolves once the avatar is visible. */
  mount(el: HTMLElement): Promise<void>
  /** Resume audio output. Must be called from a user gesture (e.g. Connect click). */
  unlockAudio(): Promise<void>
  /** Bot audio arrived (or was replaced). The renderer owns its playback. */
  attachBotAudio(track: MediaStreamTrack): void
  /** Optional: a server-rendered video track (photoreal backends only). */
  attachBotVideo?(track: MediaStreamTrack): void
  /** Apply an agent-driven expression/gesture. Unknown names are ignored. */
  applyState(state: AvatarState): void
  /** Bot started/stopped speaking (also fires on interruption). */
  setSpeaking(speaking: boolean): void
  dispose(): void
}
