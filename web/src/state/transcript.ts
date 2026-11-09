import type { BotOutputData } from '@pipecat-ai/client-js'

/** One chat bubble. User text = committed finals + live interim. */
export interface UserEntry {
  id: number
  role: 'user'
  committed: string
  interim: string
}

interface BotSegment {
  text: string
  /** 'word' = being streamed word-by-word; 'full' = complete sentence text. */
  mode: 'word' | 'full'
}

/** Bot bubble: RTVI bot-output arrives per segment (sentence), possibly word-streamed. */
export interface BotEntry {
  id: number
  role: 'bot'
  order: string[]
  segments: Record<string, BotSegment>
}

export type Entry = UserEntry | BotEntry

let nextId = 1

export function entryText(e: Entry): string {
  if (e.role === 'user') return [e.committed, e.interim].filter(Boolean).join(' ')
  return e.order.map((k) => e.segments[k].text).join(' ')
}

export function addUserText(entries: Entry[], text: string, final: boolean): Entry[] {
  const last = entries[entries.length - 1]
  const base: UserEntry =
    last?.role === 'user' ? { ...last } : { id: nextId++, role: 'user', committed: '', interim: '' }
  if (final) {
    base.committed = [base.committed, text].filter(Boolean).join(' ')
    base.interim = ''
  } else {
    base.interim = text
  }
  return last?.role === 'user' ? [...entries.slice(0, -1), base] : [...entries, base]
}

let anonSegment = 0

export function addBotOutput(entries: Entry[], d: BotOutputData): Entry[] {
  // Skip text the bot generated but will not speak (e.g. cut off by barge-in).
  if (d.will_be_spoken === false) return entries
  const type = d.output_type ?? d.aggregated_by ?? d.text_type
  if (type === 'expression_sound') return entries

  const last = entries[entries.length - 1]
  const base: BotEntry =
    last?.role === 'bot'
      ? { ...last, order: [...last.order], segments: { ...last.segments } }
      : { id: nextId++, role: 'bot', order: [], segments: {} }

  const key = d.segment_id != null ? `s${d.segment_id}` : `a${anonSegment++}`
  const prev = base.segments[key]
  if (!prev) base.order.push(key)

  if (d.spoken_progress) {
    base.segments[key] = { text: d.spoken_progress.accumulated_text, mode: 'word' }
  } else if (type === 'word') {
    // Words after the full sentence for this segment add nothing new.
    if (prev?.mode !== 'full') {
      base.segments[key] = { text: [prev?.text, d.text].filter(Boolean).join(' '), mode: 'word' }
    }
  } else {
    base.segments[key] = { text: d.text, mode: 'full' }
  }

  return last?.role === 'bot' ? [...entries.slice(0, -1), base] : [...entries, base]
}
