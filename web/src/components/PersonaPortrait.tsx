import type { Persona } from '../api/types'
import { usePersonaPhoto } from '../hooks/usePersonaPhoto'

/** Stable hue per persona so placeholders differ but don't flicker. */
function hue(seed: string): number {
  let h = 0
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 360
  return h
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : name.slice(0, 2)).toUpperCase()
}

interface Props {
  persona: Persona
  className?: string
}

/** The persona photo, or a gradient monogram when there is none. */
export function PersonaPortrait({ persona, className = '' }: Props) {
  const src = usePersonaPhoto(persona)
  if (src) {
    return <img className={`portrait ${className}`} src={src} alt={persona.name} draggable={false} />
  }
  const h = hue(persona.id || persona.name)
  return (
    <div
      className={`portrait portrait-placeholder ${className}`}
      style={{
        background: `radial-gradient(circle at 30% 25%, hsl(${h} 70% 62%), hsl(${(h + 50) % 360} 60% 32%) 70%)`,
      }}
      role="img"
      aria-label={persona.name}
    >
      <span>{initials(persona.name)}</span>
    </div>
  )
}
