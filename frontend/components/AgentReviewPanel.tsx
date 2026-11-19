'use client'

import { useEffect, useRef, useState } from 'react'
import { Check, FileUp, History, Play, RefreshCw, Square, X } from 'lucide-react'
import { TextDiff } from './RevisionDiff'
import AgentRoleProgress from './AgentRoleProgress'
import { mergeProgressEvents, normalizeProgressEvent, type AgentProgress, type ProgressEvent } from '../lib/agentProgress'

type AgentState = { id: string; name: string; status: string; summary: string; error?: string | null; started_at?: string | null; completed_at?: string | null; progress?: AgentProgress | null }
type Evidence = { source_id: string; quote: string }
export type AgentFinding = { id: string; agent_id: string; title: string; severity: string; quote: string; replacement: string | null; start: number | null; end: number | null; rationale: string; evidence: Evidence[]; verdict: 'supported' | 'needs_evidence' | 'conflict'; applicable: boolean; decision: 'pending' | 'accepted' | 'rejected'; risk_level?: string; risk_note?: string; requires_manual_review?: boolean }
export type AgentReview = { id: string; format_run_id: string; document_id: string; source_hash: string; preview_hash: string; status: string; agents: AgentState[]; findings: AgentFinding[]; summary: string; coverage: unknown; model: string | null; error?: string | null; created_at?: string; user_goal?: string; review_profile_id?: string; document_kind?: string; plan?: string[] }
type ReviewContext = { roles: { id: string; name: string }[]; document_kind: string; review_profile_id: string; template_name: string; source_characters: number; preview_characters: number; plan: string[] }
type Version = { id: string; document_id: string; parent_id: string | null; hash: string; origin: string; created_at: string; content: string }
export type AgentVersionResult = { review?: AgentReview; followup_review?: AgentReview | null; format_run_id: string | null; version?: Version | null }
type Material = { name: string; content: string }
type Config = { configured: boolean; base_url: string | null; model: string | null; reason?: string }
type Props = {
  apiBase: string; runId: string | null; sourceHash: string; previewHash: string; documentEpoch: number; dirty: boolean; busy: boolean; hidden: boolean
  selectedReviewId?: string | null
  applyVersion: (action: () => Promise<AgentVersionResult>) => Promise<AgentVersionResult>
  onLocate: (quote: string) => void
  onReviewChange: (review: AgentReview | null) => void
}

const labels: Record<string, string> = { queued: '排队中', running: '运行中', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', skipped: '未执行', pending: '待审查', supported: '证据支持', needs_evidence: '需要补充证据', conflict: '存在冲突', accepted: '已接受', rejected: '已拒绝', high: '高', medium: '中', low: '低', critical: '严重' }
const eventLabels: Record<string, string> = {
  review_queued: '审查已加入队列', review_started: '审查开始', review_completed: '审查完成',
  review_cancelled: '审查已取消', review_failed: '审查失败', review_retry: '重新尝试审查',
  role_started: '审查角色开始工作', role_checkpoint: '阶段结果已保存',
  role_completed: '审查角色已完成', role_failed: '审查角色遇到问题',
  challenge_started: '专家质疑复核开始',
  finding_decided: '建议处理结果已记录',
}
const eventLabel = (type: string) => eventLabels[type] ?? '记录了新的运行事件'
type FindingFilter = 'pending' | 'accepted' | 'rejected' | 'all'
const roles = [{ id: 'methods', name: '方法论审查官' }, { id: 'innovation', name: '创新点审查官' }, { id: 'norms', name: '学术规范审查官' }, { id: 'comparator', name: '修订对照审查官' }, { id: 'judge', name: '证据裁判官' }]
const isRunning = (review: AgentReview | null) => !!review && (review.status === 'queued' || review.status === 'running')
const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '服务连接失败；本地内容已保留，服务端状态尚未确认。'
const collection = <T,>(value: T[] | { reviews?: T[]; versions?: T[] }) => Array.isArray(value) ? value : value.reviews ?? value.versions ?? []
function when(value?: string) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '' }

