import { TalkingHead } from '@met4citizen/talkinghead'
import { HeadAudio } from '@met4citizen/headaudio/dist/headaudio.min.mjs'
import type { AvatarRenderer, AvatarState } from './AvatarRenderer'

/**
 * In-browser 3D avatar: TalkingHead (Three.js) for the body/face and HeadAudio
 * (AudioWorklet) for audio-driven visemes. Zero server GPU — the bot only sends
 * an audio track and small JSON messages.
 *
 * Audio graph (all inside TalkingHead's AudioContext so lips and voice share
 * one clock):
 *
 *   remote MediaStream ─► MediaStreamSource ─┬─► HeadAudio (visemes → mtAvatar)
 *                                            └─► DelayNode ─► destination
 *
 * The DelayNode holds the voice back by roughly HeadAudio's analysis latency
 * so the mouth doesn't trail the sound.
 */

export interface TalkingHeadOptions {
  avatarUrl?: string
  body?: 'M' | 'F'
  cameraView?: 'full' | 'mid' | 'upper' | 'head'
  /** Seconds the voice is delayed to line up with viseme detection. */
  lipsyncDelaySec?: number
}

const ASSET_BASE = import.meta.env.BASE_URL

// Gesture templates in TalkingHead 1.8.x, plus its animated-emoji head moves
// ('yes' = nod, 'no' = head shake) which playGesture also accepts.
const GESTURES = new Set([
  'handup', 'index', 'ok', 'thumbup', 'thumbdown', 'side', 'shrug', 'namaste',
  'yes', 'no',
])

export class TalkingHeadRenderer implements AvatarRenderer {
  private opts: Required<TalkingHeadOptions>
  private head: TalkingHead | null = null
  private headaudio: HeadAudio | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private delay: DelayNode | null = null
  private sinkEl: HTMLAudioElement | null = null
  private lastSpeechEnd = 0
  /** Bumped by dispose() so an in-flight mount() can tell it was superseded. */
  private generation = 0

  constructor(opts: TalkingHeadOptions = {}) {
    this.opts = {
      avatarUrl: `${ASSET_BASE}avatars/brunette.glb`,
      body: 'F',
      cameraView: 'upper',
      lipsyncDelaySec: 0.08,
      ...opts,
    }
  }

  async mount(el: HTMLElement): Promise<void> {
    const gen = ++this.generation
    const stale = () => gen !== this.generation
    const head = new TalkingHead(el, {
      lipsyncModules: [], // lip-sync comes from HeadAudio, not text
      cameraView: this.opts.cameraView,
      cameraRotateEnable: false,
      avatarMood: 'neutral',
      avatarIdleEyeContact: 0.3,
      avatarSpeakingEyeContact: 0.6,
      modelFPS: 30,
    })
    this.head = head

    await head.showAvatar({
      url: this.opts.avatarUrl,
      body: this.opts.body,
      avatarMood: 'neutral',
      lipsyncLang: 'en',
    })
    if (stale()) return

    // HeadAudio worklet + pre-trained viseme model (served from /public).
    await head.audioCtx.audioWorklet.addModule(`${ASSET_BASE}headaudio/headworklet.min.mjs`)
    const headaudio = new HeadAudio(head.audioCtx, {
      parameterData: { vadGateActiveDb: -40, vadGateInactiveDb: -60 },
    })
    await headaudio.loadModel(`${ASSET_BASE}headaudio/model-en-mixed.bin`)
    if (stale()) {
      headaudio.disconnect()
      return
    }

    headaudio.onvalue = (key, value) => {
      const mt = head.mtAvatar[key]
      if (mt) Object.assign(mt, { newvalue: value, needsUpdate: true })
    }
    headaudio.onended = () => {
      this.lastSpeechEnd = performance.now()
    }
    headaudio.onstarted = () => {
      // New sentence after a pause: re-establish eye contact, talk with hands.
      if (performance.now() - this.lastSpeechEnd > 150) {
        head.lookAtCamera(500)
        head.speakWithHands()
      }
    }
    head.opt.update = headaudio.update.bind(headaudio)
    this.headaudio = headaudio
  }

  async unlockAudio(): Promise<void> {
    const ctx = this.head?.audioCtx
    if (ctx && ctx.state !== 'running') await ctx.resume()
  }

  attachBotAudio(track: MediaStreamTrack): void {
    const head = this.head
    const headaudio = this.headaudio
    if (!head || !headaudio) return
    this.detachAudio()

    const stream = new MediaStream([track])

    // Chrome quirk: a remote WebRTC track only feeds createMediaStreamSource
    // while it is also attached to a media element. Keep that element muted
    // so the voice plays exactly once, through the Web Audio graph below.
    const sink = new Audio()
    sink.muted = true
    sink.srcObject = stream
    void sink.play().catch(() => {})
    this.sinkEl = sink

    const ctx = head.audioCtx
    const source = ctx.createMediaStreamSource(stream)
    const delay = new DelayNode(ctx, { delayTime: this.opts.lipsyncDelaySec })
    source.connect(headaudio)
    source.connect(delay)
    delay.connect(ctx.destination)
    this.source = source
    this.delay = delay
  }

  applyState(state: AvatarState): void {
    const head = this.head
    if (!head) return
    const mood = state.mood?.trim().toLowerCase()
    if (mood && head.getMoodNames().includes(mood)) {
      head.setMood(mood)
    }
    const gesture = state.gesture?.trim().toLowerCase()
    if (gesture && GESTURES.has(gesture)) {
      head.playGesture(gesture, 2.5)
    }
  }

  setSpeaking(speaking: boolean): void {
    const head = this.head
    if (!head) return
    // TalkingHead switches idle↔speaking body language on this flag; its own
    // TTS path normally sets it, but our audio comes from outside.
    head.isSpeaking = speaking
    if (speaking) {
      head.lookAtCamera(500)
    } else {
      // Interrupted or finished: close the mouth now rather than waiting for
      // HeadAudio's VAD to time out on the drained track.
      for (const [key, mt] of Object.entries(head.mtAvatar)) {
        if (key.startsWith('viseme_')) Object.assign(mt, { newvalue: 0, needsUpdate: true })
      }
    }
  }

  dispose(): void {
    this.generation++
    this.detachAudio()
    this.headaudio?.disconnect()
    this.headaudio = null
    if (this.head) {
      this.head.stop()
      this.head.dispose()
      void this.head.audioCtx.close().catch(() => {})
      this.head = null
    }
  }

  private detachAudio(): void {
    this.source?.disconnect()
    this.delay?.disconnect()
    this.source = null
    this.delay = null
    if (this.sinkEl) {
      this.sinkEl.srcObject = null
      this.sinkEl = null
    }
  }
}
