import { useEffect, useState } from 'react'
import { fetchPhotoBlob, hasApiKey, photoUrl } from '../api/client'
import type { Persona } from '../api/types'

/**
 * URL to show the persona photo. Without an API key the image URL is used
 * directly; with one, `<img>` can't send the Authorization header, so the photo
 * is fetched and exposed as an object URL.
 */
export function usePersonaPhoto(persona: Persona | null | undefined): string | null {
  const direct = persona ? photoUrl(persona) : null
  const [blobUrl, setBlobUrl] = useState<{ key: string; url: string } | null>(null)

  useEffect(() => {
    if (!hasApiKey || !persona || !direct) return
    const ctrl = new AbortController()
    let created: string | null = null
    fetchPhotoBlob(persona, ctrl.signal)
      .then((blob) => {
        created = URL.createObjectURL(blob)
        setBlobUrl({ key: direct, url: created })
      })
      .catch(() => {})
    return () => {
      ctrl.abort()
      if (created) URL.revokeObjectURL(created)
    }
    // `direct` encodes persona id + photo_key, which is all that matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direct])

  if (!direct) return null
  if (!hasApiKey) return direct
  return blobUrl?.key === direct ? blobUrl.url : null
}
