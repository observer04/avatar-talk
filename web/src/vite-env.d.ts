/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL (e.g. https://api.example.com). Empty = same origin (the Vite proxy in dev). */
  readonly VITE_API_URL?: string
  /** Client API key, sent as `Authorization: Bearer <key>` when set. */
  readonly VITE_API_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
