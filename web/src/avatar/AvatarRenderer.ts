/**
 * Contract for avatars rendered in the browser from the agent's audio
 * (currently TalkingHeadRenderer). Photoreal backends (Simli, FlashHead) render
 * server-side and publish a LiveKit video track instead, which the call view
 * plays directly.
 */

/** Payload of an `avatar.cue` text stream from the agent: `{"mood": ..}` or `{"gesture": ..}`. */
export interface AvatarState {
  mood?: string
  gesture?: string
}

export interface AvatarRenderer {
  /** Create the visual inside `el`. Resolves once the avatar is visible. */
  mount(el: HTMLElement): Promise<void>
  /** Resume audio output. Call from a user gesture if the browser blocked autoplay. */
  unlockAudio(): Promise<void>
  /** Bot audio arrived (or was replaced). The renderer owns its playback. */
  attachBotAudio(track: MediaStreamTrack): void
  /** Apply an agent-driven expression/gesture. Unknown names are ignored. */
  applyState(state: AvatarState): void
  /** Bot started/stopped speaking (also fires on interruption). */
  setSpeaking(speaking: boolean): void
  dispose(): void
}
