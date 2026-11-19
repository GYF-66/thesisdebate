export type AgentSettingsStatus = {
  configured: boolean
  base_url: string | null
  model: string | null
  has_api_key: boolean
  source: 'local' | 'environment'
  reason: string
  channel_id?: string | null
  channel_name?: string | null
  config_revision?: number
}

export type AgentSettingsInput = { base_url: string; model: string; api_key?: string; clear_api_key: boolean }

export type AgentChannel = AgentSettingsStatus & {
  id: string
  name: string
  active: boolean
  config_revision: number
  read_only: boolean
}

export type AgentChannelLibrary = {
  revision: number
  active_channel_id: string | null
  channels: AgentChannel[]
  active: AgentSettingsStatus
}

export type AgentChannelInput = AgentSettingsInput & { name: string; expected_revision?: number }
export type AgentChannelFilter = 'all' | 'configured' | 'incomplete'
export type AgentChannelDraft = { name: string; base_url: string; model: string; api_key: string; clear_api_key: boolean }
export const AGENT_SETTINGS_CHANGED = 'agent-settings-changed'
export const AGENT_CHANNEL_CHANGED = 'agent-channel-changed'

export function channelDraft(channel?: AgentChannel | null): AgentChannelDraft {
  return { name: channel?.name ?? '', base_url: channel?.base_url ?? '', model: channel?.model ?? '', api_key: '', clear_api_key: false }
}

export function channelDraftChanged(draft: AgentChannelDraft, channel?: AgentChannel | null): boolean {
  const saved = channelDraft(channel)
  return draft.name !== saved.name || draft.base_url !== saved.base_url || draft.model !== saved.model || !!draft.api_key || draft.clear_api_key
}

export function filterAgentChannels(channels: AgentChannel[], query: string, filter: AgentChannelFilter): AgentChannel[] {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean)
  return channels.filter(channel => {
    if (filter === 'configured' && !channel.configured || filter === 'incomplete' && channel.configured) return false
    const searchable = [channel.name, channel.model ?? '', channel.base_url ?? ''].join(' ').toLocaleLowerCase()
    return terms.every(term => searchable.includes(term))
  })
}

export function notifyAgentChannelChange(library: AgentChannelLibrary): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(AGENT_SETTINGS_CHANGED))
  window.dispatchEvent(new CustomEvent(AGENT_CHANNEL_CHANGED, { detail: { channelId: library.active_channel_id, revision: library.revision } }))
}

async function request<T>(apiBase: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiBase + path, { cache: 'no-store', ...init, signal: init?.signal ?? AbortSignal.timeout(25000), headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : '模型设置请求失败，请检查本地服务。')
  return body as T
}

export const getAgentSettings = (apiBase: string, signal?: AbortSignal) => request<AgentSettingsStatus>(apiBase, '/agent-settings', { signal })
export const saveAgentSettings = (apiBase: string, input: AgentSettingsInput) => request<AgentSettingsStatus>(apiBase, '/agent-settings', { method: 'PUT', body: JSON.stringify(input) })
export const testAgentSettings = (apiBase: string, input: AgentSettingsInput) => request<{ ok: boolean; message: string }>(apiBase, '/agent-settings/test', { method: 'POST', body: JSON.stringify(input) })

const channelPath = (id: string) => '/agent-settings/channels/' + encodeURIComponent(id)
async function mutateChannels(apiBase: string, path: string, init: RequestInit): Promise<AgentChannelLibrary> {
  const library = await request<AgentChannelLibrary>(apiBase, path, init)
  notifyAgentChannelChange(library)
  return library
}

export const getAgentChannels = (apiBase: string, signal?: AbortSignal) => request<AgentChannelLibrary>(apiBase, '/agent-settings/channels', { signal })
export const createAgentChannel = (apiBase: string, input: AgentChannelInput) => mutateChannels(apiBase, '/agent-settings/channels', { method: 'POST', body: JSON.stringify(input) })
export const updateAgentChannel = (apiBase: string, id: string, input: AgentChannelInput) => mutateChannels(apiBase, channelPath(id), { method: 'PUT', body: JSON.stringify(input) })
export const deleteAgentChannel = (apiBase: string, id: string, expectedRevision: number) => mutateChannels(apiBase, channelPath(id) + '?expected_revision=' + expectedRevision, { method: 'DELETE' })
export const activateAgentChannel = (apiBase: string, id: string, expectedRevision: number) => mutateChannels(apiBase, channelPath(id) + '/activate', { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision }) })
export const testAgentChannel = (apiBase: string, id: string | null, input: AgentChannelInput) => request<{ ok: boolean; message: string }>(apiBase, id ? channelPath(id) + '/test' : '/agent-settings/channels/test', { method: 'POST', body: JSON.stringify(input) })
