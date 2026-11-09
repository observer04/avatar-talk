import { Suspense, lazy, useCallback, useState } from 'react'
import type { Persona } from './api/types'
import { PersonaGallery } from './components/gallery/PersonaGallery'

// The call view pulls in livekit-client; the gallery doesn't need it.
const CallScreen = lazy(() => import('./components/call/CallScreen'))

/** `?debug=1` shows the call debug overlay (agent state, participants, video stats). */
const DEBUG = new URLSearchParams(location.search).get('debug') === '1'

export default function App() {
  const [call, setCall] = useState<{ persona: Persona; key: number } | null>(null)

  const exit = useCallback(() => setCall(null), [])
  const restart = useCallback(() => setCall((c) => (c ? { ...c, key: c.key + 1 } : c)), [])

  if (call) {
    return (
      <Suspense fallback={<div className="page-loading" aria-busy />}>
        <CallScreen key={call.key} persona={call.persona} debug={DEBUG} onExit={exit} onRestart={restart} />
      </Suspense>
    )
  }
  return <PersonaGallery onTalk={(persona) => setCall({ persona, key: 0 })} />
}
