import {
  PipecatClient,
  type BotOutputData,
  type Participant,
  type TransportState,
} from '@pipecat-ai/client-js'
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport'
import type { AvatarState } from '../avatar/AvatarRenderer'

/** Signaling endpoint on the Pipecat bot (proxied to :7860 by Vite in dev). */
const OFFER_ENDPOINT = import.meta.env.VITE_OFFER_ENDPOINT ?? '/api/offer'
/** ICE servers + gathering mode, decided by the server (local dev vs. hosted). */
const RTC_CONFIG_ENDPOINT = import.meta.env.VITE_RTC_CONFIG_ENDPOINT ?? '/api/rtc-config'

interface RtcConfig {
  iceServers: RTCIceServer[]
  waitForIceGathering: boolean
}

const DEFAULT_RTC_CONFIG: RtcConfig = {
  iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  waitForIceGathering: false,
}

async function loadRtcConfig(): Promise<RtcConfig> {
  try {
    const r = await fetch(RTC_CONFIG_ENDPOINT)
    if (!r.ok) return DEFAULT_RTC_CONFIG
    return { ...DEFAULT_RTC_CONFIG, ...((await r.json()) as Partial<RtcConfig>) }
  } catch {
    return DEFAULT_RTC_CONFIG
  }
}

/** Optional access key for public deployments: open the page as ...?key=<token>. */
const accessToken = () => new URLSearchParams(location.search).get('key') ?? undefined

export interface SessionHandlers {
  onStateChange(state: TransportState): void
  onBotAudioTrack(track: MediaStreamTrack): void
  onBotVideoTrack?(track: MediaStreamTrack): void
  onAvatarState(state: AvatarState): void
  onBotSpeaking(speaking: boolean): void
  onUserSpeaking(speaking: boolean): void
  onUserText(text: string, final: boolean): void
  onBotOutput(data: BotOutputData): void
  onError(message: string): void
}

const isRemote = (p?: Participant) => !p?.local

/** Thin wrapper around PipecatClient + SmallWebRTC (peer-to-peer, no media server). */
export class AgentSession {
  private client: PipecatClient | null = null
  private h: SessionHandlers

  constructor(h: SessionHandlers) {
    this.h = h
  }

  private createClient(rtc: RtcConfig): PipecatClient {
    const h = this.h
    return new PipecatClient({
      transport: new SmallWebRTCTransport({
        iceServers: rtc.iceServers,
        waitForICEGathering: rtc.waitForIceGathering,
      }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTransportStateChanged: (s) => h.onStateChange(s),
        onTrackStarted: (track, participant) => {
          if (!isRemote(participant)) return
          if (track.kind === 'audio') h.onBotAudioTrack(track)
          else if (track.kind === 'video') h.onBotVideoTrack?.(track)
        },
        onServerMessage: (data: unknown) => {
          const msg = data as { type?: string; mood?: string; gesture?: string } | null
          if (msg?.type === 'avatar') h.onAvatarState({ mood: msg.mood, gesture: msg.gesture })
        },
        onBotStartedSpeaking: () => h.onBotSpeaking(true),
        onBotStoppedSpeaking: () => h.onBotSpeaking(false),
        onBotInterrupted: () => h.onBotSpeaking(false),
        onUserStartedSpeaking: () => h.onUserSpeaking(true),
        onUserStoppedSpeaking: () => h.onUserSpeaking(false),
        onUserInput: (d) => {
          if (d.input_type === 'chat') return
          h.onUserText(d.text, d.final)
        },
        onBotOutput: (d) => h.onBotOutput(d),
        onError: (m) => {
          const d = m.data as { error?: string; message?: string; fatal?: boolean } | undefined
          console.warn('[bot error]', d)
          h.onError(`${d?.fatal ? 'Fatal bot error' : 'Bot error'}: ${d?.error ?? d?.message ?? 'unknown'}`)
        },
      },
    })
  }

  async connect(): Promise<void> {
    // A fresh client per call, so ICE settings always match the server's current config.
    await this.client?.disconnect().catch(() => {})
    this.client = this.createClient(await loadRtcConfig())
    const token = accessToken()
    await this.client.connect({
      webrtcRequestParams: {
        endpoint: OFFER_ENDPOINT,
        ...(token ? { requestData: { access_token: token } } : {}),
      },
    })
  }

  async disconnect(): Promise<void> {
    await this.client?.disconnect()
  }

  setMicEnabled(enabled: boolean): void {
    this.client?.enableMic(enabled)
  }

  get micEnabled(): boolean {
    return this.client?.isMicEnabled ?? true
  }
}