export default function AgentReviewPanel(props: Props) {
  const { apiBase, runId, sourceHash, previewHash, documentEpoch, dirty, busy, hidden, selectedReviewId, applyVersion, onLocate, onReviewChange } = props
  const [config, setConfig] = useState<Config | null>(null)
  const [configError, setConfigError] = useState('')
  const [configNonce, setConfigNonce] = useState(0)
  const [active, setActive] = useState<AgentReview | null>(null)
  const [history, setHistory] = useState<AgentReview[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [materials, setMaterials] = useState<Material[]>([])
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [stream, setStream] = useState('未连接')
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [reviewContext, setReviewContext] = useState<ReviewContext | null>(null)
  const [userGoal, setUserGoal] = useState('')
  const [findingFilter, setFindingFilter] = useState<FindingFilter>('pending')
  const [focusedFindingId, setFocusedFindingId] = useState('')
  const [historyNonce, setHistoryNonce] = useState(0)
  const uploadRef = useRef<HTMLInputElement>(null)
  const contextRef = useRef({ runId, documentEpoch })
  contextRef.current = { runId, documentEpoch }
  const workingRef = useRef(false)
  const callbackRef = useRef(onReviewChange)
  const historySelectionRef = useRef(0)
  const requestedReviewIdRef = useRef<string | null>(null)
  callbackRef.current = onReviewChange
  const currentSnapshot = !!active && active.format_run_id === runId && active.source_hash === sourceHash && active.preview_hash === previewHash && !dirty
  const running = isRunning(active) && active?.format_run_id === runId
  const canStart = !!runId && !!sourceHash && !!previewHash && !!config?.configured && !dirty && !busy && !working && !running
  const selectedRoles = reviewContext?.roles?.length ? reviewContext.roles : roles
  const displayAgents: AgentState[] = active?.agents?.length ? active.agents : selectedRoles.map((role) => ({ ...role, status: 'idle', summary: '' }))
  const findings = active?.findings ?? []
  const pendingFindings = findings.filter((finding) => finding.decision === 'pending')
  const visibleFindings = findingFilter === 'all' ? findings : findings.filter((finding) => finding.decision === findingFilter)
  const coverageRecord = active?.coverage && typeof active.coverage === 'object' ? active.coverage as { rounds_completed?: unknown } : null
  const roundsCompleted = Number.isInteger(coverageRecord?.rounds_completed) ? Number(coverageRecord?.rounds_completed) : 0
  const workflowEvents = events.map((event) => ({ ...event, data: event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {} }))
  const hasJudgeRound1 = roundsCompleted >= 1 || workflowEvents.some((event) => event.type.startsWith('role_') && event.data.agent_id === 'judge' && Number(event.data.round) === 1)
  const hasChallenge = roundsCompleted >= 2 || workflowEvents.some((event) => event.type === 'challenge_started' || (event.type === 'role_started' && event.data.agent_id !== 'judge' && Number(event.data.round) === 2))
  const hasJudgeRound2 = roundsCompleted >= 2 || workflowEvents.some((event) => event.type.startsWith('role_') && event.data.agent_id === 'judge' && Number(event.data.round) === 2)
  const expertAgents = displayAgents.filter((agent) => agent.id !== 'judge')
  const completedExperts = expertAgents.filter((agent) => agent.status === 'completed').length
  const failedExperts = expertAgents.some((agent) => ['failed', 'partial'].includes(agent.status))
  const reviewFinished = !!active && ['completed', 'partial', 'failed', 'cancelled'].includes(active.status)
  const workflowSteps = [
    { id: 'snapshot', label: '格式快照', detail: !runId ? '先运行格式扫描' : dirty ? '正文已变更，需重新扫描' : '已锁定本次审查版本', state: !runId ? 'waiting' : dirty ? 'attention' : 'done' },
    { id: 'experts', label: expertAgents.length + ' 位专家并行审查', detail: !active ? '等待启动 Agent 审查' : completedExperts + '/' + expertAgents.length + ' 位专家完成', state: !active ? 'waiting' : failedExperts && !reviewFinished ? 'attention' : completedExperts === expertAgents.length ? 'done' : running ? 'active' : reviewFinished && completedExperts < expertAgents.length ? 'attention' : 'waiting' },
    { id: 'judge1', label: '证据裁判 · 第 1 轮', detail: hasJudgeRound1 ? roundsCompleted >= 1 ? '第一轮裁决已保存' : '正在核对专家发现与证据' : '专家审查完成后进入', state: roundsCompleted >= 1 ? 'done' : hasJudgeRound1 && running ? 'active' : reviewFinished && hasJudgeRound1 ? 'attention' : 'waiting' },
    { id: 'challenge', label: '质疑与复核', detail: hasChallenge ? '按裁判提出的质疑复核专家结论' : reviewFinished && roundsCompleted === 1 ? '未触发额外质疑，按流程跳过' : '仅在裁判提出质疑时执行', state: hasJudgeRound2 ? 'done' : hasChallenge && running ? 'active' : reviewFinished && roundsCompleted === 1 && !hasChallenge ? 'skipped' : reviewFinished && hasChallenge ? 'attention' : 'waiting' },
    { id: 'judge2', label: '证据裁判 · 第 2 轮', detail: hasJudgeRound2 ? roundsCompleted >= 2 ? '第二轮裁决已保存' : '正在复核质疑后的结论' : reviewFinished && roundsCompleted === 1 ? '无需第二轮' : '仅质疑复核后进入', state: roundsCompleted >= 2 ? 'done' : hasJudgeRound2 && running ? 'active' : reviewFinished && roundsCompleted === 1 && !hasJudgeRound2 ? 'skipped' : reviewFinished && hasJudgeRound2 ? 'attention' : 'waiting' },
    { id: 'human', label: '人工处理建议', detail: !reviewFinished || active?.status !== 'completed' ? 'Agent 完成后，由你逐条决定' : pendingFindings.length ? pendingFindings.length + ' 条待人工核验；不会自动改正文' : '本次建议均已处理', state: !reviewFinished ? 'waiting' : active?.status !== 'completed' ? 'attention' : pendingFindings.length ? 'active' : 'done' },
  ] as const
  const liveWorkflowEvent = [...workflowEvents].reverse().find((event) => ['review_queued', 'review_started', 'role_started', 'challenge_started', 'role_completed', 'role_failed', 'review_completed', 'review_failed', 'review_cancelled'].includes(event.type))
  const liveWorkflowTitle = liveWorkflowEvent ? eventLabel(liveWorkflowEvent.type) + (liveWorkflowEvent.data.agent_id ? ' · ' + (displayAgents.find((role) => role.id === liveWorkflowEvent.data.agent_id)?.name ?? String(liveWorkflowEvent.data.agent_id)) : '') + (liveWorkflowEvent.data.round ? ' · 第 ' + String(liveWorkflowEvent.data.round) + ' 轮' : '') : ''

  function goToNextPending() {
    if (!pendingFindings.length) return
    const current = pendingFindings.findIndex((finding) => finding.id === focusedFindingId)
    const next = pendingFindings[(current + 1) % pendingFindings.length]
    setFindingFilter('pending')
    setFocusedFindingId(next.id)
  }

  useEffect(() => {
    if (!focusedFindingId || findingFilter !== 'pending') return
    document.getElementById('agent-finding-' + focusedFindingId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [findingFilter, focusedFindingId, active?.id])

  async function request<T,>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(apiBase + path, { ...init, signal: init?.signal ?? AbortSignal.timeout(25000) })
    if (!response.ok) {
      const data = await response.json().catch(() => null) as { detail?: unknown } | null
      throw new Error(typeof data?.detail === 'string' ? data.detail : '请求失败（' + response.status + '）')
    }
    return response.json() as Promise<T>
  }

  useEffect(() => { callbackRef.current(active) }, [active])

  useEffect(() => {
    const refresh = () => setConfigNonce(value => value + 1)
    window.addEventListener('agent-settings-changed', refresh)
    return () => window.removeEventListener('agent-settings-changed', refresh)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setConfig(null)
    setConfigError('')
    request<Config>('/agent-config', { signal: controller.signal }).then(setConfig).catch((failure) => { if (!controller.signal.aborted) setConfigError(errorText(failure)) })
    return () => controller.abort()
  }, [apiBase, configNonce])

  useEffect(() => {
    setActive(null)
    setMaterials([])
    setEvents([])
    setUserGoal('')
    setFindingFilter('pending')
    setFocusedFindingId('')
    setError('')
    setWorking('')
    workingRef.current = false
  }, [documentEpoch])

  useEffect(() => {
    const controller = new AbortController()
    setReviewContext(null)
    if (runId) void request<ReviewContext>('/agent-review-context?format_run_id=' + encodeURIComponent(runId), { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setReviewContext(value) }).catch(() => { /* Generic scope remains visible if the optional context lookup fails. */ })
    return () => controller.abort()
  }, [apiBase, runId, previewHash, documentEpoch])

  useEffect(() => {
    if (!active?.id) return
    const controller = new AbortController()
    const id = active.id
    void request<unknown[]>('/agent-reviews/' + encodeURIComponent(id) + '/progress-events', { signal: controller.signal }).then(values => {
      if (!controller.signal.aborted) setEvents(items => mergeProgressEvents(items, values.map(normalizeProgressEvent).filter((item): item is ProgressEvent => !!item)))
    }).catch(failure => { if (!controller.signal.aborted) setError('执行历史读取失败：' + errorText(failure)) })
    return () => controller.abort()
  }, [apiBase, active?.id, active?.status, documentEpoch])

  useEffect(() => {
    const controller = new AbortController()
    setHistory([])
    setVersions([])
    if (!runId) return () => controller.abort()
    Promise.all([
      request<AgentReview[] | { reviews: AgentReview[] }>('/agent-reviews?format_run_id=' + encodeURIComponent(runId), { signal: controller.signal }),
      request<Version[] | { versions: Version[] }>('/format-runs/' + encodeURIComponent(runId) + '/versions', { signal: controller.signal }),
    ]).then(([reviews, versionList]) => {
      if (controller.signal.aborted) return
      setHistory(collection(reviews))
      setVersions(collection(versionList))
    }).catch((failure) => { if (!controller.signal.aborted) setError('历史记录读取失败：' + errorText(failure)) })
    return () => controller.abort()
  }, [apiBase, runId, previewHash, historyNonce])

  useEffect(() => {
    if (!selectedReviewId) { requestedReviewIdRef.current = null; return }
    if (selectedReviewId === active?.id || busy || running || !!working || workingRef.current) return
    if (requestedReviewIdRef.current === selectedReviewId) return
    requestedReviewIdRef.current = selectedReviewId
    void selectHistory(selectedReviewId)
  }, [selectedReviewId, active?.id, busy, running, working, documentEpoch])

  useEffect(() => {
    if (!active || !isRunning(active) || active.format_run_id !== runId) { setStream('未连接'); return }
    const controller = new AbortController()
    let stopped = false
    let fetching = false
    let reconnecting = false
    let lastEvent = Date.now()
    let delayed: ReturnType<typeof setTimeout> | undefined
    const id = active.id
    const refresh = async () => {
      if (fetching || stopped) return
      fetching = true
      try {
        const updated = await request<AgentReview>('/agent-reviews/' + encodeURIComponent(id), { signal: controller.signal })
        if (!stopped) { setActive((previous) => previous?.id === updated.id && !isRunning(previous) && isRunning(updated) ? previous : updated); if (!isRunning(updated)) setHistoryNonce((value) => value + 1) }
        if (reconnecting && !stopped) {
          const replay = await request<unknown[]>('/agent-reviews/' + encodeURIComponent(id) + '/progress-events', { signal: controller.signal })
          if (!stopped) setEvents(items => mergeProgressEvents(items, replay.map(normalizeProgressEvent).filter((item): item is ProgressEvent => !!item)))
        }
      } catch (failure) { if (!stopped) { setStream('连接中断，轮询重试'); setError(errorText(failure)) } }
      finally { fetching = false }
    }
    setStream('连接事件流')
    const source = new EventSource(apiBase + '/agent-reviews/' + encodeURIComponent(id) + '/events')
    source.onopen = () => { if (!stopped) { reconnecting = false; setStream('实时事件流已连接') } }
    source.onmessage = (message) => {
      if (stopped) return
      lastEvent = Date.now()
      try {
        const event = normalizeProgressEvent(JSON.parse(message.data))
        if (event) setEvents(items => mergeProgressEvents(items, [event]))
      } catch { /* An incomplete event never becomes a review result. */ }
      if (delayed) clearTimeout(delayed)
      delayed = setTimeout(() => { void refresh() }, 80)
    }
    source.onerror = () => { if (!stopped) { reconnecting = true; setStream('事件流重连中，轮询核对') } }
    const poll = setInterval(() => { if (reconnecting || Date.now() - lastEvent > 10000) void refresh() }, 2000)
    void refresh()
    return () => { stopped = true; controller.abort(); source.close(); clearInterval(poll); if (delayed) clearTimeout(delayed) }
  }, [apiBase, active?.id, active?.status, runId, documentEpoch])

  async function operate(label: string, action: () => Promise<void>) {
    if (workingRef.current) return
    workingRef.current = true
    setWorking(label)
    setError('')
    const epoch = documentEpoch
    try { await action() }
    catch (failure) { if (contextRef.current.documentEpoch === epoch) setError(errorText(failure)) }
    finally { if (contextRef.current.documentEpoch === epoch) { workingRef.current = false; setWorking('') } }
  }

  function readableEvent(event: { type: string; payload?: unknown }) {
    const payload = event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {}
    const role = displayAgents.find((item) => item.id === payload.agent_id)?.name
    const base = eventLabel(event.type)
    const title = role && event.type.startsWith('role_') ? role + base.replace('审查角色', '') : base
    const details = Object.entries(payload).filter(([key]) => !['progress', 'started_at', 'completed_at'].includes(key)).map(([key, value]) => {
      const name: Record<string, string> = { agent_id: '审查角色', status: '状态', round: '轮次', window_index: '分段', batch_index: '批次', reused: '复用已有结果', decision: '处理决定', finding_id: '建议编号', error: '说明' }
      const rendered = key === 'status' ? labels[String(value)] ?? String(value) : typeof value === 'boolean' ? (value ? '是' : '否') : String(value)
      return (name[key] ?? key) + '：' + rendered
    })
    return { title, details }
  }

  async function startReview(retry = false) {
    if (!canStart || (retry && (!active || !currentSnapshot))) return
    const currentRun = runId
    const epoch = documentEpoch
    await operate(retry ? '重试中' : '启动中', async () => {
      const result = await request<AgentReview>(retry ? '/agent-reviews/' + encodeURIComponent(active!.id) + '/retry' : '/agent-reviews', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(retry ? { expected_source_hash: sourceHash, expected_preview_hash: previewHash } : { format_run_id: currentRun, expected_source_hash: sourceHash, expected_preview_hash: previewHash, materials, user_goal: userGoal }),
      })
      if (contextRef.current.runId !== currentRun || contextRef.current.documentEpoch !== epoch) return
      setActive(result)
      setEvents([])
      setFindingFilter('pending')
      setFocusedFindingId('')
      setHistoryNonce((value) => value + 1)
    })
  }

  async function cancelReview() {
    if (!active || !isRunning(active)) return
    const id = active.id
    const epoch = documentEpoch
    await operate('取消中', async () => {
      const result = await request<AgentReview>('/agent-reviews/' + encodeURIComponent(id) + '/cancel', { method: 'POST' })
      if (contextRef.current.documentEpoch === epoch) { setActive(result); setHistoryNonce((value) => value + 1) }
    })
  }

  async function decide(finding: AgentFinding, decision: 'accept' | 'reject') {
    if (!active || !currentSnapshot || busy || running || finding.decision !== 'pending') return
    if (decision === 'accept' && (!finding.applicable || finding.verdict !== 'supported')) return
    const id = active.id
    const epoch = documentEpoch
    await operate('提交修订中', async () => {
      const result = await applyVersion(() => request<AgentVersionResult>('/agent-reviews/' + encodeURIComponent(id) + '/findings/' + encodeURIComponent(finding.id) + '/decision', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, expected_preview_hash: previewHash }),
      }))
      if (contextRef.current.documentEpoch === epoch) {
        const updated = result.followup_review ?? result.review
        if (updated) { setActive(updated); if (result.followup_review) setEvents([]) }
        setHistoryNonce((value) => value + 1)
      }
    })
  }

  async function restoreVersion(version: Version) {
    if (!runId || dirty || busy || running) return
    const epoch = documentEpoch
    await operate('恢复版本中', async () => {
      const result = await applyVersion(() => request<AgentVersionResult>('/format-runs/' + encodeURIComponent(runId) + '/versions/' + encodeURIComponent(version.id) + '/restore', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_preview_hash: previewHash }),
      }))
      if (contextRef.current.documentEpoch === epoch) {
        const updated = result.followup_review ?? result.review
        if (updated) { setActive(updated); if (result.followup_review) setEvents([]) }
        setHistoryNonce((value) => value + 1)
      }
    })
  }

  async function selectHistory(id: string) {
    if (running || busy || workingRef.current) return
    const epoch = documentEpoch
    const selection = ++historySelectionRef.current
    await operate('读取审查记录', async () => {
      const result = await request<AgentReview>('/agent-reviews/' + encodeURIComponent(id))
      if (contextRef.current.documentEpoch === epoch && historySelectionRef.current === selection) { setActive(result); setUserGoal(result.user_goal ?? ''); setEvents([]); setFindingFilter('pending'); setFocusedFindingId('') }
    })
  }

  async function uploadMaterials(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!files.length) return
    const epoch = documentEpoch
    await operate('读取材料', async () => {
      if (materials.length + files.length > 10) throw new Error('最多上传 10 份材料。')
      if (files.some((file) => !/\.(txt|md|markdown)$/i.test(file.name))) throw new Error('材料仅支持 TXT 或 Markdown 文件。')
      if (files.some((file) => file.name.length > 255)) throw new Error('材料文件名不能超过 255 字符。')
      if (files.some((file) => file.size > 1200000)) throw new Error('单份材料过大，请缩减至 300,000 字符以内。')
      const added = await Promise.all(files.map(async (file) => ({ name: file.name, content: await file.text() })))
      if (added.some((material) => !material.content.trim())) throw new Error('参考材料不能为空，请移除空白文件后重试。')
      const combined = [...materials, ...added]
      if (combined.reduce((sum, material) => sum + material.content.length, 0) > 300000) throw new Error('材料总量超过 300,000 字符，请分批审查。')
      if (contextRef.current.documentEpoch === epoch) setMaterials(combined)
    })
  }

  return <section className="agent-review-panel" hidden={hidden} aria-label="Agent 审查面板">
    {error && <div className="agent-error" role="alert">{error}</div>}
    {dirty && <div className="agent-notice">编辑器已有修改，请重新格式扫描后启动或应用 Agent 审查。</div>}
    <input ref={uploadRef} type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" multiple hidden onChange={uploadMaterials} />
    <div className="agent-run-summary" aria-label="审查流程与下一步">
      <strong>{running ? '正在审查' : active?.status === 'completed' && pendingFindings.length ? pendingFindings.length + ' 条建议待处理' : active?.status === 'completed' ? '本次审查已完成' : active && ['partial', 'failed', 'cancelled'].includes(active.status) ? '审查未完整完成' : !runId ? '请先运行格式扫描' : config && !config.configured ? '配置模型后即可开始' : !config ? '正在检查模型服务' : '快照已准备好'}</strong>
      <p>审查链路：文档快照 → {expertAgents.length} 位专家并行 → 证据裁判 → 按需质疑复核 → 人工处理</p>
      <p>{running ? '审查正在后台执行；当前文档可继续浏览，稍后返回查看进度。' : !runId ? '格式扫描会建立用于核对的文档快照。' : dirty ? '正文有未扫描修改；重新扫描后才能启动审查或处理建议。' : active?.status === 'completed' && pendingFindings.length ? '按“下一条待处理建议”逐项核对证据，并决定接受或拒绝。' : active && ['partial', 'failed', 'cancelled'].includes(active.status) ? '可重试未完成的审查；已完成的审查角色结果会按原快照复用。' : config && !config.configured ? '请展开下方模型配置，配置本地兼容服务后启动。' : !config ? '正在检查模型服务配置，请稍候。' : active?.status === 'completed' ? '本次待处理建议已全部决定，可查看历史、证据和版本。' : '快照与模型服务就绪后，可以启动五角色审查。'}</p>
      {active && <p>状态：{labels[active.status] ?? active.status} · 待处理 {pendingFindings.length} 条</p>}
      <label style={{ display: 'block', margin: '10px 0' }}>本次修改目标<input aria-label="本次智能修改目标" maxLength={2000} value={userGoal} disabled={running || !!working || busy} onChange={event => setUserGoal(event.target.value)} placeholder="例如：按模板统一术语，检查结构并改善表达" style={{ display: 'block', width: '100%', marginTop: 6, padding: 8, boxSizing: 'border-box' }}/></label>
      <p>发送范围：本次原稿{reviewContext ? '（' + reviewContext.source_characters + ' 字符）' : ''}、修订快照{reviewContext ? '（' + reviewContext.preview_characters + ' 字符）' : ''}、确认模板规则、修改目标，以及 {materials.length} 份参考材料；发送到当前配置的模型端点。</p>
      <p>计划：确认快照与模板 → 按目标分段检查 → 裁判复核 → 逐项确认、另存并复检。数字、公式和引用变化需人工核实。</p>
      <div className="agent-controls"><button type="button" className="primary-button" onClick={() => active?.status === 'completed' ? goToNextPending() : active && ['partial', 'failed', 'cancelled'].includes(active.status) ? void startReview(true) : void startReview()} disabled={active?.status === 'completed' ? !pendingFindings.length : active && ['partial', 'failed', 'cancelled'].includes(active.status) ? !canStart || !currentSnapshot : !canStart}>{active?.status === 'completed' ? '下一条待处理建议' : active && ['partial', 'failed', 'cancelled'].includes(active.status) ? '重试未完成审查' : working === '启动中' ? working : '启动五角色审查'}</button><button type="button" className="ghost-button" onClick={cancelReview} disabled={!running || !!working}><Square size={12} />取消</button></div>
      {!runId && <p className="review-empty">请先运行格式扫描，为审查建立可核对的文档快照。</p>}
    </div>
    <section className="agent-workflow" aria-label="Agent 审查工作流" data-testid="agent-workflow">
      <div className="agent-workflow-heading"><strong>本次流程</strong>{running && liveWorkflowTitle && <span role="status">当前：{liveWorkflowTitle}</span>}</div>
      <ol className="agent-workflow-steps">{workflowSteps.map((step, index) => <li className={'agent-workflow-step workflow-' + step.state} key={step.id} aria-current={step.state === 'active' ? 'step' : undefined}><span className="agent-workflow-marker">{step.state === 'done' ? <Check size={12}/> : step.state === 'skipped' ? '—' : index + 1}</span><div><strong>{step.label}</strong><small>{step.detail}</small></div></li>)}</ol>
    </section>
    <div className="agent-stages" aria-label="Agent 运行阶段">{displayAgents.map((agent) => <div className={'agent-stage stage-' + agent.status} key={agent.id}><span>{agent.name}</span><b>{agent.status === 'idle' ? '尚未运行' : labels[agent.status] ?? agent.status}</b>{agent.summary && <p>{agent.summary}</p>}{agent.error && <p className="stage-error">{agent.error}</p>}<AgentRoleProgress key={documentEpoch + ':' + (active?.id ?? 'idle') + ':' + agent.id} name={agent.name} status={agent.status} progress={agent.progress} startedAt={agent.started_at} completedAt={agent.completed_at} events={events.filter(event => event.payload?.agent_id === agent.id)}/></div>)}</div>
    <details className="agent-coverage agent-config-details"><summary>模型配置与审查说明</summary><div className="agent-config"><strong>{expertAgents.length} 位专家 + 一位证据裁判</strong><p>{displayAgents.filter(agent => agent.id !== 'judge').map(agent => agent.name).join('、')}并行审查；证据裁判汇总并核验，必要时发起质疑复核。结果仍由人工决定，接受后另存新版本。</p>
      {config ? <><span className={'agent-config-status ' + (config.configured ? 'configured' : '')}>{config.configured ? '模型服务已配置' : '模型服务未配置'}</span><p>{config.configured ? '模型：' + config.model : config.reason || '请在后端配置模型服务。'}</p></> : <p>{configError || '正在检查模型服务配置…'}</p>}
      <button type="button" className="view-debate" onClick={() => setConfigNonce((value) => value + 1)} disabled={!!working || busy}><RefreshCw size={12} />重新检查配置</button>
    </div></details>
    <details className="agent-coverage"><summary>参考材料 · 不联网检索（{materials.length}/10）</summary><div className="agent-materials"><button type="button" className="view-debate" onClick={() => uploadRef.current?.click()} disabled={running || !!working || busy}><FileUp size={12} />上传 TXT / Markdown 材料</button>
      <p>仅以文档快照及上传材料核对证据；总量上限 300,000 字符。</p>
      {materials.map((material, index) => <div className="material-item" key={index}><span title={material.name}>{material.name} · {material.content.length} 字符</span><button type="button" aria-label={'移除材料 ' + material.name} disabled={running || !!working || busy} onClick={() => setMaterials((items) => items.filter((_, at) => at !== index))}><X size={12} /></button></div>)}
    </div></details>
    {active && <>
      <div className="agent-run-summary"><strong>{labels[active.status] ?? active.status}</strong><span>{active.model || '模型信息待返回'}</span><p>{active.summary || (running ? '等待实际审查结果…' : '本次审查未返回总结。')}</p>{active.error && <p className="stage-error">{active.error}</p>}{running && <small>{stream}</small>}</div>
      {!currentSnapshot && <div className="agent-notice">此报告对应历史快照，仅供查看。对当前内容重新审查后才能接受建议。</div>}
      <details className="agent-coverage"><summary>审查覆盖范围与快照</summary><p>审查 ID：{active.id}</p><p>原稿 SHA-256：{active.source_hash}</p><p>预览 SHA-256：{active.preview_hash}</p><pre>{typeof active.coverage === 'string' ? active.coverage : JSON.stringify(active.coverage, null, 2)}</pre></details>
      <div className="agent-findings" aria-label="Agent 修改建议">
        <div className="agent-controls" role="group" aria-label="筛选修改建议" style={{ flexWrap: 'wrap' }}>
          {([['pending', '待处理', pendingFindings.length], ['accepted', '已接受', findings.filter((finding) => finding.decision === 'accepted').length], ['rejected', '已拒绝', findings.filter((finding) => finding.decision === 'rejected').length], ['all', '全部', findings.length]] as const).map(([filter, title, count]) => <button key={filter} type="button" className={findingFilter === filter ? 'primary-button' : 'ghost-button'} aria-pressed={findingFilter === filter} onClick={() => setFindingFilter(filter)}>{title} {count}</button>)}
          {pendingFindings.length > 0 && <button type="button" className="view-debate" onClick={goToNextPending}>下一条待处理</button>}
        </div>
        {!findings.length && !running && <p className="review-empty">未返回修改建议；这不代表文档无问题，请结合运行状态与覆盖范围判断。</p>}
        {!!findings.length && !visibleFindings.length && <p className="review-empty">此筛选下没有修改建议。</p>}
        {visibleFindings.map((finding) => <article className="agent-finding" id={'agent-finding-' + finding.id} key={finding.id}>
        <div className="agent-finding-head"><strong>{finding.title}</strong><span>{labels[finding.severity] ?? finding.severity}</span></div><div className="agent-finding-meta">{active.agents.find((agent) => agent.id === finding.agent_id)?.name ?? finding.agent_id} · {labels[finding.verdict]} · {labels[finding.decision]}</div>
        <p>{finding.rationale}</p><div className="change-diff"><span className="before">原文：{finding.replacement === null ? finding.quote || '未提供可定位原文' : <TextDiff before={finding.quote} after={finding.replacement} side="before"/>}</span>{finding.replacement !== null && <span className="after">建议：{finding.replacement ? <TextDiff before={finding.quote} after={finding.replacement} side="after"/> : '（删除此段）'}</span>}</div>
        {finding.risk_note && <p className={finding.requires_manual_review ? 'finding-limit' : undefined}>修改风险：{finding.risk_note}</p>}
        <details><summary>证据 ({finding.evidence.length})</summary>{finding.evidence.length ? finding.evidence.map((evidence, index) => <blockquote key={index}><strong>{evidence.source_id}</strong><p>{evidence.quote}</p></blockquote>) : <p>尚无已验证的材料引文。</p>}</details>
        {!finding.applicable && <p className="finding-limit">此项仅供人工核对，未满足自动应用条件。</p>}
        <div className="agent-finding-actions"><button type="button" className="tool-chip" onClick={() => onLocate(finding.quote)} disabled={!currentSnapshot || !finding.quote}>定位原文</button><button type="button" className="accept-btn" onClick={() => void decide(finding, 'accept')} disabled={!currentSnapshot || running || busy || !!working || !finding.applicable || finding.verdict !== 'supported' || finding.decision !== 'pending'}><Check size={12} />接受并另存版本</button><button type="button" className="reject-btn" onClick={() => void decide(finding, 'reject')} disabled={!currentSnapshot || running || busy || !!working || finding.decision !== 'pending'}><X size={12} />拒绝</button></div>
      </article>)}</div>
      {events.length > 0 && <details className="agent-events"><summary>运行事件详情（{events.length}）</summary><ol>{events.map((event, index) => { const summary = readableEvent(event); return <li key={event.id + '-' + index}>{summary.title}{summary.details.length > 0 && <small> · {summary.details.join(' · ')}</small>}<details><summary>原始字段</summary><p>事件码：<code>{event.type}</code></p><pre>{JSON.stringify(event.payload ?? {}, null, 2)}</pre></details></li> })}</ol></details>}
    </>}
    <details className="agent-history"><summary><History size={12} />审查历史 ({history.length})</summary>{history.length ? history.map((item) => <button type="button" key={item.id} className="history-item" disabled={running || !!working} onClick={() => void selectHistory(item.id)}><span>{labels[item.status] ?? item.status} · {when(item.created_at) || item.id}</span><small>{item.summary || item.id}</small></button>) : <p>当前快照尚无历史审查。</p>}</details>
    <details className="agent-history"><summary><History size={12} />文档版本 ({versions.length})</summary>{versions.length ? versions.map((version) => <div className="version-item" key={version.id}><span>{version.origin} · {when(version.created_at)}</span><code title={version.hash}>{version.hash.slice(0, 16)}</code><button type="button" className="view-debate" disabled={dirty || busy || !!working || running || version.hash === previewHash} onClick={() => void restoreVersion(version)}>恢复为新版本</button></div>) : <p>版本记录将在扫描后生成。</p>}</details>
  </section>
}
