# Comparative architecture: Real-time AI Digital Human

For your product, I'd narrow the comparison to these three:

[The Live Avatar Landscape: APIs, Transport and Subjective Evaluation of 10 Leading Providers | by Gustavo Garcia | Medium](https://images.openai.com/static-rsc-4/lNQ8WoSB9DSYBPC7xQOjSDZeD3_a5YIWrkisCn-YgiAm4_sfiL3FSv1qK_-6vSJp2C-EKLIzGZdUQ9CSy1DB3ywd-8AEYxkw_TRzhx7ncGwCK31nMOI3zQ2mRSpJcWKQU-QChO-Wqp8trwDuMpcAgZCRxT0Ka6tCVJj5iCn4fqM?purpose=inline)

1\. OpenSpline — Open source

Modern, audio-driven talking-head generation from a portrait. Designed to integrate with an existing voice agent.

[GitHub](https://github.com/openspline/openspline?utm_source=chatgpt.com)

[LiveTalking 数字人项目 Windows 部署完全指南（EPGF 架构） - 技术栈](https://images.openai.com/static-rsc-4/5yqpoEF4GYOxX52VjeU3WfiEHTjMlHlpoOizHfWIxB8bXb9zlvO63WfoulZbri7u5HbKYOe3Inakc4JaOH8Sm4CnVnzAUsi94ipROH-zaYjD8qBIBiUzQ46TJmqXI87Xhin61aKdP0P919mTIktRASm7rpzfWfWA53kw0HOooJA?purpose=inline)

2\. LiveTalking — Open source

More established modular framework supporting multiple lip-sync engines, video-based avatars, and prerecorded body actions.

[GitHub](https://github.com/lipku/LiveTalking?utm_source=chatgpt.com)

[Microsoft crée des avatars intelligents qui s'expriment à notre place](https://images.openai.com/static-rsc-4/peuDhqjl8i1xtrirmEq-xJJ6kK84bBAEcH5pY_X0BF1MwIRztqYz1vysUzxcSWeKDNfTG1ig9tEzFc4QQatVfU-U1bVD5mar4WJch4An9JBkMUDpiD8haz2WLMRJUGTioiIIJsPtNqvcA1KENXr6s0YXt1YXIxS5oKakQEKyZrw?purpose=inline)

3\. Azure Voice Live + Avatar — Fully managed

Microsoft-native conversational AI with synchronized avatars, speech recognition, voice synthesis, and turn-taking.

[Documentation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live?utm_source=chatgpt.com)

These are not identical products. OpenSpline is primarily an avatar renderer, LiveTalking is a digital-human streaming framework, and Azure Voice Live is a managed voice-agent platform with integrated rendering.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+2



## 1. Define the MVP properly

Your MVP is not simply an avatar that can lip-sync. It should feel like a video call with an AI person.

I'd establish these acceptance criteria:

- A natural-looking human avatar visible continuously, including when silent.
- Two-way, low-latency spoken conversation with pauses, interruptions, and follow-up questions.
- Mouth movements synchronized to the actual generated speech.
- Natural blinking, subtle head movement, and believable listening behavior.
- Stable identity and appearance throughout the session.
- A browser-based meeting interface with microphone controls, connection state, and session management.

For performance, I'd set a design target of under 1.5 seconds from the end of a user's turn to the beginning of the AI's audible reply, aiming closer to one second. That's a target to benchmark, not a claim about any of these products.

Your longer-term vision adds another dimension: embodiment. The avatar should eventually possess a body, react to its surroundings, point at things, perform gestures, and perhaps move within a virtual room.

That distinction will influence which architecture you choose now.

## 2. Side-by-side technology stack

| Layer                   | OpenSpline                  | LiveTalking                                 | Azure Voice Live                        |
| ----------------------- | --------------------------- | ------------------------------------------- | --------------------------------------- |
| Primary language        | Python, Node SDK            | Python                                      | Python/Node/JS SDK or API               |
| Conversation engine     | Bring your own              | Bring your own                              | Managed Voice Live                      |
| LLM                     | Azure OpenAI, etc.          | Azure OpenAI, etc.                          | Supported Azure-hosted models           |
| Speech recognition      | External / LiveKit          | External / custom                           | Integrated                              |
| Voice synthesis         | External TTS                | Pluggable TTS                               | Integrated                              |
| Avatar generation       | SoulX-FlashHead             | MuseTalk, Wav2Lip, ER-NeRF, others          | Microsoft avatar models, including VASA |
| Avatar input            | Portrait + audio            | Prepared video/avatar + speech              | Preset or eligible custom avatar        |
| Real-time streaming     | WebRTC integrations         | WebRTC / RTMP                               | WebRTC video, WebSocket control         |
| Frontend integration    | React component / SDK       | Custom frontend APIs                        | Speech SDK / WebRTC                     |
| GPU requirement         | NVIDIA GPU for self-hosting | NVIDIA GPU for self-hosting                 | None managed by you                     |
| Azure deployment        | GPU VM + CPU services       | GPU VM + CPU services                       | Foundry resource + web/backend          |
| Main engineering burden | GPU inference and streaming | Avatar preparation and pipeline integration | API integration and customization       |

Sources: OpenSpline and LiveTalking repositories, Microsoft Voice Live documentation.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+3



Important: For the two open-source choices, you still need to connect a speech agent. Neither should be assumed to provide the same complete managed voice-to-voice system as Azure Voice Live.

## 3. Feature-by-feature product comparison

Here's how the three options compare against features I'd expect in a real conversational digital-human product.

Legend:  Supported by the stack ·  Requires additional implementation · — Not a native capability

| Feature                       | OpenSpline           | LiveTalking                   | Azure Voice Live                     |
| ----------------------------- | -------------------- | ----------------------------- | ------------------------------------ |
| Real-time lip-sync            |                      |                               |                                      |
| Natural speech conversation   | &#x20;External agent | &#x20;Agent + TTS             | &#x20;Integrated                     |
| Interrupt mid-speech          |                      |                               |                                      |
| Blinking / idle motion        |                      | Via source video              |                                      |
| Custom human face             | Photo                | Prepared video                | Photo/video with access requirements |
| Facial expressions            | Generated motion     | Mostly source-video-dependent | Model-generated motion               |
| Head movements                | Generated            | Source-video-dependent        | Generated                            |
| Body gestures                 | Limited              | Prerecorded clips             | Limited/model-dependent              |
| Full-body appearance          | Not a core feature   | Video stitching               | Video avatar option                  |
| Dynamic walking / movement    | —                    | —                             | —                                    |
| Camera understanding          |                      |                               |                                      |
| Screen sharing and awareness  |                      |                               |                                      |
| Memory / RAG                  | External agent       | External agent                | External tools/backend               |
| External actions / tool calls | External agent       | External agent                | Supported                            |
| Multiple concurrent users     | GPU workers          | Supported; GPU-limited        | Managed; quota-limited               |
| Custom UI / meeting room      |                      |                               |                                      |

The documented features above are supported by the projects and Azure APIs, but their quality and reliability haven't been independently benchmarked against one another.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+3



### The critical difference for your future plans

When you say move and interact with the environment, there are actually three levels of capability.

[Young hispanic man sitting in the office at the desk, looking and talking to the camera while gesturing remotely with his hands](https://images.openai.com/static-rsc-4/PEjKvr-1C12Ru3pzZ-TMHDMNP4cUKL5g4KZyQXuNLDBFxfALF7adQsoSn9Nv7s2U3Zw1guqC8EDCzJ2SP1wmC8xnpkFkgzWxBpJ2ZFDzL5nPwAGKGC8f3Ihm7KeSRfuDHK5YstiGsL-6JKNheQHPj1ayoyyscPPiheaIBzbAQxfYMWVkYWmQAuWYJHzWbc30?purpose=inline)

Level 1 — Conversational talking head

Lip-sync, blinking, expressions, natural idle behavior, and head motion.

This is your MVP. All three are built around this level.

[Business meeting with whiteboard](https://images.openai.com/static-rsc-4/eFN-HN8Xx773w3swwFxUBmewYkRK60LBpdpzRGW1p3alH4uZZRatBBQ--khLDmOVm1Dc1b_STSjPqjOjsizt5mmlKVBgz5f2KfzdZlQLPvB_bvJUvWo5nwNUMTUCshgwGJndJIh0Ne9SkWQis5X5KeOEPUYmE3wOA0P6uW46cqLVr7Zo-LDYXpx9IvRK82Ex?purpose=inline)

Level 2 — Expressive body and gestures

The avatar points, nods, raises a hand, changes posture, or reacts with a gesture based on conversational context.

LiveTalking can stitch in prerecorded gestures. A fully controllable performance would need a more sophisticated animation system.

[Metahuman with AWS - DEV Community](https://images.openai.com/static-rsc-4/DlEcK1drWrNmwLwdKtEFedOIzdHTDuPD-AOE53LyFKHCLZlVBfIZaBP7rgJLirzzxdlOFdgye7fZ-YZjMePjNuNE0OFLx7BcqDS1zPQXFe92K1j-Ej7R8RqzoACVAtzdaUq_IuKc0_TTixw4VMfwSJTId4bSPNZlmGznS-MmSrE?purpose=inline)

Level 3 — Embodied agent

The avatar can stand up, walk through a room, manipulate objects, point at a screen, and respond to its surroundings.

None of the three gives you this out of the box. You would likely move to a rigged 3D character with a scene engine such as Unreal or Unity.

A full-body video is not the same thing as a controllable 3D body. LiveTalking's full-body stitching and Azure's full-body video avatars can display a person with arms and legs, but they don't inherently provide freely controllable skeletal movement or object interactions.

## 4. Engineering feasibility, latency, and deployment

| Dimension                       | OpenSpline                  | LiveTalking                           | Azure Voice Live                                  |
| ------------------------------- | --------------------------- | ------------------------------------- | ------------------------------------------------- |
| MVP integration effort          | Medium                      | Medium–high                           | Low                                               |
| Visual output                   | Generated talking head      | Video-based with generated lip motion | Photorealistic managed avatar                     |
| GPU management                  | You                         | You                                   | Microsoft                                         |
| Streaming integration           | WebRTC / LiveKit            | WebRTC built in                       | WebRTC built in                                   |
| Real-time performance risk      | Significant at high quality | Depends on selected engine            | Managed, still subject to service/network latency |
| Avatar customizability          | High for portraits          | High for prepared video               | Presets; custom creation restricted               |
| Vendor lock-in                  | Low                         | Low                                   | Higher                                            |
| Cloud operating burden          | High                        | High                                  | Low                                               |
| Long-term animation flexibility | Limited to generated video  | Better for scripted video gestures    | Limited by managed avatar controls                |

Two performance findings are especially relevant:

OpenSpline: Its maintainers report about 64 FPS in low-quality and 8 FPS in high-quality A100 tests. This is a major caution: the high-quality mode in that test is not fast enough for a smooth 25 FPS live video. Performance and image quality must be tested together.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



LiveTalking: Its maintainers report 42 FPS for MuseTalk on an RTX 3080 Ti and recommend that GPU or better for that engine. These are their test numbers, not a controlled cross-product comparison.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



Azure: Microsoft's avatar documentation lists 25 FPS video output. Video avatars support 1080p and higher, while VASA photo avatars use 512×512 output. VASA-2, currently in preview, adds upper-body framing and more controlled natural expressions, but it is not a freely controllable 3D body.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://learn.microsoft.com\&sz=32)

Microsoft Learn



For Azure deployment:

- OpenSpline and LiveTalking can run their inference on an Azure GPU VM; keep the web frontend, session API, and database on regular managed services.

- Azure Container Apps now offers serverless NVIDIA GPUs in supported configurations, but you must confirm regional availability, quotas, CUDA compatibility, startup latency, and WebRTC networking.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://learn.microsoft.com\&sz=32)

  Microsoft Learn

- Voice Live removes the self-hosted inference burden. Azure's current service availability table lists real-time avatar support in `centralindia`, although your chosen model and account permissions must also be checked.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://learn.microsoft.com\&sz=32)

  Microsoft Learn



## 5. Cost and licensing considerations

|                  | OpenSpline                         | LiveTalking                                   | Azure Voice Live                                        |
| ---------------- | ---------------------------------- | --------------------------------------------- | ------------------------------------------------------- |
| Software         | Open-source runtime                | Open-source community edition                 | Commercial API                                          |
| Hosting cost     | Continuous GPU use during sessions | GPU for lip-sync inference                    | Usage-based billing                                     |
| Scaling cost     | More GPU workers                   | More inference capacity                       | Service usage and quotas                                |
| Cost when idle   | VM still billed unless stopped     | VM still billed unless stopped                | No active avatar session charge after proper disconnect |
| Licensing caveat | Model weights have separate terms  | Check model licenses and watermark conditions | Microsoft service and custom-avatar terms               |

OpenSpline's main repository is MIT licensed, while bundled inference components and model weights retain separate terms. LiveTalking describes its community edition as Apache 2.0 and documents a watermark requirement for certain published videos.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub+1



Azure Voice Live's avatar output is charged separately under real-time avatar usage, in addition to applicable voice/model charges. I wouldn't estimate a reliable monthly budget without the planned session minutes and deployment SKU.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://azure.microsoft.com\&sz=32)

Microsoft Azure



For a student team, a GPU VM running throughout development can become a bigger cost risk than API calls.

## 6. My recommended architecture

I would keep the voice/conversation logic independent of the avatar system. That's important because you're likely to change rendering technology as the product develops.

Meeting UI — Next.js

Mic · Camera · Avatar · Transcript · Screen share

Real-time conversation layer

LiveKit / Pipecat or Azure Voice Live

VAD · Turn detection · LLM · Speech · Tool calls

Avatar renderer

OpenSpline / LiveTalking / Azure

Audio → Face / Video

Agent tools & state

Memory · RAG · Vision · Actions

Reasoning → Commands

Future embodiment layer

Gesture controller · 3D animation · Scene engine

For example, your LLM could eventually output a structured command such as:

```
{
  "speech": "Look at this diagram.",
  "animation": {
    "gesture": "point_right",
    "expression": "curious"
  },
  "action": {
    "type": "highlight",
    "target": "diagram_1"
  }
}
```

That's a proposed application-level contract, not a built-in OpenSpline, LiveTalking, or Azure API response. Initially, the renderer can ignore the animation command and simply talk. Later, a 3D engine could interpret the gesture and expression while the application performs the UI action.

For this future 3D phase, NVIDIA's [Audio2Face-3D](https://github.com/NVIDIA/Audio2Face-3D?utm_source=chatgpt.com) becomes interesting. Its open-source SDK and Unreal integration can produce facial animation data for rigged characters, including MetaHuman workflows. You would still need separate skeletal/body animation, locomotion, and scene interaction logic.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



Azure Voice Live also documents viseme and animation outputs, which may provide a route toward a separate 3D face renderer, subject to the selected voice and model's supported outputs.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://learn.microsoft.com\&sz=32)

Microsoft Learn+1



## 7. Recommended feature roadmap

MVPV1V2

MVP — Conversational Human

[ ] Natural face, blinking, idle movement and synchronized speech
[ ] Real-time two-way voice with barge-in and echo handling
[ ] Meeting-style UI with mute/unmute and connection status
[ ] Working cloud deployment with latency and reliability measurements

V1 — Intelligent Meeting Companion
[ ] Session history and contextual memory
[ ] Screen sharing with image-based understanding
[ ] Tool calls: open documents, search knowledge, control UI
[ ] Transcripts, captions and conversation summaries
[ ] Basic agent-selected expressions and gestures

V2 — Embodied Digital Human
[ ] Rigged 3D character with facial animation
[ ] Dynamic gestures and posture controlled by the agent
[ ] Interactive room or whiteboard environment
[ ] Object manipulation and contextual pointing
[ ] Spatial navigation, camera movement and environmental awareness

## Final recommendation

I would test Azure Voice Live first, then prototype OpenSpline as the open-source alternative.

The reason is specific to your MVP: you care more about natural human interaction than controlling a 3D body today. Azure's integrated speech-to-speech pipeline eliminates several synchronization and interruption problems, allowing you to concentrate on whether the actual experience feels human.

However, I wouldn't build the whole product tightly around the Azure avatar. Keep your UI, agent tools, memory, and future animation commands separate.

My ranking:

1. Azure Voice Live: Best first MVP and Azure cloud submission, assuming the available avatars meet your visual expectations.
2. OpenSpline: Best first self-hosted experiment, but verify that the visual quality you accept can actually run at real-time frame rates.
3. LiveTalking: Preferable when video-based custom characters and scripted body motions are more important than experimenting with newer talking-head generation.

The biggest product decision is not ultimately Azure versus open source. It's whether your long-term avatar is a generated video of a person or a fully controllable digital character.

For a meeting companion, generated video is a sensible starting point. If walking, physical gestures, and environmental manipulation become central requirements rather than optional enhancements, I'd plan a deliberate transition to a 3D character architecture rather than trying to extend a talking-head renderer into something it wasn't designed to do.
