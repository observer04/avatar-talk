import type { AvatarRenderer, AvatarState } from './AvatarRenderer'

/**
 * Photoreal backends (Simli, self-hosted MuseTalk / FlashHead) render on the
 * server and stream a video track; this renderer just plays it. Expression and
 * speaking hints are handled server-side, so they are no-ops here.
 */
export class VideoTrackRenderer implements AvatarRenderer {
  private video: HTMLVideoElement | null = null
  private audio: HTMLAudioElement | null = null

  async mount(el: HTMLElement): Promise<void> {
    const video = document.createElement('video')
    video.autoplay = true
    video.playsInline = true
    video.muted = true // audio plays via the separate <audio> element
    video.className = 'avatar-video'
    el.appendChild(video)
    this.video = video
    this.audio = new Audio()
    this.audio.autoplay = true
  }

  async unlockAudio(): Promise<void> {
    await this.audio?.play().catch(() => {})
  }

  attachBotAudio(track: MediaStreamTrack): void {
    if (this.audio) this.audio.srcObject = new MediaStream([track])
  }

  attachBotVideo(track: MediaStreamTrack): void {
    if (this.video) this.video.srcObject = new MediaStream([track])
  }

  applyState(_state: AvatarState): void {}

  setSpeaking(_speaking: boolean): void {}

  dispose(): void {
    this.video?.remove()
    if (this.audio) this.audio.srcObject = null
    this.video = null
    this.audio = null
  }
}
