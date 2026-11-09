import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// No <StrictMode>: its dev double-mount would build two WebGL scenes and fetch
// the avatar GLB twice. The renderer is still safe against remounts.
createRoot(document.getElementById('root')!).render(<App />)
