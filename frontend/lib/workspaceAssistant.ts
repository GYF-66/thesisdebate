import type { DocumentStyle } from './documentStudio'

export type LiteratureSource = { id: string; title: string; authors: string[]; year: number | null; doi: string | null; url: string; providers: string[]; text: string; text_level: 'metadata' | 'abstract' | 'full_text'; text_hash: string; retrieved_at: string; query: string }
export type LiteratureSearch = { id: string; query: string; year_from: number | null; year_to: number | null; page: number; results: LiteratureSource[]; has_more: boolean; warnings: string[]; status: string; retrieved_at: string }
export type TaskResult = { summary?: string; action?: string; run_id?: string; review_id?: string; changes_count?: number; search?: LiteratureSearch; proposed_style?: DocumentStyle; changes?: { field: string; label: string; before: unknown; after: unknown }[]; applied?: { style: DocumentStyle }; analysis?: { findings: { title: string; quote: string; detail?: string }[] }; evidence?: { answer: string; citations: { quote: string; source_name: string; paragraph: number; page: number | null }[] } }
export type TaskStep = { id: string; kind: string; label: string; status: string; error: string; result: TaskResult }
export type AssistantTask = { id: string; message: string; source_hash: string; status: string; stale: boolean; steps: TaskStep[]; created_at: string; planner: string }
export type AssistantSession = { document_id: string; content_hash: string; tasks: AssistantTask[] }

export async function workspaceRequest<T>(base: string, path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { method, signal: signal ?? AbortSignal.timeout(65000), headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : `请求未完成（${response.status}），请重试。`)
  return result as T
}

export const taskLabels: Record<string, string> = { queued: '等待执行', pending: '尚未开始', running: '进行中', completed: '已完成', needs_confirmation: '待确认', failed: '待重试', cancelled: '已取消', paused: '待续跑' }
export const textLevelLabels = { metadata: '仅题录', abstract: '含摘要', full_text: '含全文' }
export const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '连接未完成，已保存的结果仍在。请重试。'
