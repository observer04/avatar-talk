// Minimal typings for the untyped @met4citizen ES modules. Only the surface we use.

declare module '@met4citizen/talkinghead' {
  export interface ShowAvatarOptions {
    url: string
    body?: 'M' | 'F'
    avatarMood?: string
    lipsyncLang?: string
    baseline?: Record<string, number>
    [key: string]: unknown
  }

  export interface MorphTarget {
    newvalue: number | null
    needsUpdate: boolean
    [key: string]: unknown
  }

  export class TalkingHead {
    constructor(node: HTMLElement, opt?: Record<string, unknown>)
    audioCtx: AudioContext
    audioSpeechGainNode: GainNode
    audioReverbNode: ConvolverNode
    mtAvatar: Record<string, MorphTarget>
    opt: Record<string, unknown> & { update?: ((dt: number) => void) | null }
    isSpeaking: boolean
    showAvatar(
      avatar: ShowAvatarOptions,
      onprogress?: ((ev: ProgressEvent) => void) | null,
    ): Promise<void>
    getMoodNames(): string[]
    setMood(mood: string): void
    playGesture(name: string, dur?: number, mirror?: boolean, ms?: number): void
    stopGesture(ms?: number): void
    lookAtCamera(ms: number): void
    makeEyeContact(ms: number): void
    speakWithHands(delay?: number, prob?: number): void
    setView(view: 'full' | 'mid' | 'upper' | 'head', opt?: Record<string, unknown>): void
    start(): void
    stop(): void
    dispose(): void
  }
}

declare module '@met4citizen/headaudio/dist/headaudio.min.mjs' {
  export class HeadAudio extends AudioWorkletNode {
    constructor(
      ctx: BaseAudioContext,
      options?: {
        processorOptions?: Record<string, unknown>
        parameterData?: Record<string, number>
      },
    )
    loadModel(url: string, reset?: boolean): Promise<void>
    update(dt: number): void
    start(): void
    stop(): void
    resetAll(): void
    onvalue: ((key: string, value: number) => void) | null
    onstarted: ((data: { event: 'start'; t: number }) => void) | null
    onended: ((data: { event: 'end'; t: number }) => void) | null
  }
}
