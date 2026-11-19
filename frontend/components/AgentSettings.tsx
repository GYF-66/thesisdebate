'use client'

import { useEffect, useRef, useState } from 'react'
import { Check, Plus, Search, Settings, Trash2, X } from 'lucide-react'
import {
  activateAgentChannel, channelDraft, channelDraftChanged, createAgentChannel, deleteAgentChannel,
  filterAgentChannels, getAgentChannels, testAgentChannel, updateAgentChannel,
  type AgentChannel, type AgentChannelDraft, type AgentChannelFilter, type AgentChannelInput, type AgentChannelLibrary,
} from '../lib/agentSettings'
import './agent-channels.css'

type Props = { apiBase: string; open: boolean; onClose: () => void; onSaved?: () => void }
const NEW_CHANNEL = '__new_channel__'

export function AgentChannelList({ channels, selectedId, disabled, draftIds, onSelect }: {
  channels: AgentChannel[]; selectedId: string; disabled: boolean; draftIds: string[]; onSelect: (id: string) => void
}) {
  return <div className="channel-list" aria-label="已保存的模型渠道">
    {channels.length ? channels.map(channel => <button type="button" key={channel.id}
      className={'channel-list-item' + (selectedId === channel.id ? ' selected' : '')}
      aria-pressed={selectedId === channel.id} disabled={disabled} onClick={() => onSelect(channel.id)}>
      <span className="channel-list-title"><strong>{channel.name}</strong>{channel.active && <span className="channel-badge active">当前启用</span>}</span>
      <span className="channel-list-model">{channel.model || '尚未填写模型'}</span>
      <span className="channel-list-address" title={channel.base_url || undefined}>{channel.base_url || '尚未填写服务地址'}</span>
      <span className="channel-list-state">{channel.read_only ? '环境来源 · 只读' : channel.configured ? '已配置' : '待完善'}{draftIds.includes(channel.id) && ' · 有未保存修改'}</span>
    </button>) : <p className="channel-empty">没有匹配的渠道。</p>}
  </div>
}

