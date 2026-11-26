export type AgentSettingsStatus = {
  configured: boolean
  base_url: string | null
  model: string | null
  has_api_key: boolean
  source: 'local' | 'environment'
  reason: string
}

export type AgentSettingsInput = { base_url: string; model: string; api_key?: string; clear_api_key: boolean }

async function request<T>(apiBase: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiBase + path, { cache: 'no-store', ...init, signal: init?.signal ?? AbortSignal.timeout(25000), headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : '模型设置请求失败，请检查本地服务。')
  return body as T
}

export const getAgentSettings = (apiBase: string, signal?: AbortSignal) => request<AgentSettingsStatus>(apiBase, '/agent-settings', { signal })
export const saveAgentSettings = (apiBase: string, input: AgentSettingsInput) => request<AgentSettingsStatus>(apiBase, '/agent-settings', { method: 'PUT', body: JSON.stringify(input) })
export const testAgentSettings = (apiBase: string, input: AgentSettingsInput) => request<{ ok: boolean; message: string }>(apiBase, '/agent-settings/test', { method: 'POST', body: JSON.stringify(input) })
