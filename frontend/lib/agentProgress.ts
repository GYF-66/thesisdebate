export type AgentProgress = { phase: 'review' | 'challenge' | 'judgment'; round: number; completed_units: number; total_units: number | null; unit: 'window' | 'batch' | 'task'; reused_units: number; updated_at: string }
export type ProgressEvent = { id: string; type: string; created_at?: string; payload?: Record<string, unknown> }

// Keep execution metadata only; model outputs and request bodies are not logs.
export function normalizeProgressEvent(value: unknown): ProgressEvent | null {
  if (!value || typeof value !== 'object') return null
  const event = value as Record<string, unknown>
  if (event.id === undefined || typeof event.type !== 'string') return null
  const raw = event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {}
  const payload: Record<string, unknown> = {}
  for (const key of ['agent_id', 'status', 'round', 'window_index', 'batch_index', 'reused', 'decision', 'finding_id', 'error', 'started_at', 'completed_at', 'progress']) {
    if (raw[key] !== undefined) payload[key] = raw[key]
  }
  return { id: String(event.id), type: event.type, created_at: typeof event.created_at === 'string' ? event.created_at : undefined, payload }
}

export function mergeProgressEvents(current: ProgressEvent[], incoming: ProgressEvent[]): ProgressEvent[] {
  const merged = new Map(current.map(event => [event.id, event]))
  for (const event of incoming) merged.set(event.id, event)
  return [...merged.values()].sort((a, b) => Number(a.id) - Number(b.id))
}

const phases: Record<string, string> = { review: '分段审查', challenge: '质疑复核', judgment: '证据裁判' }
const units: Record<string, string> = { window: '段', batch: '批', task: '项' }
export const progressPhaseLabel = (phase?: string) => phases[phase ?? ''] ?? '等待执行'
export const progressUnitLabel = (unit?: string) => units[unit ?? ''] ?? '项'
