import type { Persona, PersonaCreate, SessionStart } from './types'

const BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')
const API_KEY = import.meta.env.VITE_API_KEY ?? ''

/** True when requests need an Authorization header (so `<img src>` can't load photos directly). */
export const hasApiKey = API_KEY !== ''

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function authHeaders(): Record<string, string> {
  return API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {}
}

/** FastAPI errors are `{detail: string}` or `{detail: [{msg, loc}, ...]}` for validation. */
function describeError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { msg?: string; loc?: unknown[] }) =>
        [d.loc?.filter((x) => x !== 'body').join('.'), d.msg].filter(Boolean).join(': '),
      )
      .join('; ')
  }
  return `HTTP ${status}`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the API. Is it running?')
  }
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    throw new ApiError(res.status, describeError(res.status, body))
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const json = (body: unknown): RequestInit => ({
  body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json' },
})

export const api = {
  listPersonas: () => request<Persona[]>('/v1/personas'),
  getPersona: (id: string) => request<Persona>(`/v1/personas/${encodeURIComponent(id)}`),
  createPersona: (body: PersonaCreate) =>
    request<Persona>('/v1/personas', { method: 'POST', ...json(body) }),
  deletePersona: (id: string) =>
    request<void>(`/v1/personas/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadPhoto: (id: string, photo: File) => {
    const form = new FormData()
    form.append('photo', photo)
    return request<Persona>(`/v1/personas/${encodeURIComponent(id)}/photo`, {
      method: 'PUT',
      body: form,
    })
  },
  startSession: (personaId: string) =>
    request<SessionStart>('/v1/sessions', { method: 'POST', ...json({ persona_id: personaId }) }),
}

/** Direct photo URL; `photo_key` busts the cache when a new photo is uploaded. */
export function photoUrl(p: Persona): string | null {
  if (!p.avatar.photo_key) return null
  return `${BASE}/v1/personas/${encodeURIComponent(p.id)}/photo?v=${encodeURIComponent(p.avatar.photo_key)}`
}

/** Fetch the photo with auth headers (used when an API key is configured). */
export async function fetchPhotoBlob(p: Persona, signal?: AbortSignal): Promise<Blob> {
  const url = photoUrl(p)
  if (!url) throw new Error('no photo')
  const res = await fetch(url, { headers: authHeaders(), signal })
  if (!res.ok) throw new ApiError(res.status, `photo HTTP ${res.status}`)
  return res.blob()
}

/** User-facing message for a failed session start. */
export function sessionErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return `This persona's avatar isn't ready yet (${err.message}).`
    if (err.status === 429) return `The daily usage limit has been reached. Try again tomorrow. (${err.message})`
    if (err.status === 503) return `The call service is unavailable right now (${err.message}).`
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
