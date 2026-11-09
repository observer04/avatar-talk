#!/usr/bin/env node
// Stand-in for the control-plane API (docs/api.md) so the web app can be built
// and tested before services/api is ready. Personas live in memory; sessions are
// real: the room is created on LiveKit, a participant token is minted, and the
// `avatar-agent` worker is dispatched with an inline persona.
//
//   npm run mock                      # API on :8000 (what the Vite proxy expects)
//   MOCK_PORT=8010 npm run mock       # elsewhere; then API_PROXY_TARGET=http://localhost:8010 npm run dev
//
// Env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET (also read from ../.env).
// MOCK_DISPATCH=0 skips the agent dispatch. Photoreal personas are dispatched to
// the agent as backend "none" (no Simli/GPU spend) unless MOCK_REAL_AVATARS=1.
// Dev helper: GET /v1/_mock/token?room=..&identity=.. mints a token so
// mock/publisher.html can join as a fake video avatar.

import { randomBytes } from 'node:crypto'
import { createServer } from 'node:http'
import { AccessToken, AgentDispatchClient, RoomServiceClient } from 'livekit-server-sdk'

try {
  process.loadEnvFile(new URL('../../.env', import.meta.url))
} catch {
  /* optional */
}

const PORT = Number(process.env.MOCK_PORT ?? 8000)
const { LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET } = process.env
const DISPATCH = process.env.MOCK_DISPATCH !== '0'
const REAL_AVATARS = process.env.MOCK_REAL_AVATARS === '1'
const AGENT_NAME = 'avatar-agent'
const PROVISION_MS = 5000

const httpUrl = LIVEKIT_URL?.replace(/^ws/, 'http')
const rooms = httpUrl ? new RoomServiceClient(httpUrl, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) : null
const dispatch = httpUrl ? new AgentDispatchClient(httpUrl, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) : null

const id = (prefix) => `${prefix}_${randomBytes(6).toString('hex')}`

function persona(name, backend, extra = {}) {
  return {
    id: id('per'),
    name,
    system_prompt: '',
    greeting: 'Greet the person warmly and briefly introduce yourself.',
    stt: { provider: 'deepgram', model: 'flux-general-en', language: 'en' },
    llm: { provider: 'groq', model: 'openai/gpt-oss-120b', temperature: 0.7, fallbacks: [] },
    voice: { provider: 'cartesia', model: 'sonic-3', voice_id: null, speed: null, language: 'en' },
    avatar: { backend, status: 'not_required', photo_key: null, simli_face_id: null, error: null },
    tools: ['basics', 'web_search'],
    max_session_minutes: 15,
    created_at: new Date(Date.now() + personas.size).toISOString(),
    ...extra,
  }
}

