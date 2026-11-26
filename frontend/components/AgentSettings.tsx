'use client'

import { useEffect, useRef, useState } from 'react'
import { Check, Settings, X } from 'lucide-react'
import { getAgentSettings, saveAgentSettings, testAgentSettings, type AgentSettingsInput, type AgentSettingsStatus } from '../lib/agentSettings'

type Props = { apiBase: string; open: boolean; onClose: () => void; onSaved?: () => void }

export default function AgentSettings({ apiBase, open, onClose, onSaved }: Props) {
  const [status, setStatus] = useState<AgentSettingsStatus | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [loading, setLoading] = useState(false)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const controller = new AbortController()
    setLoading(true); setStatus(null); setError(''); setMessage(''); setApiKey(''); setClearKey(false)
    getAgentSettings(apiBase, controller.signal).then(value => {
      setStatus(value); setBaseUrl(value.base_url || ''); setModel(value.model || '')
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : '无法读取模型设置。')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    dialogRef.current?.focus()
    return () => { controller.abort(); previous?.focus() }
  }, [open, apiBase])

  if (!open) return null
  const busy = loading || !!working
  const input = (): AgentSettingsInput => ({ base_url: baseUrl, model, api_key: apiKey, clear_api_key: clearKey })
  const changed = () => { setError(''); setMessage('') }

  async function save() {
    setWorking('save'); setError(''); setMessage('')
    try {
      const saved = await saveAgentSettings(apiBase, input())
      setStatus(saved); setApiKey(''); setClearKey(false)
      setMessage(saved.configured ? '设置已保存，后续 Agent 审查立即使用新配置。' : '设置已保存；补全配置后即可启动 Agent 审查。')
      window.dispatchEvent(new Event('agent-settings-changed'))
      onSaved?.()
    } catch (failure) { setError(failure instanceof Error ? failure.message : '保存模型设置失败。') }
    finally { setWorking('') }
  }

  async function test() {
    setWorking('test'); setError(''); setMessage('')
    try { setMessage((await testAgentSettings(apiBase, input())).message + ' 当前表单尚未保存的更改仍需点击保存。') }
    catch (failure) { setError(failure instanceof Error ? failure.message : '模型连接测试失败。') }
    finally { setWorking('') }
  }

  return <div className="settings-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !working) onClose() }}>
    <div className="settings-dialog" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="agent-settings-title" onKeyDown={event => {
      if (event.key === 'Escape' && !working) { event.preventDefault(); onClose() }
      if (event.key === 'Tab') {
        const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') || [])]
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first?.focus() }
      }
    }}>
      <header className="settings-heading"><div><h2 id="agent-settings-title"><Settings size={20}/>模型渠道</h2><p>配置已有的兼容模型服务，无需重启工作台。</p></div><button type="button" className="ghost-button" aria-label="关闭模型渠道设置" disabled={!!working} onClick={onClose}><X size={18}/></button></header>
      {loading ? <p role="status">正在读取本机设置…</p> : <form onSubmit={event => { event.preventDefault(); void save() }}>
        <p className="settings-status">{status?.configured ? '模型服务已配置' : '模型服务未配置'}{status?.source === 'environment' && status.configured ? ' · 当前来自环境配置' : ''}</p>
        <label className="settings-field" htmlFor="agent-base-url">服务地址<input id="agent-base-url" type="url" value={baseUrl} onChange={event => { setBaseUrl(event.target.value); changed() }} disabled={busy} placeholder="https://服务地址/v1" autoComplete="off" spellCheck={false}/></label>
        <label className="settings-field" htmlFor="agent-model">模型名称<input id="agent-model" value={model} onChange={event => { setModel(event.target.value); changed() }} disabled={busy} placeholder="填写服务支持的准确模型名称" autoComplete="off" spellCheck={false}/></label>
        <label className="settings-field" htmlFor="agent-api-key">API 密钥<input id="agent-api-key" type="password" value={apiKey} onChange={event => { setApiKey(event.target.value); changed() }} disabled={busy || clearKey} placeholder={status?.has_api_key ? '已配置，留空保留现有密钥' : '填写 API 密钥'} autoComplete="new-password" spellCheck={false}/></label>
        {status?.has_api_key && <label className="settings-checkbox"><input type="checkbox" checked={clearKey} disabled={busy} onChange={event => { setClearKey(event.target.checked); setApiKey(''); changed() }}/>清除当前密钥，暂停模型调用</label>}
        <p className="settings-description">密钥只保存在本机，读取配置时不会返回。测试连接仅发送固定测试文本，不发送论文正文；已开始的审查保留原配置。</p>
        {error && <p className="settings-error" role="alert">{error}</p>}
        {message && <p className="settings-message" role="status"><Check size={16}/>{message}</p>}
        <div className="settings-actions"><button type="button" className="ghost-button" disabled={busy} onClick={() => { void test() }}>{working === 'test' ? '正在测试…' : '测试连接'}</button><button type="submit" className="primary-button" disabled={busy}>{working === 'save' ? '正在保存…' : '保存设置'}</button></div>
      </form>}
    </div>
  </div>
}
