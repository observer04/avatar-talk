# Control-plane API (services/api)

Base URL in dev is `http://localhost:8000`. Every `/v1` route requires `Authorization: Bearer <API_KEY>` when `API_KEYS` is set (comma-separated). In dev with no keys configured, the API is open. JSON uses the `avatar_core.schemas` models.

## Personas

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/v1/personas` | — | `Persona[]` |
| POST | `/v1/personas` | JSON `PersonaCreate` | `Persona` |
| GET | `/v1/personas/{id}` | — | `Persona` |
| PATCH | `/v1/personas/{id}` | JSON (partial `PersonaCreate`) | `Persona` |
| DELETE | `/v1/personas/{id}` | — | 204 |
| PUT | `/v1/personas/{id}/photo` | multipart `photo` (jpg/png/webp, at most 10 MB) | `Persona` (avatar.status becomes `provisioning` or `ready`) |
| GET | `/v1/personas/{id}/photo` | — | image bytes (the normalised 512×512 face crop) |

`PersonaCreate` fields:

```json
{
  "name": "Ava",
  "system_prompt": "optional; default persona prompt when empty",
  "greeting": "optional instruction for the first message",
  "voice": {"provider": "cartesia", "model": "sonic-3", "voice_id": "optional"},
  "llm": {"provider": "groq", "model": "openai/gpt-oss-120b"},
  "avatar_backend": "simli | flashhead | browser3d | none",
  "tools": ["basics", "web_search"]
}
```

Avatar provisioning runs after the photo upload:
- **`simli`:** the photo is sent to Simli to create a face. The status goes `provisioning` → `ready` with `avatar.simli_face_id`, or `failed` with `avatar.error`.
- **`flashhead`:** the photo is the only input, so the status is `ready` as soon as it's stored.
- **`browser3d` / `none`:** no photo is needed; the status is `not_required`.

Clients poll `GET /v1/personas/{id}` until `avatar.status` is `ready`.

## Sessions

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/v1/sessions` | `{"persona_id": "per_..."}` | `SessionStart` |
| GET | `/v1/sessions/{id}` | — | the session row, including the final report (latency, usage, cost) when it has ended |

`SessionStart`:

```json
{
  "session_id": "ses_...",
  "room": "room_...",
  "livekit_url": "wss://...livekit.cloud",
  "token": "<participant JWT, 15 min>",
  "persona": { "...": "Persona" }
}
```

The API creates the room and dispatches the agent (`agent_name = "avatar-agent"`) with `SessionMetadata {session_id, persona_id}`. The browser joins with `token`. A session returns 409 if the persona's avatar isn't `ready`, and 429 when the daily spend cap is reached.

## Internal (agent → API)

These need `Authorization: Bearer <INTERNAL_API_TOKEN>`.

| Method | Path | Body |
| --- | --- | --- |
| GET | `/internal/personas/{id}` | — → `Persona` |
| POST | `/internal/sessions/{id}/report` | the session summary from `agent/observability.py` |

## Health

`GET /health` returns `{"status": "ok", "db": true, "storage": true}`.

## Room conventions (browser ⇄ agent)

- **Agent audio:** the agent, or the avatar participant when a video avatar is active, publishes the audio. Photoreal avatars also publish a video track from the avatar participant (identity `simli-avatar-agent` or `flashhead-avatar`).
- **Transcripts:** the standard LiveKit `lk.transcription` text streams.
- **Browser3d cues:** a text stream on topic `avatar.cue`, carrying JSON `{"mood": "happy"}` or `{"gesture": "thumbup"}`.