/** A generated face-ish SVG so seeded photoreal personas have a "photo". */
function svgPortrait(hue, initials) {
  return Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><radialGradient id="g" cx="35%" cy="25%" r="90%"><stop offset="0" stop-color="hsl(${hue} 70% 70%)"/><stop offset="1" stop-color="hsl(${(hue + 60) % 360} 55% 22%)"/></radialGradient></defs>
<rect width="512" height="512" fill="url(#g)"/>
<circle cx="256" cy="210" r="96" fill="hsl(${hue} 30% 85%)" opacity=".9"/>
<path d="M96 512c10-110 80-170 160-170s150 60 160 170z" fill="hsl(${hue} 30% 85%)" opacity=".9"/>
<text x="256" y="236" font-family="sans-serif" font-size="72" font-weight="700" text-anchor="middle" fill="hsl(${hue} 50% 30%)">${initials}</text>
</svg>`)
}

const personas = new Map()
const photos = new Map() // photo_key -> { type, data }

function seed() {
  const add = (p, photo, avatar = {}) => {
    if (photo) {
      const key = `photos/${p.id}.svg`
      photos.set(key, { type: 'image/svg+xml', data: photo })
      p.avatar.photo_key = key
    }
    Object.assign(p.avatar, avatar)
    personas.set(p.id, p)
  }
  add(persona('Iris', 'simli'), svgPortrait(40, 'IR'), {
    status: 'failed',
    error: 'Simli could not detect a face in the photo.',
  })
  add(persona('Theo', 'flashhead'), svgPortrait(200, 'TH'), { status: 'ready' })
  add(persona('Mila', 'simli'), svgPortrait(330, 'MI'), { status: 'ready', simli_face_id: 'face_mock' })
  add(persona('Nova', 'none'))
  add(persona('Ava', 'browser3d'))
}
seed()

const PHOTO_BACKENDS = ['simli', 'flashhead']
const BACKENDS = ['simli', 'flashhead', 'browser3d', 'none']

function initialStatus(backend, photoKey) {
  if (!PHOTO_BACKENDS.includes(backend)) return 'not_required'
  if (backend === 'flashhead' && photoKey) return 'ready'
  return 'pending'
}

// ---------- HTTP plumbing ----------

function send(res, status, body, headers = {}) {
  if (body === undefined) {
    res.writeHead(status, headers).end()
    return
  }
  if (Buffer.isBuffer(body)) {
    res.writeHead(status, headers).end(body)
    return
  }
  res.writeHead(status, { 'content-type': 'application/json', ...headers }).end(JSON.stringify(body))
}
const fail = (res, status, detail) => send(res, status, { detail })

async function readBody(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  return Buffer.concat(chunks)
}

async function readJson(req) {
  const raw = await readBody(req)
  try {
    return raw.length ? JSON.parse(raw.toString('utf8')) : {}
  } catch {
    return null
  }
}

async function readForm(req) {
  // Let undici parse multipart for us.
  const body = await readBody(req)
  return new Request('http://mock/', { method: 'POST', headers: { 'content-type': req.headers['content-type'] ?? '' }, body }).formData()
}

// ---------- Handlers ----------

async function createPersona(req, res) {
  const body = await readJson(req)
  if (!body) return fail(res, 422, 'invalid JSON')
  const backend = body.avatar_backend ?? 'simli'
  if (!BACKENDS.includes(backend)) return fail(res, 422, [{ loc: ['body', 'avatar_backend'], msg: 'invalid backend' }])
  const p = persona(body.name?.trim() || 'Ava', backend)
  if (body.system_prompt) p.system_prompt = body.system_prompt
  if (body.greeting !== undefined) p.greeting = body.greeting
  if (body.voice) p.voice = { ...p.voice, ...body.voice }
  if (body.tools) p.tools = body.tools
  p.avatar.status = initialStatus(backend, null)
  personas.set(p.id, p)
  send(res, 201, p)
}

async function uploadPhoto(req, res, p) {
  let file
  try {
    file = (await readForm(req)).get('photo')
  } catch {
    return fail(res, 422, 'expected multipart form with a "photo" file')
  }
  if (!file || typeof file === 'string') return fail(res, 422, [{ loc: ['body', 'photo'], msg: 'Field required' }])
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return fail(res, 422, 'photo must be jpg, png or webp')
  if (file.size > 10 * 1024 * 1024) return fail(res, 413, 'photo larger than 10 MB')
  const key = `photos/${p.id}-${Date.now()}`
  photos.set(key, { type: file.type, data: Buffer.from(await file.arrayBuffer()) })
  p.avatar.photo_key = key
  p.avatar.error = null
  if (p.avatar.backend === 'simli') {
    p.avatar.status = 'provisioning'
    setTimeout(() => {
      if (!personas.has(p.id) || p.avatar.photo_key !== key) return
      if (/fail/i.test(p.name)) {
        Object.assign(p.avatar, { status: 'failed', error: 'Simli rejected the photo (mock: name contains "fail").' })
      } else {
        Object.assign(p.avatar, { status: 'ready', simli_face_id: id('face') })
      }
    }, PROVISION_MS)
  } else {
    p.avatar.status = initialStatus(p.avatar.backend, key)
  }
  send(res, 200, p)
}

async function startSession(req, res) {
  const body = await readJson(req)
  const p = body && personas.get(body.persona_id)
  if (!p) return fail(res, 404, 'persona not found')
  const needed = PHOTO_BACKENDS.includes(p.avatar.backend) ? 'ready' : 'not_required'
  if (p.avatar.status !== needed) {
    return fail(res, 409, `avatar not ready (status=${p.avatar.status}${p.avatar.error ? `: ${p.avatar.error}` : ''})`)
  }
  if (!rooms) return fail(res, 503, 'LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not set')

  const sessionId = id('ses')
  const room = `room_${randomBytes(6).toString('hex')}`
  const agentBackend = PHOTO_BACKENDS.includes(p.avatar.backend) && !REAL_AVATARS ? 'none' : p.avatar.backend
  const metadata = {
    session_id: sessionId,
    persona: {
      name: p.name,
      ...(p.system_prompt ? { system_prompt: p.system_prompt } : {}),
      ...(p.voice.voice_id ? { voice: { voice_id: p.voice.voice_id } } : {}),
      avatar: { backend: agentBackend },
    },
  }
  try {
    await rooms.createRoom({ name: room, emptyTimeout: 60, departureTimeout: 20 })
    if (DISPATCH) await dispatch.createDispatch(room, AGENT_NAME, { metadata: JSON.stringify(metadata) })
  } catch (err) {
    return fail(res, 503, `LiveKit error: ${err.message}`)
  }
  const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity: `user_${randomBytes(4).toString('hex')}`,
    name: 'You',
    ttl: '15m',
  })
  at.addGrant({ roomJoin: true, room, canPublish: true, canSubscribe: true, canPublishData: true })
  console.log(`[mock] session ${sessionId} room=${room} persona=${p.name} agent_backend=${agentBackend} dispatch=${DISPATCH}`)
  send(res, 201, { session_id: sessionId, room, livekit_url: LIVEKIT_URL, token: await at.toJwt(), persona: p })
}

async function mockToken(url, res) {
  const room = url.searchParams.get('room')
  if (!room || !LIVEKIT_API_KEY) return fail(res, 400, 'room required (and LiveKit env)')
  const identity = url.searchParams.get('identity') ?? 'flashhead-avatar'
  const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, { identity, name: identity, ttl: '15m' })
  at.addGrant({ roomJoin: true, room, canPublish: true, canSubscribe: true })
  send(res, 200, { token: await at.toJwt(), livekit_url: LIVEKIT_URL })
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://localhost')
  const parts = url.pathname.split('/').filter(Boolean)
  try {
    if (url.pathname === '/health') return send(res, 200, { status: 'ok', db: true, storage: true })
    if (parts[0] !== 'v1') return fail(res, 404, 'Not Found')
    if (parts[1] === '_mock' && parts[2] === 'token') return await mockToken(url, res)

    if (parts[1] === 'sessions' && parts.length === 2 && req.method === 'POST') return await startSession(req, res)

    if (parts[1] === 'personas') {
      if (parts.length === 2) {
        if (req.method === 'GET') {
          return send(res, 200, [...personas.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)))
        }
        if (req.method === 'POST') return await createPersona(req, res)
      }
      const p = personas.get(parts[2])
      if (!p) return fail(res, 404, 'persona not found')
      if (parts.length === 3) {
        if (req.method === 'GET') return send(res, 200, p)
        if (req.method === 'DELETE') {
          personas.delete(p.id)
          return send(res, 204)
        }
        if (req.method === 'PATCH') {
          const body = (await readJson(req)) ?? {}
          const { avatar_backend: backend, ...rest } = body
          Object.assign(p, rest)
          if (backend && backend !== p.avatar.backend) {
            p.avatar = { ...p.avatar, backend, status: initialStatus(backend, p.avatar.photo_key), simli_face_id: null, error: null }
          }
          return send(res, 200, p)
        }
      }
      if (parts[3] === 'photo' && parts.length === 4) {
        if (req.method === 'PUT') return await uploadPhoto(req, res, p)
        if (req.method === 'GET') {
          const photo = p.avatar.photo_key && photos.get(p.avatar.photo_key)
          if (!photo) return fail(res, 404, 'no photo')
          return send(res, 200, photo.data, { 'content-type': photo.type, 'cache-control': 'private, max-age=300' })
        }
      }
    }
    fail(res, 405, 'Method Not Allowed')
  } catch (err) {
    console.error('[mock] error', err)
    fail(res, 500, String(err?.message ?? err))
  }
})

server.listen(PORT, () => {
  console.log(`[mock] API on http://localhost:${PORT}  livekit=${LIVEKIT_URL ?? '(unset)'}  dispatch=${DISPATCH}`)
})
