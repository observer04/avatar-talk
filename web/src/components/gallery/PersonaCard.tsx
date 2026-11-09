import { useEffect, useRef, useState } from 'react'
import { BACKEND_LABELS, PHOTO_BACKENDS, isCallable, type AvatarStatus, type Persona } from '../../api/types'
import { PersonaPortrait } from '../PersonaPortrait'
import { ChatIcon, TrashIcon, UploadIcon } from '../icons'

const STATUS_LABELS: Record<AvatarStatus, string> = {
  not_required: 'Ready',
  ready: 'Ready',
  pending: 'Needs photo',
  provisioning: 'Preparing avatar…',
  failed: 'Avatar failed',
}

interface Props {
  persona: Persona
  busy?: boolean
  onTalk(p: Persona): void
  onDelete(p: Persona): Promise<void>
  onUploadPhoto(p: Persona, file: File): Promise<void>
}

export function PersonaCard({ persona, busy, onTalk, onDelete, onUploadPhoto }: Props) {
  const { avatar } = persona
  const [confirming, setConfirming] = useState(false)
  const [working, setWorking] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // Drop the "Confirm delete?" state if the user wanders off.
  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => setConfirming(false), 4000)
    return () => clearTimeout(t)
  }, [confirming])

  const needsPhoto = PHOTO_BACKENDS.includes(avatar.backend)
  const canRetryPhoto = needsPhoto && (avatar.status === 'pending' || avatar.status === 'failed')
  const callable = isCallable(persona)

  const del = async () => {
    if (!confirming) return setConfirming(true)
    setWorking(true)
    try {
      await onDelete(persona)
    } finally {
      setWorking(false)
      setConfirming(false)
    }
  }

  const upload = async (file: File | undefined) => {
    if (!file) return
    setWorking(true)
    try {
      await onUploadPhoto(persona, file)
    } finally {
      setWorking(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <article className={`persona-card status-${avatar.status}`} data-persona-id={persona.id}>
      <button
        className="persona-card-media"
        onClick={() => callable && onTalk(persona)}
        disabled={!callable || busy}
        aria-label={`Talk to ${persona.name}`}
      >
        <PersonaPortrait persona={persona} />
        {avatar.status === 'provisioning' && <div className="media-shimmer" aria-hidden />}
        <span className="backend-badge">{BACKEND_LABELS[avatar.backend] ?? avatar.backend}</span>
      </button>
      <div className="persona-card-body">
        <div className="persona-card-title">
          <h3 title={persona.name}>{persona.name}</h3>
          <span className={`status-pill pill-${avatar.status}`}>
            {avatar.status === 'provisioning' && <span className="spinner" aria-hidden />}
            {STATUS_LABELS[avatar.status] ?? avatar.status}
          </span>
        </div>
        {avatar.status === 'failed' && avatar.error && (
          <p className="persona-card-error" role="status">
            {avatar.error}
          </p>
        )}
        {avatar.status === 'pending' && needsPhoto && (
          <p className="persona-card-hint">Upload a face photo to create the {BACKEND_LABELS[avatar.backend]} avatar.</p>
        )}
        <div className="persona-card-actions">
          <button className="btn primary sm" onClick={() => onTalk(persona)} disabled={!callable || busy || working}>
            <ChatIcon width={16} height={16} /> Talk
          </button>
          {canRetryPhoto && (
            <>
              <button className="btn ghost sm" onClick={() => fileRef.current?.click()} disabled={working}>
                <UploadIcon width={16} height={16} /> Photo
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                hidden
                onChange={(e) => void upload(e.target.files?.[0])}
              />
            </>
          )}
          <button
            className={`btn ghost sm icon-only ${confirming ? 'confirm-danger' : ''}`}
            onClick={() => void del()}
            disabled={working}
            aria-label={confirming ? `Confirm delete ${persona.name}` : `Delete ${persona.name}`}
            title={confirming ? 'Click again to delete' : 'Delete'}
          >
            <TrashIcon width={16} height={16} />
            {confirming && <span>Delete?</span>}
          </button>
        </div>
      </div>
    </article>
  )
}
