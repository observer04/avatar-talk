import { useEffect, useRef } from 'react'
import type { AvatarRenderer } from '../avatar/AvatarRenderer'

interface Props {
  renderer: AvatarRenderer
  onReady(): void
  onError(message: string): void
  botSpeaking: boolean
  userSpeaking: boolean
}

/** Stage that hosts whichever AvatarRenderer is active. */
export function CallView({ renderer, onReady, onError, botSpeaking, userSpeaking }: Props) {
  const stageRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    let cancelled = false
    renderer
      .mount(el)
      .then(() => !cancelled && onReady())
      .catch((err: unknown) => !cancelled && onError(`Avatar failed to load: ${String(err)}`))
    return () => {
      cancelled = true
      renderer.dispose()
      el.replaceChildren()
    }
    // Mount once per renderer instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderer])

  return (
    <section className="stage">
      <div ref={stageRef} className="avatar-stage" />
      <div className="stage-badges">
        {userSpeaking && <span className="badge listening">Listening</span>}
        {botSpeaking && <span className="badge speaking">Speaking</span>}
      </div>
    </section>
  )
}
