'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { Settings } from 'lucide-react'
import { activateAgentChannel, AGENT_SETTINGS_CHANGED, getAgentChannels, type AgentChannelLibrary } from '../lib/agentSettings'
import './agent-channels.css'

export type AgentChannelSwitcherProps = { apiBase: string; onManage: () => void; disabled?: boolean; className?: string }

export default function AgentChannelSwitcher({ apiBase, onManage, disabled = false, className = '' }: AgentChannelSwitcherProps) {
  const [library, setLibrary] = useState<AgentChannelLibrary | null>(null)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const mounted = useRef(false)
  const selectId = useId()

  useEffect(() => {
    mounted.current = true
    let controller: AbortController | null = null
    const refresh = () => {
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      setLoading(true)
      getAgentChannels(apiBase, requestController.signal).then(value => {
        if (mounted.current && !requestController.signal.aborted) { setLibrary(value); setError('') }
      }).catch(failure => {
        if (mounted.current && !requestController.signal.aborted) setError(failure instanceof Error ? failure.message : '渠道暂不可用。')
      }).finally(() => { if (mounted.current && !requestController.signal.aborted) setLoading(false) })
    }
    refresh()
    window.addEventListener(AGENT_SETTINGS_CHANGED, refresh)
    return () => { mounted.current = false; controller?.abort(); window.removeEventListener(AGENT_SETTINGS_CHANGED, refresh) }
  }, [apiBase])

  async function activate(id: string) {
    if (!library || !id || id === library.active_channel_id) return
    setWorking(true); setError(''); setMessage('')
    try {
      const value = await activateAgentChannel(apiBase, id, library.revision)
      if (!mounted.current) return
      setLibrary(value)
      setMessage('已切换至“' + (value.channels.find(channel => channel.id === id)?.name ?? '所选渠道') + '”，已开始的审查保留原渠道。')
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : '切换渠道失败，请刷新后重试。') }
    finally { if (mounted.current) setWorking(false) }
  }

  return <div className={'agent-channel-switcher ' + className}>
    <label htmlFor={selectId}>Agent 渠道</label>
    <select id={selectId} aria-label="当前 Agent 渠道" value={library?.active_channel_id ?? ''} disabled={disabled || loading || working || !library} onChange={event => void activate(event.target.value)}>
      <option value="" disabled>{loading && !library ? '正在读取…' : '未启用渠道'}</option>
      {library?.channels.map(channel => <option key={channel.id} value={channel.id} disabled={!channel.configured || channel.read_only && !channel.active}>{channel.name}{channel.read_only ? ' · 环境来源' : !channel.configured ? ' · 待完善' : ''}</option>)}
    </select>
    <button type="button" className="ghost-button" onClick={onManage} disabled={disabled || working} aria-label="管理 Agent 渠道"><Settings size={14}/>管理渠道</button>
    {working && <span className="channel-switch-status" role="status">正在切换…</span>}
    {error && <span className="channel-switch-error" role="alert">{error}</span>}
    {!error && message && <span className="channel-switch-status" role="status">{message}</span>}
  </div>
}
