import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../../api/client'
import type { Persona } from '../../api/types'
import { PlusIcon } from '../icons'
import { NewPersonaDialog } from './NewPersonaDialog'
import { PersonaCard } from './PersonaCard'

const POLL_MS = 2000

interface Props {
  onTalk(p: Persona): void
}

type Load = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ok' }

export function PersonaGallery({ onTalk }: Props) {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [notice, setNotice] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const refresh = useCallback(
    () =>
      api.listPersonas().then(
        (list) => {
          setPersonas(list)
          setLoad({ kind: 'ok' })
        },
        (err: unknown) => setLoad({ kind: 'error', message: err instanceof Error ? err.message : String(err) }),
      ),
    [],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  const upsert = useCallback((p: Persona) => {
    setPersonas((list) => {
      const i = list.findIndex((x) => x.id === p.id)
      if (i === -1) return [p, ...list]
      const next = list.slice()
      next[i] = p
      return next
    })
  }, [])

  // Poll personas whose avatar is still being provisioned.
  const provisioning = personas
    .filter((p) => p.avatar.status === 'provisioning')
    .map((p) => p.id)
    .join(',')
  useEffect(() => {
    if (!provisioning) return
    const ids = provisioning.split(',')
    const timer = setInterval(() => {
      for (const id of ids) {
        api
          .getPersona(id)
          .then(upsert)
          .catch((err: unknown) => {
            if (err instanceof ApiError && err.status === 404) {
              setPersonas((list) => list.filter((x) => x.id !== id))
            }
          })
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [provisioning, upsert])

  const remove = async (p: Persona) => {
    try {
      await api.deletePersona(p.id)
      setPersonas((list) => list.filter((x) => x.id !== p.id))
    } catch (err) {
      setNotice(`Could not delete ${p.name}: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const uploadPhoto = async (p: Persona, file: File) => {
    try {
      upsert(await api.uploadPhoto(p.id, file))
    } catch (err) {
      setNotice(`Photo upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="gallery-page">
      <header className="page-header">
        <div>
          <h1 className="brand">
            <span className="brand-mark" aria-hidden />
            Avatar Talk
          </h1>
          <p className="page-sub">Pick a persona and start a face-to-face voice conversation.</p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}>
          <PlusIcon width={18} height={18} /> New persona
        </button>
      </header>

      {notice && (
        <div className="banner error" role="alert">
          <span>{notice}</span>
          <button className="link" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      {load.kind === 'error' && (
        <div className="banner error" role="alert">
          <span>Could not load personas: {load.message}</span>
          <button
            className="link"
            onClick={() => {
              setLoad({ kind: 'loading' })
              void refresh()
            }}
          >
            Retry
          </button>
        </div>
      )}

      {load.kind === 'loading' && personas.length === 0 && (
        <div className="persona-grid" aria-busy>
          {[0, 1, 2].map((i) => (
            <div key={i} className="persona-card skeleton" />
          ))}
        </div>
      )}

      {load.kind === 'ok' && personas.length === 0 && (
        <div className="empty-state">
          <h2>No personas yet</h2>
          <p>Create one with a photo for a photoreal video avatar, or pick the 3D avatar to start right away.</p>
          <button className="btn primary" onClick={() => setCreating(true)}>
            <PlusIcon width={18} height={18} /> New persona
          </button>
        </div>
      )}

      {personas.length > 0 && (
        <div className="persona-grid">
          {personas.map((p) => (
            <PersonaCard key={p.id} persona={p} onTalk={onTalk} onDelete={remove} onUploadPhoto={uploadPhoto} />
          ))}
        </div>
      )}

      {creating && (
        <NewPersonaDialog
          onClose={() => setCreating(false)}
          onCreated={(p, warning) => {
            upsert(p)
            setCreating(false)
            if (warning) setNotice(warning)
          }}
        />
      )}
    </div>
  )
}
