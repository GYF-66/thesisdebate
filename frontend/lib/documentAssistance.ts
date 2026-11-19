export type DocumentKind = 'thesis' | 'course_report' | 'lab_report' | 'internship_report' | 'proposal' | 'generic'
export type AssistantFinding = { id: string; category: string; title: string; detail: string; quote: string; start: number | null; end: number | null; severity: string }
export type MentorTask = { id: string; comment: string; quote: string; source_quote: string; source: string; status: 'pending' | 'in_progress' | 'done' | 'skipped'; reply: string; version: number; stale: boolean; source_content_hash: string; location_status: string }
export type AssistantMaterial = { id: string; name: string; kind: string; characters: number; warnings: string[] }
export type ReferenceCheck = { id: string; reference: string; status: string; existence_status: string; claim_support_status: string; message: string; warnings: string[]; sources: string[]; candidates: { title: string; doi: string; url: string | null; source: string; matched: boolean }[] }
export type AssistantState = { document_id: string; content_hash: string; revision: number; document_kind: DocumentKind; required_sections: string[] | null; mentor_tasks: MentorTask[]; materials: AssistantMaterial[]; reference_checks: ReferenceCheck[] }
export type AssistantAnalysis = { content_hash: string; findings: AssistantFinding[]; references: { number: number; text: string }[]; submission_checklist: { id: string; title: string; status: string; detail: string }[]; defense_questions: { question: string; quote: string; reason: string }[]; limitations: string[] }
export type AssistantAnswer = { answer: string; model_used: boolean; citations: { material_id: string; source_name: string; page: number | null; paragraph: number; quote: string; matched_terms: string[] }[] }
export type AssistantGuard = { document_id: string; content: string; expected_content_hash: string }

export async function assistantHash(content: string) {
  if (!globalThis.crypto?.subtle) throw new Error('当前环境不支持安全内容校验，请从 localhost 或 HTTPS 打开应用。')
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content))
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('')
}

const path = (apiBase: string, route: string) => apiBase.replace(/\/$/, '') + '/document-assistance' + route
async function checked(response: Response) {
  if (response.ok) return response
  const body = await response.json().catch(() => null)
  throw new Error(typeof body?.detail === 'string' ? body.detail : `文档助手请求失败（${response.status}），请检查输入或本地服务。`)
}
export async function assistantRequest<T>(apiBase: string, route: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await checked(await fetch(path(apiBase, route), { method, cache: 'no-store', signal: signal ?? AbortSignal.timeout(25000), headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }))
  return response.json() as Promise<T>
}
export async function assistantUpload(apiBase: string, route: string, file: File, guard: AssistantGuard): Promise<AssistantState> {
  const query = new URLSearchParams({ document_id: guard.document_id, filename: file.name, expected_content_hash: guard.expected_content_hash })
  const response = await checked(await fetch(path(apiBase, route + '?' + query), { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' }, signal: AbortSignal.timeout(45000) }))
  return response.json() as Promise<AssistantState>
}
export async function assistantDownload(apiBase: string, documentId: string, hash: string, type: 'report.md' | 'mentor-replies.csv') {
  const query = new URLSearchParams({ expected_content_hash: hash })
  const response = await checked(await fetch(path(apiBase, '/documents/' + encodeURIComponent(documentId) + '/' + type + '?' + query), { cache: 'no-store', signal: AbortSignal.timeout(25000) }))
  return response.blob()
}
export function saveAssistantBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