export default function AgentSettings({ apiBase, open, onClose, onSaved }: Props) {
  const [library, setLibrary] = useState<AgentChannelLibrary | null>(null)
  const [selectedId, setSelectedId] = useState(NEW_CHANNEL)
  const [drafts, setDrafts] = useState<Record<string, AgentChannelDraft>>({})
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<AgentChannelFilter>('all')
  const [loading, setLoading] = useState(false)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [deletePending, setDeletePending] = useState(false)
  const dialogRef = useRef<HTMLDivElement>(null)
  const sessionRef = useRef(0)

  useEffect(() => {
    const session = ++sessionRef.current
    setDrafts({}); setError(''); setMessage(''); setDeletePending(false); setWorking('')
    if (!open) { setLibrary(null); return }
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const controller = new AbortController()
    setLoading(true); setLibrary(null); setQuery(''); setFilter('all')
    getAgentChannels(apiBase, controller.signal).then(value => {
      if (session !== sessionRef.current || controller.signal.aborted) return
      setLibrary(value); setSelectedId(value.active_channel_id ?? value.channels[0]?.id ?? NEW_CHANNEL)
    }).catch(failure => {
      if (session === sessionRef.current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : '无法读取渠道库。')
    }).finally(() => { if (session === sessionRef.current && !controller.signal.aborted) setLoading(false) })
    dialogRef.current?.focus()
    return () => { controller.abort(); sessionRef.current++; previous?.focus() }
  }, [open, apiBase])

  const selected = library?.channels.find(channel => channel.id === selectedId)
  const draft = drafts[selectedId] ?? channelDraft(selected)
  const dirty = channelDraftChanged(draft, selected)
  const busy = loading || !!working
  const readOnly = !!selected?.read_only
  const draftIds = Object.keys(drafts).filter(id => channelDraftChanged(drafts[id], library?.channels.find(channel => channel.id === id)))

  if (!open) return null

  function changeDraft(patch: Partial<AgentChannelDraft>) {
    setDrafts(previous => ({ ...previous, [selectedId]: { ...(previous[selectedId] ?? channelDraft(selected)), ...patch } }))
    setError(''); setMessage(''); setDeletePending(false)
  }
  function select(id: string) { setSelectedId(id); setError(''); setMessage(''); setDeletePending(false) }
  function input(): AgentChannelInput { return { ...draft, name: draft.name.trim(), expected_revision: library?.revision } }
  function forgetDraft(id: string) { setDrafts(previous => { const next = { ...previous }; delete next[id]; return next }) }

  async function refreshLibrary() {
    const session = sessionRef.current
    setLoading(true)
    try {
      const value = await getAgentChannels(apiBase)
      if (session !== sessionRef.current) return
      setLibrary(value)
      if (selectedId !== NEW_CHANNEL && !value.channels.some(channel => channel.id === selectedId)) setSelectedId(value.active_channel_id ?? value.channels[0]?.id ?? NEW_CHANNEL)
      setError(''); setMessage('渠道列表已刷新，未保存的表单仍保留。')
    } catch (failure) { if (session === sessionRef.current) setError(failure instanceof Error ? failure.message : '刷新渠道失败。') }
    finally { if (session === sessionRef.current) setLoading(false) }
  }

  async function save(enable: boolean) {
    if (!library || readOnly) return
    if (!draft.name.trim()) { setError('请填写渠道名称。'); return }
    const session = sessionRef.current
    const wasNew = selectedId === NEW_CHANNEL
    const previousIds = new Set(library.channels.map(channel => channel.id))
    let persisted = false
    setWorking(enable ? 'save-enable' : 'save'); setError(''); setMessage('')
    try {
      let saved = wasNew ? await createAgentChannel(apiBase, input()) : await updateAgentChannel(apiBase, selectedId, input())
      persisted = true
      if (session !== sessionRef.current) return
      const id = wasNew ? saved.channels.find(channel => !previousIds.has(channel.id))?.id : selectedId
      setLibrary(saved); forgetDraft(selectedId)
      if (!id) { setError('渠道已保存，请刷新列表后选择。'); return }
      setSelectedId(id); onSaved?.()
      if (enable) {
        setWorking('save-enable')
        saved = await activateAgentChannel(apiBase, id, saved.revision)
        if (session !== sessionRef.current) return
        setLibrary(saved)
      }
      setMessage(enable ? '渠道已保存并启用，后续任务使用此渠道；已开始的审查保留原配置。' : '渠道已保存。启用状态保持原选择；可单独启用已保存的配置。')
    } catch (failure) {
      if (session === sessionRef.current) setError((persisted ? '渠道已保存，启用失败：' : '') + (failure instanceof Error ? failure.message : '保存渠道失败。'))
    } finally { if (session === sessionRef.current) setWorking('') }
  }

  async function activate() {
    if (!library || !selected || dirty) return
    const session = sessionRef.current
    setWorking('activate'); setError(''); setMessage('')
    try {
      const value = await activateAgentChannel(apiBase, selected.id, library.revision)
      if (session !== sessionRef.current) return
      setLibrary(value); onSaved?.(); setMessage('已启用该渠道。后续任务使用新选择，已开始的审查保留原配置。')
    } catch (failure) { if (session === sessionRef.current) setError(failure instanceof Error ? failure.message : '启用渠道失败。') }
    finally { if (session === sessionRef.current) setWorking('') }
  }

  async function remove() {
    if (!library || !selected || readOnly) return
    const session = sessionRef.current
    setWorking('delete'); setError(''); setMessage('')
    try {
      const value = await deleteAgentChannel(apiBase, selected.id, library.revision)
      if (session !== sessionRef.current) return
      setLibrary(value); forgetDraft(selected.id); setSelectedId(value.active_channel_id ?? value.channels[0]?.id ?? NEW_CHANNEL)
      setDeletePending(false); onSaved?.(); setMessage('渠道已删除。已开始的审查仍使用自己的配置快照。')
    } catch (failure) { if (session === sessionRef.current) setError(failure instanceof Error ? failure.message : '删除渠道失败。') }
    finally { if (session === sessionRef.current) setWorking('') }
  }

  async function test() {
    if (!library) return
    const session = sessionRef.current
    setWorking('test'); setError(''); setMessage('')
    try {
      const result = await testAgentChannel(apiBase, selectedId === NEW_CHANNEL ? null : selectedId, input())
      if (session === sessionRef.current) setMessage(result.message + ' 测试不会保存表单或切换当前渠道。')
    } catch (failure) { if (session === sessionRef.current) setError(failure instanceof Error ? failure.message : '模型连接测试失败。') }
    finally { if (session === sessionRef.current) setWorking('') }
  }

  return <div className="settings-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !working) onClose() }}>
    <div className="settings-dialog channel-manager" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="agent-settings-title" onKeyDown={event => {
      if (event.key === 'Escape' && !working) { event.preventDefault(); onClose() }
      if (event.key === 'Tab') {
        const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]') || [])]
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first?.focus() }
      }
    }}>
      <header className="settings-heading"><div><h2 id="agent-settings-title"><Settings size={20}/>Agent 渠道库</h2><p>保存多个模型渠道，按任务需要切换。</p></div><button type="button" className="ghost-button" aria-label="关闭 Agent 设置" disabled={!!working} onClick={onClose}><X size={18}/></button></header>
      {library && <p className="channel-current" role="status">当前渠道：<strong>{library.channels.find(channel => channel.id === library.active_channel_id)?.name || '未启用'}</strong><span>{library.active.configured ? library.active.model : '后续模型任务暂停，启用完整渠道后可继续'}</span></p>}
      {error && <div className="settings-error" role="alert">{error}<button type="button" className="ghost-button channel-refresh" disabled={busy} onClick={() => void refreshLibrary()}>刷新列表</button></div>}
      {message && <p className="settings-message" role="status"><Check size={16}/>{message}</p>}
      {loading && !library ? <p role="status">正在读取本机渠道库…</p> : <div className="channel-manager-grid">
        <aside className="channel-library-pane">
          <button type="button" className="soft-button channel-add" disabled={busy || !library} onClick={() => select(NEW_CHANNEL)}><Plus size={16}/>新增渠道</button>
          <label className="channel-search" htmlFor="agent-channel-search"><Search size={15}/><input id="agent-channel-search" type="search" value={query} disabled={busy} onChange={event => setQuery(event.target.value)} placeholder="搜索名称、模型或地址"/></label>
          <div className="channel-filters" role="group" aria-label="筛选渠道状态">{([['all', '全部'], ['configured', '已配置'], ['incomplete', '待完善']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} disabled={busy} onClick={() => setFilter(value)}>{label}</button>)}</div>
          <AgentChannelList channels={filterAgentChannels(library?.channels ?? [], query, filter)} selectedId={selectedId} disabled={busy} draftIds={draftIds} onSelect={select}/>
        </aside>
        <section className="channel-editor" aria-labelledby="channel-editor-heading">
          <div className="channel-editor-heading"><h3 id="channel-editor-heading">{selectedId === NEW_CHANNEL ? '新增渠道' : '渠道详情'}{dirty && <small>未保存</small>}</h3>{selected && !readOnly && <button type="button" className="ghost-button channel-delete" disabled={busy} onClick={() => setDeletePending(value => !value)}><Trash2 size={14}/>删除</button>}</div>
          {deletePending && selected && <div className="channel-delete-confirm" role="alert"><p>删除“{selected.name}”？{selected.active ? '该渠道当前启用，删除后将暂停后续模型任务。' : '此操作会移除该渠道的本机设置。'}</p><div><button type="button" className="ghost-button" disabled={busy} onClick={() => setDeletePending(false)}>取消</button><button type="button" className="ghost-button channel-delete" disabled={busy} onClick={() => void remove()}>{working === 'delete' ? '正在删除…' : '确认删除'}</button></div></div>}
          {readOnly && <p className="settings-description">此渠道来自进程环境配置，只读使用。可新增一个本机渠道，分别保存地址、模型和密钥。</p>}
          <form onSubmit={event => { event.preventDefault(); void save(false) }}>
            <label className="settings-field" htmlFor="agent-channel-name">渠道名称<input id="agent-channel-name" value={draft.name} maxLength={80} onChange={event => changeDraft({ name: event.target.value })} disabled={busy || readOnly || !library} placeholder="例如：日常润色、长文审查" autoComplete="off"/></label>
            <label className="settings-field" htmlFor="agent-base-url">服务地址<input id="agent-base-url" type="url" value={draft.base_url} maxLength={2000} onChange={event => changeDraft({ base_url: event.target.value })} disabled={busy || readOnly || !library} placeholder="https://服务地址/v1" autoComplete="off" spellCheck={false}/></label>
            <label className="settings-field" htmlFor="agent-model">模型名称<input id="agent-model" value={draft.model} maxLength={255} onChange={event => changeDraft({ model: event.target.value })} disabled={busy || readOnly || !library} placeholder="填写服务支持的准确模型名称" autoComplete="off" spellCheck={false}/></label>
            <label className="settings-field" htmlFor="agent-api-key">API 密钥<input id="agent-api-key" type="password" value={draft.api_key} maxLength={8192} onChange={event => changeDraft({ api_key: event.target.value })} disabled={busy || readOnly || draft.clear_api_key || !library} placeholder={selected?.has_api_key ? '留空保留本渠道已保存的密钥' : '填写此渠道独立使用的 API 密钥'} autoComplete="new-password" spellCheck={false}/></label>
            {selected?.has_api_key && !readOnly && <label className="settings-checkbox"><input type="checkbox" checked={draft.clear_api_key} disabled={busy} onChange={event => changeDraft({ clear_api_key: event.target.checked, api_key: '' })}/>清除此渠道的已保存密钥</label>}
            <p className="settings-description">密钥仅保存在本机，读取配置时不会返回。新增渠道不继承其他渠道密钥。连接测试只发送固定测试文本，不发送论文正文；已开始的审查保留原配置。</p>
            {selected && <p className="settings-status">{selected.active ? '当前启用 · ' : ''}{selected.configured ? '已配置' : selected.reason}{!selected.active && !dirty && selected.configured && ' · 启用后用于后续任务'}</p>}
            <div className="settings-actions channel-editor-actions">
              {dirty && <button type="button" className="ghost-button" disabled={busy} onClick={() => forgetDraft(selectedId)}>放弃修改</button>}
              <button type="button" className="ghost-button" disabled={busy || !library} onClick={() => void test()}>{working === 'test' ? '正在测试…' : '测试连接'}</button>
              {!readOnly && <><button type="submit" className="ghost-button" disabled={busy || !library}>{working === 'save' ? '正在保存…' : '保存'}</button><button type="button" className="primary-button" disabled={busy || !library} onClick={() => void save(true)}>{working === 'save-enable' ? '正在保存并启用…' : '保存并启用'}</button></>}
              {selected && !selected.active && !dirty && selected.configured && !readOnly && <button type="button" className="soft-button" disabled={busy} onClick={() => void activate()}>{working === 'activate' ? '正在启用…' : '启用已保存配置'}</button>}
            </div>
          </form>
        </section>
      </div>}
    </div>
  </div>
}
