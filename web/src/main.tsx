import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// No <StrictMode>: its dev double-mount would start two sessions (two agent
// dispatches) and build two WebGL scenes. Effects still clean up on remount.
createRoot(document.getElementById('root')!).render(<App />)
