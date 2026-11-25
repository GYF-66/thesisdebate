'use client'

import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Check, Circle, Square, RotateCcw } from 'lucide-react'
import { assistantHash } from '../lib/documentAssistance'
import { errorText, taskLabels, workspaceRequest, type AssistantSession, type AssistantTask, type TaskResult } from '../lib/workspaceAssistant'
import type { DocumentStyle } from '../lib/documentStudio'

type Props = { apiBase: string; documentId: string; content: string; disabled: boolean; prepare: () => Promise<string>; onAction: (action: string, result: TaskResult) => void; onRun: (runId: string) => Promise<void>; onStyle: (style: DocumentStyle) => void; onLocate: (quote: string) => void }
export default function WorkspaceAssistant(props: Props) {
  const { apiBase, documentId, content, disabled } = props
  const [session, setSession] = useState<AssistantSession | null>(null)
  const [message, setMessage] = useState(''), [error, setError] = useState('')
  const [working, setWorking] = useState(''), [currentHash, setCurrentHash] = useState('')
  const [loading, setLoading] = useState(true)
  const propsRef = useRef(props); propsRef.current = props
  const alive = useRef(true), operation = useRef(false)
  const pendingRequest = useRef<{ message: string; hash: string; id: string } | null>(null)
  const seenRuns = useRef(new Set<string>())
  const route = '/workspace-assistant/documents/' + encodeURIComponent(documentId)
  const hasRunning = session?.tasks.some(t => ['running', 'queued'].includes(t.status)) ?? false

  useEffect(() => { let stopped = false; void assistantHash(content).then(hash => { if (!stopped) setCurrentHash(hash) }); return () => { stopped = true } }, [content])
  useEffect(() => {
    alive.current = true
    let stopped = false, pending = false
    const controller = new AbortController()
    const refresh = async () => {
      if (pending || stopped) return
      pending = true
      try {
        const state = await workspaceRequest<AssistantSession>(apiBase, route, 'GET', undefined, controller.signal)
        if (stopped) return
        setSession(state); setLoading(false)
        for (const task of state.tasks) for (const step of task.steps) {
          if (step.kind === 'check' && step.status === 'completed' && step.result.run_id && !seenRuns.current.has(step.result.run_id)) {
            await propsRef.current.onRun(step.result.run_id)
            seenRuns.current.add(step.result.run_id)
          }
        }
      } catch (error) { if (!stopped) { setError(errorText(error)); setLoading(false) } }
      finally { pending = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), hasRunning ? 1000 : 4000)
    return () => { alive.current = false; stopped = true; controller.abort(); window.clearInterval(timer) }
  }, [apiBase, route, hasRunning])

  async function refresh() {
    const state = await workspaceRequest<AssistantSession>(apiBase, route)
    if (alive.current) setSession(state)
  }
  async function send(value = message) {
    if (operation.current || disabled || !value.trim()) return
    operation.current = true; setWorking('send'); setError('')
    try {
      const saved = await propsRef.current.prepare()
      if (!alive.current) return
      const hash = await assistantHash(saved)
      if (pendingRequest.current?.message !== value.trim() || pendingRequest.current?.hash !== hash) pendingRequest.current = { message: value.trim(), hash, id: crypto.randomUUID() }
      const task = await workspaceRequest<AssistantTask>(apiBase, route + '/tasks', 'POST', { message: value.trim(), expected_content_hash: hash, request_id: pendingRequest.current.id })
      pendingRequest.current = null
      if (alive.current) { setMessage(''); setSession(previous => ({ document_id: documentId, content_hash: task.source_hash, tasks: [...(previous?.tasks.filter(item => item.id !== task.id) ?? []), task] })) }
    } catch (error) { if (alive.current) setError(errorText(error)) }
    finally { operation.current = false; if (alive.current) setWorking('') }
  }
  async function control(task: AssistantTask, action: 'resume' | 'cancel') {
    if (operation.current) return
    operation.current = true; setWorking(task.id); setError('')
    try { if (action === 'resume') await propsRef.current.prepare(); if (!alive.current) return; await workspaceRequest(apiBase, `${route}/tasks/${task.id}/${action}`, 'POST'); await refresh() }
    catch (error) { if (alive.current) setError(errorText(error)) }
    finally { operation.current = false; if (alive.current) setWorking('') }
  }
  async function apply(task: AssistantTask, stepId: string) {
    if (operation.current) return
    operation.current = true; setWorking(stepId); setError('')
    try {
      await propsRef.current.prepare(); if (!alive.current) return
      const result = await workspaceRequest<{ style: DocumentStyle }>(apiBase, `${route}/tasks/${task.id}/steps/${stepId}/apply`, 'POST')
      if (alive.current) propsRef.current.onStyle(result.style)
      await refresh()
    } catch (error) { if (alive.current) setError(errorText(error)) }
    finally { operation.current = false; if (alive.current) setWorking('') }
  }

  const actionNames: Record<string, string> = { changes: '查看修改建议', agents: '查看内容审查', assistance: '打开导师意见与资料', literature: '查看文献结果', export: '打开导出', layout: '查看排版' }
  return <section className="workspace-assistant" aria-label="文档助手">
    <div className="panel-intro"><h2>这篇文档，下一步做什么？</h2><p>写下目标，我会按步骤调用检查、检索和排版工具。</p></div>
    <div className="assistant-shortcuts">
      <button disabled={disabled || !!working} onClick={() => void send('检查论文')}>检查论文</button>
      <button disabled={disabled} onClick={() => props.onAction('literature', {})}>查找文献</button>
      <button disabled={disabled} onClick={() => props.onAction('assistance', {})}>处理导师意见</button>
    </div>
    <form className="assistant-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      <label htmlFor="workspace-assistant-message">告诉我这次想完成的事</label>
      <textarea id="workspace-assistant-message" value={message} onChange={e => setMessage(e.target.value)} placeholder="例如：检查论文，然后导出" maxLength={2000} rows={3} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send() } }}/>
      <div><small>修改先核对，再应用 · Ctrl + Enter 发送</small><button className="primary-button" disabled={disabled || !!working || !message.trim()}><ArrowUp size={15}/>{working === 'send' ? '准备中…' : '开始'}</button></div>
    </form>
    {error && <p className="review-alert" role="alert">{error}<button onClick={() => void refresh().then(() => setError('')).catch(error => setError(errorText(error)))}>重新连接</button></p>}
    {loading && <p className="field-note" role="status">正在读取这篇文档的任务…</p>}
    {session?.tasks.length === 0 && <div className="assistant-first-note"><span>从一次检查开始</span><p>无需设置模型，就能检查格式、查文献和导出。需要检查论证与贡献时，再连接模型。</p></div>}
    <div className="assistant-task-list">{session?.tasks.slice().reverse().map(task => {
      const stale = task.stale || !!currentHash && currentHash !== task.source_hash
      return <article className="assistant-task" key={task.id}>
        <div className="task-heading"><h3>{task.message}</h3><span>{taskLabels[task.status] ?? task.status}</span></div>
        <time>{new Date(task.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
        {stale && <p className="task-stale">正文已变化。这份结果保留供参考，请基于当前正文重新发起任务。</p>}
        <ol className="task-steps">{task.steps.map(step => <li key={step.id}>
          <div className="task-step-heading">{step.status === 'completed' ? <Check size={14}/> : <Circle size={12}/>}<strong>{step.label}</strong><span>{taskLabels[step.status]}</span></div>
          {step.result.summary && <p>{step.result.summary}</p>}
          {step.result.evidence && <div className="task-evidence"><p>{step.result.evidence.answer}</p>{step.result.evidence.citations.map((citation, index) => <blockquote key={index}><p>{citation.quote}</p><small>{citation.source_name} · {citation.page ? `第 ${citation.page} 页` : `第 ${citation.paragraph} 段`}</small></blockquote>)}</div>}
          {step.error && <p className="step-error" role="status">{step.error}<button className="text-action" onClick={() => props.onAction('settings', {})}>模型设置</button></p>}
          {!!step.result.changes?.length && <dl className="style-proposal">{step.result.changes.map(change => <div key={change.field}><dt>{change.label}</dt><dd><del>{String(change.before)}</del><span> → </span><strong>{String(change.after)}</strong></dd></div>)}</dl>}
          {step.status === 'needs_confirmation' && <button className="primary-button" disabled={stale || disabled || !!working || task.status === 'cancelled'} onClick={() => void apply(task, step.id)}>确认这些排版修改</button>}
          {step.result.applied && <p className="inline-notice">排版已应用，正文保持原内容。</p>}
          {!!step.result.analysis?.findings.length && <details className="task-checklist"><summary>完整性与引用检查 · {step.result.analysis.findings.length} 项</summary>{step.result.analysis.findings.map((finding, index) => <div key={index}><strong>{finding.title}</strong>{finding.detail && <p>{finding.detail}</p>}{finding.quote && <button disabled={stale} onClick={() => props.onLocate(finding.quote)}>定位原文</button>}</div>)}</details>}
          {step.result.action && actionNames[step.result.action] && <button className="text-action" disabled={disabled || stale && ['changes', 'agents'].includes(step.result.action)} onClick={() => props.onAction(step.result.action!, step.result)}>{actionNames[step.result.action]} →</button>}
        </li>)}</ol>
        <div className="task-controls">{['failed', 'paused', 'cancelled'].includes(task.status) && <button className="ghost-button" disabled={stale || disabled || !!working} onClick={() => void control(task, 'resume')}><RotateCcw size={13}/>继续未完成步骤</button>}{['queued', 'running', 'needs_confirmation'].includes(task.status) && <button className="ghost-button" disabled={!!working} onClick={() => void control(task, 'cancel')}><Square size={12}/>取消任务</button>}{stale && <button className="ghost-button" disabled={disabled || !!working} onClick={() => void send(task.message)}>按当前正文重新运行</button>}</div>
      </article>
    })}</div>
  </section>
}
