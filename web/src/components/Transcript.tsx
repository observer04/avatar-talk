import { useEffect, useRef } from 'react'
import { entryText, type Entry } from '../state/transcript'

export function Transcript({ entries }: { entries: Entry[] }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [entries])

  return (
    <aside className="transcript" aria-label="Transcript">
      <h2>Transcript</h2>
      <div className="transcript-list">
        {entries.length === 0 && <p className="muted">Connect and say hello.</p>}
        {entries.map((e) => {
          const text = entryText(e)
          if (!text) return null
          const interim = e.role === 'user' && e.interim && !e.committed
          return (
            <div key={e.id} className={`bubble ${e.role}${interim ? ' interim' : ''}`}>
              <span className="who">{e.role === 'user' ? 'You' : 'Avatar'}</span>
              <p>{text}</p>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>
    </aside>
  )
}
