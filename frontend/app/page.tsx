'use client'
import { useEffect, useRef, useState } from 'react'
import { EditorContent, useEditor, type Editor } from '@tiptap/react'
import { editorExtensions, MARKDOWN_SCHEMA_VERSION, migrateLegacyDocument, parseMarkdown, serializeMarkdown, type StoredEditorDocument } from '../lib/markdown'
import AgentReviewPanel, { type AgentReview, type AgentVersionResult } from '../components/AgentReviewPanel'
import AgentSettings from '../components/AgentSettings'
import AgentChannelSwitcher from '../components/AgentChannelSwitcher'
import SchoolTemplatePanel from '../components/SchoolTemplatePanel'
import FormatDiffPanel from '../components/FormatDiffPanel'
import DocumentStudio from '../components/DocumentStudio'
import DocumentAssistant from '../components/DocumentAssistant'
import GroupedChanges from '../components/GroupedChanges'
import { changeLocation, conflictingChangeIds, LOW_RISK_RULES } from '../lib/changeGrouping'
import { DEFAULT_DOCUMENT_STYLE, documentStyleCss, type DocumentStyle } from '../lib/documentStudio'
import { listFormatProfiles, type FormatProfile } from '../lib/schoolTemplates'
import { readWorkspaceLayout, saveWorkspaceLayout, type ReviewTab } from '../lib/workspaceLayout'
import { ArrowDownToLine, Check, ChevronDown, FilePlus2, FileText, FileUp, GitCompare, Highlighter, History, Layers3, MoreHorizontal, PanelLeft, PanelRight, RotateCcw, Save, ScanLine, Settings, Sigma, X, AlertTriangle } from 'lucide-react'
import 'katex/dist/katex.min.css'
type Status = 'pending' | 'accepted' | 'rejected'
type ChangeKind = 'format' | 'structure' | 'reference' | 'ai'
type Change = { id: string; title: string; detail: string; before: string; after: string; kind: ChangeKind; status: Status; anchor: string; rule: string; start?: number; end?: number; replacement?: string | null; line?: number; occurrence?: number; decidedAt?: string | null }
type ApiChange = { id: string; property: string; explanation: string; before: string; after: string; kind: ChangeKind; status: Status; anchor: { quote: string; start: number; end: number; node_id: string }; replacement?: string | null; rule_id: string; decided_at?: string | null }
type PreviewResponse = { preview?: string; source?: string; notes?: string[]; applied_count?: number }
type FormatRunIdentity = { id: string; document_id: string }
type DiffRow = { kind: 'equal' | 'insert' | 'delete' | 'replace'; source_line_no: number | null; preview_line_no: number | null; before: string | null; after: string | null }
type ReviewResponse = { run_id: string; engine: 'deterministic'; llm_enabled: false; source_hash: string; preview_hash: string; status: 'passed' | 'needs_review' | 'blocked'; summary: string; applied_count: number; accepted_count: number; pending_count: number; rejected_count: number; blocked_change_ids: string[]; checks: { id: string; title: string; status: 'passed' | 'warning' | 'failed'; detail: string }[]; diff_rows: DiffRow[]; unified_diff: string }
type EditorNode = StoredEditorDocument
type WorkspaceSummary = { id: string; name: string; document_type: string; updated_at: string; revision: number; last_run_id: string | null; head_hash: string | null; profile_id: string }
type WorkspaceDocument = WorkspaceSummary & { content: string; editor_json: EditorNode | null }
type WorkspaceAgentTask = { document_id: string; review_id: string; format_run_id: string; status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled'; pending_count: number; finding_count: number; updated_at: string }
type CloseSnapshot = { id: string; name: string; content: string; editor_json: EditorNode; expected_revision: number; last_run_id: string | null; profile_id: string }
type OutlineItem = { title: string; level: number; position: number }
type SaveState = 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict'
declare global { interface Window { __thesisCloseSnapshot?: () => CloseSnapshot | null; __thesisCloseResume?: (message: string) => void; __thesisSavePending?: boolean } }
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8001')
const reviewStatus = { passed: '检查通过', needs_review: '待处理', blocked: '存在冲突' }
const saveLabels: Record<SaveState, string> = { saved: '已保存', unsaved: '待保存', saving: '保存中…', error: '保存失败', conflict: '保存冲突' }
function kindName(kind: ChangeKind) { return { format: '格式', structure: '结构', reference: '引用', ai: 'AI 建议' }[kind] }

export default function Home() {
  const [documents, setDocuments] = useState<WorkspaceSummary[]>([])
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(true)
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [leftRailOpen, setLeftRailOpen] = useState(true)
  const [rightPanelOpen, setRightPanelOpen] = useState(false)
  const [layoutReady, setLayoutReady] = useState(false)
  const [formulaOpen, setFormulaOpen] = useState(false)
  const [libraryQuery, setLibraryQuery] = useState('')
  const [agentTasks, setAgentTasks] = useState<Record<string, WorkspaceAgentTask>>({})
  const [taskStatusError, setTaskStatusError] = useState(false)
  const [documentName, setDocumentName] = useState('')
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [saveError, setSaveError] = useState('')
  const [saveTick, setSaveTick] = useState(0)
  const [outline, setOutline] = useState<OutlineItem[]>([])
  const [changes, setChanges] = useState<Change[]>([])
  const [activeId, setActiveId] = useState('')
  const [showDiff, setShowDiff] = useState(false)
  const [latex, setLatex] = useState('')
  const [formulaNotice, setFormulaNotice] = useState('')
  const [scanState, setScanState] = useState('')
  const [tab, setTab] = useState<ReviewTab>('changes')
  const [lastBatch, setLastBatch] = useState<{runId: string; batchId: string} | null>(null)
  const [toast, setToast] = useState('')
  const [runId, setRunId] = useState<string | null>(null)
  const [uploadWarnings, setUploadWarnings] = useState<string[]>([])
  const [previewData, setPreviewData] = useState<PreviewResponse | null>(null)
  const [review, setReview] = useState<ReviewResponse | null>(null)
  const [hasLocalEdits, setHasLocalEdits] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [busy, setBusy] = useState(false)
  const [documentEpoch, setDocumentEpoch] = useState(0)
  const [agentReview, setAgentReview] = useState<AgentReview | null>(null)
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [templatesOpen, setTemplatesOpen] = useState(false)
  const [studioOpen, setStudioOpen] = useState(false)
  const [documentStyle, setDocumentStyle] = useState<DocumentStyle>(DEFAULT_DOCUMENT_STYLE)
  const [profileStyles, setProfileStyles] = useState<Record<string, DocumentStyle | null>>({})
  const [suggestedContent, setSuggestedContent] = useState<string | null>(null)
  const [selectedProfileId, setSelectedProfileId] = useState('generic-thesis')
  const [profileNames, setProfileNames] = useState<Record<string, string>>({ 'generic-thesis': '通用学术论文规范' })
  const profileRef = useRef('generic-thesis')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const toolsMenuRef = useRef<HTMLDetailsElement>(null)
  const editorRef = useRef<Editor | null>(null)
  const operationRef = useRef(0), revisionRef = useRef(0), dirtyRef = useRef(false), busyRef = useRef(false)
  const markdownRef = useRef<string | null>(null), workspaceRef = useRef<WorkspaceDocument | null>(null), nameRef = useRef('')
  const lastRunRef = useRef<string | null>(null), headHashRef = useRef<string | null>(null)
  const saveRequiredRef = useRef(false), saveSerialRef = useRef(0), savePromiseRef = useRef<Promise<void> | null>(null)
  const saveConflictRef = useRef(false), closePausedRef = useRef(false), initializedRef = useRef(false)
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const editor = useEditor({
    extensions: editorExtensions, content: '', immediatelyRender: false,
    editorProps: { attributes: { class: 'paper-content', spellcheck: 'false', 'aria-label': '文档正文' } },
    onUpdate: ({ editor: current }) => {
      if (!workspaceRef.current) return
      revisionRef.current += 1; dirtyRef.current = true; markdownRef.current = null
      setHasLocalEdits(true); refreshOutline(current); markDraftChanged()
    },
  })
  editorRef.current = editor
  const pending = changes.filter(item => item.status === 'pending')
  const accepted = changes.filter(item => item.status === 'accepted').sort((a, b) => (a.decidedAt || '').localeCompare(b.decidedAt || ''))
  const active = changes.find(item => item.id === activeId) || changes[0]
  const hasContent = !!activeDocumentId && !!readContent().trim()
  const canDecide = !!activeDocumentId && !!runId && !!review && !hasLocalEdits && !busy && !saveConflictRef.current
  useEffect(() => {
    let saved
    try { saved = readWorkspaceLayout(window.localStorage, window.matchMedia('(max-width: 1080px)').matches) } catch { saved = {leftRailOpen: !window.matchMedia('(max-width: 1080px)').matches, rightPanelOpen: false, showDiff: false, reviewTab: 'changes' as ReviewTab} }
    setLeftRailOpen(saved.leftRailOpen); setRightPanelOpen(saved.rightPanelOpen); setShowDiff(saved.showDiff); setTab(saved.reviewTab); setLayoutReady(true)
  }, [])
  useEffect(() => {
    if (!layoutReady) return
    try { saveWorkspaceLayout(window.localStorage, {leftRailOpen, rightPanelOpen, showDiff, reviewTab: tab}) } catch { /* Preferences are optional. */ }
  }, [layoutReady, leftRailOpen, rightPanelOpen, showDiff, tab])
  function openReviewPanel(nextTab: ReviewTab) { setTab(nextTab); setRightPanelOpen(true); if (window.matchMedia('(max-width: 1080px)').matches) setLeftRailOpen(false) }
  function toggleNavigation() { setLeftRailOpen(!leftRailOpen); if (!leftRailOpen && window.matchMedia('(max-width: 1080px)').matches) setRightPanelOpen(false) }
  function runTool(action: () => void) { if (toolsMenuRef.current) toolsMenuRef.current.open = false; action() }
  useEffect(() => {
    const closeMenu = (event: MouseEvent) => { if (toolsMenuRef.current && !toolsMenuRef.current.contains(event.target as Node)) toolsMenuRef.current.open = false }
    const escape = (event: KeyboardEvent) => { if (event.key !== 'Escape') return; if (toolsMenuRef.current?.open) { toolsMenuRef.current.open = false; toolsMenuRef.current.querySelector('summary')?.focus() } else if (window.matchMedia('(max-width: 1080px)').matches) { setLeftRailOpen(false); setRightPanelOpen(false) } }
    document.addEventListener('mousedown', closeMenu); document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', closeMenu); document.removeEventListener('keydown', escape) }
  }, [])
  useEffect(() => {
    setLastBatch(null)
    if (!runId) return
    const controller = new AbortController()
    void fetch(API_BASE + '/format-runs/' + encodeURIComponent(runId) + '/batches', {signal:controller.signal})
      .then(async response => response.ok ? response.json() as Promise<{id:string;can_undo:boolean}[]> : [])
      .then(batches => {if (!controller.signal.aborted) {const available=batches.find(batch => batch.can_undo); setLastBatch(available ? {runId,batchId:available.id} : null)}})
      .catch(() => undefined)
    return () => controller.abort()
  }, [runId, review?.preview_hash])
  function refreshOutline(instance: Editor) {
    const headings: OutlineItem[] = []
    instance.state.doc.descendants((node, position) => { if (node.type.name === 'heading' && node.textContent.trim()) headings.push({ title: node.textContent, level: Number(node.attrs.level || 1), position }) })
    setOutline(headings)
  }
  function readContent() { return markdownRef.current ?? (editorRef.current ? serializeMarkdown(editorRef.current.getJSON()) : '') }
  function markDraftChanged() { saveRequiredRef.current = true; saveSerialRef.current += 1; setSaveTick(value => value + 1); if (!saveConflictRef.current) setSaveState('unsaved') }
  function notify(message: string) { setToast(message); window.setTimeout(() => setToast(''), 2600) }
  function summaryOf(document: WorkspaceDocument): WorkspaceSummary { const {id,name,document_type,updated_at,revision,last_run_id,head_hash,profile_id} = document; return {id,name,document_type,updated_at,revision,last_run_id,head_hash,profile_id} }
  function updateLibrary(document: WorkspaceDocument) { setDocuments(previous => [summaryOf(document), ...previous.filter(item => item.id !== document.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at))) }
  async function requestJson<T,>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(API_BASE + path, { ...init, signal: init?.signal ?? AbortSignal.timeout(25000) })
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as { detail?: unknown } | null
      const error = new Error(typeof payload?.detail === 'string' ? payload.detail : '请求失败（' + response.status + '）') as Error & { status?: number }; error.status = response.status; throw error
    }
    return response.json() as Promise<T>
  }
  const workspaceDocumentIds = documents.map(document => document.id).sort().join(',')
  useEffect(() => {
    if (!workspaceDocumentIds) { setAgentTasks({}); setTaskStatusError(false); return }
    let stopped = false
    let pendingRequest = false
    const refresh = async () => {
      if (stopped || pendingRequest) return
      pendingRequest = true
      try {
        const result = await requestJson<{ tasks: WorkspaceAgentTask[] }>('/agent-reviews/workspace-status?document_ids=' + encodeURIComponent(workspaceDocumentIds))
        if (stopped) return
        const allowed = new Set(['queued', 'running', 'completed', 'partial', 'failed', 'cancelled'])
        const safe: Record<string, WorkspaceAgentTask> = {}
        for (const task of result.tasks) {
          if (!task || typeof task.document_id !== 'string' || !workspaceDocumentIds.split(',').includes(task.document_id) || typeof task.review_id !== 'string' || typeof task.format_run_id !== 'string' || !allowed.has(task.status) || !Number.isInteger(task.pending_count) || !Number.isInteger(task.finding_count) || typeof task.updated_at !== 'string') continue
          safe[task.document_id] = { ...task, status: task.status as WorkspaceAgentTask['status'] }
        }
        setAgentTasks(safe)
        setTaskStatusError(false)
      } catch { if (!stopped) setTaskStatusError(true) }
      finally { pendingRequest = false }
    }
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 4000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [workspaceDocumentIds])
  function draftSnapshot(): CloseSnapshot | null {
    const current = workspaceRef.current, instance = editorRef.current
    if (!current || !instance) return null
    return { id: current.id, name: nameRef.current.trim() || '未命名文档.md', content: readContent(), editor_json: { ...instance.getJSON(), markdownSchema: MARKDOWN_SCHEMA_VERSION }, expected_revision: current.revision, last_run_id: lastRunRef.current, profile_id: profileRef.current }
  }
  async function flushDraft(): Promise<void> {
    if (savePromiseRef.current) { await savePromiseRef.current; if (saveRequiredRef.current) return flushDraft(); return }
    if (!saveRequiredRef.current || !workspaceRef.current) return
    if (saveConflictRef.current) throw new Error('另一窗口已修改此文档。请先导出当前副本，再重新打开文档。')
    if (autoSaveTimerRef.current) { clearTimeout(autoSaveTimerRef.current); autoSaveTimerRef.current = null }
    const pendingSave = (async () => {
      window.__thesisSavePending = true; setSaveState('saving'); setSaveError('')
      try {
        while (saveRequiredRef.current) {
          const snapshot = draftSnapshot(); if (!snapshot) return
          const serial = saveSerialRef.current
          const { id, ...payload } = snapshot
          const saved = await requestJson<WorkspaceDocument>('/workspace/documents/' + encodeURIComponent(id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
          if (workspaceRef.current?.id !== snapshot.id) return
          workspaceRef.current = saved; updateLibrary(saved)
          if (serial === saveSerialRef.current) { saveRequiredRef.current = false; setSaveState('saved'); nameRef.current = saved.name; setDocumentName(saved.name) }
        }
      } catch (error) {
        const conflict = (error as Error & { status?: number }).status === 409
        saveConflictRef.current = conflict; setSaveState(conflict ? 'conflict' : 'error')
        setSaveError(conflict ? '另一窗口已修改此文档。当前内容仍在，请先导出副本再重新打开。' : '尚未保存。请检查连接后重试，当前内容仍在。')
        throw error
      } finally { window.__thesisSavePending = false }
    })()
    savePromiseRef.current = pendingSave
    try { await pendingSave } finally { if (savePromiseRef.current === pendingSave) savePromiseRef.current = null }
  }
  useEffect(() => {
    if (!saveRequiredRef.current || closePausedRef.current || saveConflictRef.current) return
    autoSaveTimerRef.current = setTimeout(() => { autoSaveTimerRef.current = null; void flushDraft().catch(() => undefined) }, 700)
    return () => { if (autoSaveTimerRef.current) { clearTimeout(autoSaveTimerRef.current); autoSaveTimerRef.current = null } }
  }, [saveTick])
  useEffect(() => {
    window.__thesisSavePending = false
    window.__thesisCloseSnapshot = () => { closePausedRef.current = true; if (autoSaveTimerRef.current) { clearTimeout(autoSaveTimerRef.current); autoSaveTimerRef.current = null } return saveRequiredRef.current ? draftSnapshot() : null }
    window.__thesisCloseResume = (message: string) => { closePausedRef.current = false; setSaveError(message); setSaveState(saveConflictRef.current ? 'conflict' : 'error'); setSaveTick(value => value + 1) }
    const unload = (event: BeforeUnloadEvent) => { if (closePausedRef.current) return; if (saveRequiredRef.current || window.__thesisSavePending) { void flushDraft().catch(() => undefined); event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', unload)
    return () => { delete window.__thesisCloseSnapshot; delete window.__thesisCloseResume; delete window.__thesisSavePending; window.removeEventListener('beforeunload', unload) }
  }, [])
  useEffect(() => { editor?.setEditable(!busy && !workspaceLoading && !!activeDocumentId, false) }, [editor, busy, workspaceLoading, activeDocumentId])
  useEffect(() => {
    if (!editor || initializedRef.current) return
    initializedRef.current = true
    const token = beginOperation(); if (token === null) return
    void (async () => {
      try {
        const workspace = await requestJson<{ documents: WorkspaceSummary[]; active_document_id: string | null }>('/workspace')
        setDocuments(workspace.documents)
        const selected = workspace.documents.find(item => item.id === workspace.active_document_id) || workspace.documents[0]
        if (selected) await activateDocument(await requestJson<WorkspaceDocument>('/workspace/documents/' + encodeURIComponent(selected.id)), token)
      } catch (error) { reportError(error, token) }
      finally { setWorkspaceLoading(false); finishOperation(token) }
    })()
  }, [editor])
  function beginOperation() { if (busyRef.current) return null; busyRef.current = true; setBusy(true); setRequestError(''); return ++operationRef.current }
  function finishOperation(token: number) { if (token !== operationRef.current) return; busyRef.current = false; setBusy(false); setScanState('') }
  function requireCurrentEditor(token: number, revision: number) { if (token !== operationRef.current || revision !== revisionRef.current) throw new Error('文档已变化，请重新扫描。') }
  function reportError(error: unknown, token: number) { if (token === operationRef.current) setRequestError(error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '连接失败，当前内容已保留。') }
  function requireFreshRun() { if (!runId || !review) { notify('请先扫描文档'); return false } if (dirtyRef.current) { setRequestError('正文已修改，请重新扫描。'); return false } if (saveConflictRef.current) { setRequestError('请先导出当前副本并处理保存冲突。'); return false } return true }
  function mapApiChange(item: ApiChange, source: string): Change { return { id: item.id, title: item.property, detail: item.explanation, before: item.before, after: item.after, kind: item.kind, status: item.status, ...changeLocation(source, item.anchor?.start, item.anchor?.quote || '', item.property), start: item.anchor?.start, end: item.anchor?.end, replacement: item.replacement, rule: item.rule_id, decidedAt: item.decided_at } }
  async function readRun(currentRunId: string) {
    const [apiChanges, apiPreview, apiReview, apiSuggested] = await Promise.all([requestJson<ApiChange[]>('/format-runs/' + currentRunId + '/changes'), requestJson<PreviewResponse>('/format-runs/' + currentRunId + '/preview'), requestJson<ReviewResponse>('/format-runs/' + currentRunId + '/review'), requestJson<{preview:string}>('/format-runs/' + currentRunId + '/suggested-preview')])
    return { apiChanges, apiPreview, apiReview, apiSuggested }
  }
  function displayRun(currentRunId: string, result: Awaited<ReturnType<typeof readRun>>) {
    const mapped = result.apiChanges.map(item => mapApiChange(item, result.apiPreview.source ?? readContent()))
    setRunId(currentRunId); setChanges(mapped); setActiveId(mapped.find(item => item.status === 'pending')?.id || mapped[0]?.id || ''); setPreviewData(result.apiPreview); setReview(result.apiReview)
    setSuggestedContent(result.apiSuggested.preview)
    return mapped
  }
  async function activateDocument(document: WorkspaceDocument, token: number) {
    await requestJson('/workspace/active', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: document.id }) })
    if (token !== operationRef.current) return
    workspaceRef.current = document; nameRef.current = document.name; lastRunRef.current = document.last_run_id; headHashRef.current = document.head_hash
    profileRef.current = document.profile_id || 'generic-thesis'; setSelectedProfileId(profileRef.current)
    markdownRef.current = document.content; saveRequiredRef.current = false; saveConflictRef.current = false; closePausedRef.current = false
    revisionRef.current += 1; dirtyRef.current = !!document.content.trim(); setHasLocalEdits(dirtyRef.current)
    setActiveDocumentId(document.id); setDocumentName(document.name); setSaveState('saved'); setSaveError(''); setUploadWarnings([]); setLatex('')
    setDocumentStyle(DEFAULT_DOCUMENT_STYLE); setSuggestedContent(null)
    setRunId(null); setReview(null); setPreviewData(null); setChanges([]); setActiveId(''); setAgentReview(null); setDocumentEpoch(value => value + 1)
    editorRef.current?.commands.setContent(migrateLegacyDocument(document.content, document.editor_json), { emitUpdate: false })
    if (editorRef.current) refreshOutline(editorRef.current)
    updateLibrary(document)
    if (document.last_run_id) {
      try { const result = await readRun(document.last_run_id); if (token !== operationRef.current) return; displayRun(document.last_run_id, result); dirtyRef.current = document.content !== (result.apiPreview.preview ?? result.apiPreview.source ?? ''); setHasLocalEdits(dirtyRef.current) }
      catch { if (token === operationRef.current) setRequestError('上次扫描记录暂时无法读取，正文已恢复。') }
    }
  }
  async function switchDocument(id: string) {
    if (id === workspaceRef.current?.id) return
    const token = beginOperation(); if (token === null) return
    try { await flushDraft(); const document = await requestJson<WorkspaceDocument>('/workspace/documents/' + encodeURIComponent(id)); await activateDocument(document, token) } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  useEffect(() => {
    const controller = new AbortController()
    void listFormatProfiles(API_BASE, controller.signal).then(profiles => {
      setProfileNames(Object.fromEntries(profiles.map(profile => [profile.id, profile.name])))
      setProfileStyles(Object.fromEntries(profiles.map(profile => [profile.id, profile.document_style ?? null])))
    }).catch(() => undefined)
    return () => controller.abort()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    if (activeDocumentId) void requestJson<{style:DocumentStyle}>('/workspace/documents/' + encodeURIComponent(activeDocumentId) + '/layout', {signal:controller.signal}).then(result => { if (!controller.signal.aborted) setDocumentStyle(result.style) }).catch(() => undefined)
    return () => controller.abort()
  }, [activeDocumentId])
  async function selectSchoolTemplate(id: string, profile: FormatProfile) {
    const token = beginOperation(); if (token === null) throw new Error('请等待当前操作完成。')
    try {
      await flushDraft()
      setProfileNames(names => ({ ...names, [id]: profile.name }))
      setProfileStyles(styles => ({ ...styles, [id]: profile.document_style ?? null }))
      if (profileRef.current !== id) {
        profileRef.current = id; setSelectedProfileId(id)
        if (workspaceRef.current) {
          revisionRef.current += 1; dirtyRef.current = true; setHasLocalEdits(true)
          lastRunRef.current = null; setRunId(null); setReview(null); setChanges([]); setActiveId(''); setPreviewData(null)
          setAgentReview(null); setSelectedReviewId(null); setDocumentEpoch(value => value + 1)
          markDraftChanged(); await flushDraft()
        }
      }
      setTemplatesOpen(false); notify('已选用“' + profile.name + '”，运行格式扫描后查看建议')
      if (profile.document_style && workspaceRef.current) setStudioOpen(true)
    } catch (error) { reportError(error, token); throw error }
    finally { finishOperation(token) }
  }
  async function openDocumentReview(id: string) {
    if (id !== activeDocumentId) { setSelectedReviewId(null); await switchDocument(id); if (workspaceRef.current?.id !== id) return }
    setSelectedReviewId(agentTasks[id]?.review_id ?? null)
    openReviewPanel('agents')
  }
  function agentTaskLabel(task: WorkspaceAgentTask) {
    if (task.status === 'queued') return '排队中'
    if (task.status === 'running') return '审查中'
    if (task.status === 'failed') return '审查失败'
    if (task.status === 'cancelled') return '已取消'
    if (task.status === 'partial') return task.pending_count ? '部分完成 · 待处理 ' + task.pending_count : '部分完成'
    return task.pending_count ? '待处理 ' + task.pending_count : '已完成'
  }
  function visibleDocuments() {
    const query = libraryQuery.trim().toLocaleLowerCase()
    return query ? documents.filter(document => document.name.toLocaleLowerCase().includes(query)) : documents
  }
  async function reopenSavedDocument() {
    const id = workspaceRef.current?.id
    if (!id || busyRef.current || !window.confirm('重新打开将放弃当前未保存内容。请先导出需要保留的副本。')) return
    const token = beginOperation(); if (token === null) return
    try { const document = await requestJson<WorkspaceDocument>('/workspace/documents/' + encodeURIComponent(id)); await activateDocument(document, token) } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function createDocument() {
    const token = beginOperation(); if (token === null) return
    try { await flushDraft(); const document = await requestJson<WorkspaceDocument>('/workspace/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: '未命名文档.md', content: '', profile_id: profileRef.current, editor_json: { type: 'doc', content: [{ type: 'paragraph' }] } }) }); await activateDocument(document, token); editorRef.current?.commands.focus() } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function handleFileUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
    if (!/\.(md|markdown|txt|docx)$/i.test(file.name)) { setRequestError('支持导入 Markdown、TXT 和 DOCX 文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setRequestError('文件不能超过 10 MB。'); return }
    const token = beginOperation(); if (token === null) return
    try {
      await flushDraft()
      const document = await requestJson<WorkspaceDocument & { source_type: string; warnings: string[] }>('/workspace/import?filename=' + encodeURIComponent(file.name) + '&profile_id=' + encodeURIComponent(profileRef.current), { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })
      await activateDocument(document, token)
      setUploadWarnings(document.warnings || [])
      notify('已导入 ' + document.name)
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function refreshRun(currentRunId: string, token: number, revision: number) {
    const result = await readRun(currentRunId); requireCurrentEditor(token, revision)
    const mapped = displayRun(currentRunId, result)
    lastRunRef.current = currentRunId; headHashRef.current = result.apiReview.preview_hash
    const previousContent = readContent()
    markdownRef.current = result.apiPreview.preview ?? result.apiPreview.source ?? ''; dirtyRef.current = false; setHasLocalEdits(false)
    if (markdownRef.current !== previousContent) editorRef.current?.commands.setContent(parseMarkdown(markdownRef.current), { emitUpdate: false })
    if (editorRef.current) refreshOutline(editorRef.current)
    markDraftChanged(); await flushDraft()
    return { count: mapped.length, preview: result.apiPreview }
  }
  async function scanFormat() {
    if (!editor || !workspaceRef.current || !readContent().trim()) return
    const token = beginOperation(); if (token === null) return
    const revision = revisionRef.current; setScanState('扫描中…')
    try {
      await flushDraft(); requireCurrentEditor(token, revision)
      const run = await requestJson<FormatRunIdentity>('/format-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profileRef.current, document_id: workspaceRef.current.id, document_version_id: 'draft-' + workspaceRef.current.revision, document_name: nameRef.current, document_type: 'markdown', content: readContent(), ...(headHashRef.current ? { expected_head_hash: headHashRef.current } : {}) }) })
      requireCurrentEditor(token, revision); const refreshed = await refreshRun(run.id, token, revision); notify('发现 ' + refreshed.count + ' 条修改建议')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function decide(change: Change, decision: 'accept' | 'reject') {
    if (!requireFreshRun()) return; const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try { await flushDraft(); await requestJson('/format-changes/' + change.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, expected_source_hash: review?.source_hash }) }); await refreshRun(runId!, token, revision); notify(decision === 'accept' ? '已接受修改' : '已保留原文') } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function decideGroup(candidates: Change[], decision: 'accept' | 'reject') {
    if (!requireFreshRun()) return
    if (!candidates.length) return
    if (candidates.length > 500) { notify('每批最多 500 项，请按组处理。'); return }
    const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try {
      await flushDraft()
      requireCurrentEditor(token, revision)
      const result = await requestJson<{batch_id: string; count: number}>('/format-runs/' + runId + '/batch-decisions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, change_ids: candidates.map(change => change.id), expected_source_hash: review?.source_hash, expected_preview_hash: review?.preview_hash }),
      })
      await refreshRun(runId!, token, revision)
      setLastBatch({ runId: runId!, batchId: result.batch_id })
      notify((decision === 'accept' ? '已接受并复检 ' : '已保留 ') + result.count + ' 项；可撤销本批')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function acceptAllPending() {
    const blocked = new Set(review?.blocked_change_ids ?? []), conflicts = conflictingChangeIds(pending)
    await decideGroup(pending.filter(change => !blocked.has(change.id) && !conflicts.has(change.id) && LOW_RISK_RULES.includes(change.rule)), 'accept')
  }
  async function undoBatch() {
    if (!requireFreshRun() || !lastBatch || lastBatch.runId !== runId) return
    const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try {
      await flushDraft()
      await requestJson('/format-runs/' + runId + '/batch-decisions', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({decision:'undo',batch_id:lastBatch.batchId,expected_source_hash:review?.source_hash,expected_preview_hash:review?.preview_hash})})
      await refreshRun(runId!, token, revision); setLastBatch(null); notify('已撤销整个格式修改批次')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function undoLast() {
    if (!requireFreshRun()) return; const last = accepted[accepted.length - 1]; if (!last) return
    const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try { await flushDraft(); await requestJson('/format-changes/' + last.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision: 'undo', expected_source_hash: review?.source_hash }) }); await refreshRun(runId!, token, revision); notify('已撤销修改') } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function restoreOriginal() {
    if (!requireFreshRun()) return; const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try { await flushDraft(); await requestJson('/format-runs/' + runId + '/restore', { method: 'POST' }); await refreshRun(runId!, token, revision); notify('已恢复本次扫描原稿') } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function applyAgentVersion(action: () => Promise<AgentVersionResult>): Promise<AgentVersionResult> {
    if (!requireFreshRun()) throw new Error('请先扫描当前文档。')
    const token = beginOperation(); if (token === null) throw new Error('请等待当前操作完成。'); const revision = revisionRef.current
    try { await flushDraft(); const result = await action(); requireCurrentEditor(token, revision); if (result.format_run_id) { await refreshRun(result.format_run_id, token, revision); notify('已保存为新版本') } return result } catch (error) { reportError(error, token); throw error } finally { finishOperation(token) }
  }
  function insertFormula() {
    if (!editor || !activeDocumentId || !latex.trim() || busy) return
    const source = latex.trim()
    const insertionAnchor = editor.state.selection.from
    const inserted = editor.chain().focus().insertContent({ type: 'blockMath', attrs: { latex: source } }).run()
    if (!inserted) return
    // The block-math node is atomic; select it and scroll it into view so its
    // insertion point is unambiguous rather than leaving focus in the toolbar.
    let insertedPosition: number | null = null
    let nearestDistance = Number.POSITIVE_INFINITY
    editor.state.doc.descendants((node, position) => {
      if (node.type.name !== 'blockMath' || node.attrs.latex !== source) return
      const distance = Math.abs(position - insertionAnchor)
      if (distance < nearestDistance) { insertedPosition = position; nearestDistance = distance }
    })
    if (insertedPosition !== null) editor.chain().focus().setNodeSelection(insertedPosition).scrollIntoView().run()
    setFormulaNotice('已插入公式：光标已定位到正文中的公式块')
    setLatex('')
    window.setTimeout(() => setFormulaNotice(''), 3200)
  }
  function locateAgentQuote(quote: string, occurrence?: number) {
    if (!editor || !quote.trim()) return
    let text = ''
    const positions: { from: number; to: number }[] = []
    editor.state.doc.descendants((block, position) => {
      if (!block.isTextblock) return
      if (positions.length) { text += '\n'; positions.push({ from: position, to: position }) }
      block.descendants((node, offset) => {
        const value = node.isText ? node.text || '' : node.type.name === 'hardBreak' ? '\n' : String(node.attrs.latex || node.attrs.alt || '')
        const from = position + 1 + offset
        for (let index = 0; index < value.length; index += 1) positions.push({ from: node.isText ? from + index : from, to: node.isText ? from + index + 1 : from + node.nodeSize })
        text += value
      })
      return false
    })
    const candidates = [quote.trim()]
    try { const fragment = editor.schema.nodeFromJSON(parseMarkdown(quote)); candidates.push(fragment.textBetween(0, fragment.content.size, '\n', node => String(node.attrs.latex || node.attrs.alt || '')).trim()) } catch { /* Keep literal matching available for incomplete Markdown quotes. */ }
    const matches: { from: number; to: number }[] = []
    for (const candidate of new Set(candidates.filter(Boolean))) {
      const candidateMatches: { from: number; to: number }[] = []
      let start = 0
      while (start < text.length) {
        const found = text.indexOf(candidate, start); if (found < 0) break
        const match = { from: positions[found].from, to: positions[found + candidate.length - 1].to }
        candidateMatches.push(match)
        if (!matches.some(item => item.from === match.from && item.to === match.to)) matches.push(match)
        start = found + candidate.length
      }
      if (occurrence !== undefined && candidateMatches[occurrence]) { editor.chain().focus().setTextSelection(candidateMatches[occurrence]).scrollIntoView().run(); return }
    }
    if (matches.length !== 1) return notify(matches.length > 1 ? '原文有多处匹配，请结合证据定位。' : '当前正文未找到该原文。')
    editor.chain().focus().setTextSelection(matches[0]).scrollIntoView().run()
  }
  function focusChange(change: Change) { setActiveId(change.id); if (change.anchor.trim()) locateAgentQuote(change.anchor.trim(), change.occurrence) }
  function download(content: string, filename: string, contentType: string) { const url = URL.createObjectURL(new Blob([content], { type: contentType })); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000) }
  function exportMarkdown() { if (!activeDocumentId || !hasContent || busyRef.current) return; download(readContent(), (nameRef.current || '文档').replace(/\.[^.]+$/, '') + '.md', 'text/markdown;charset=utf-8') }
  function exportReport() { if (!requireFreshRun() || busyRef.current) return; download(JSON.stringify({ document_name: nameRef.current, review, changes, preview: previewData, agent_review: agentReview, agent_review_matches_current_snapshot: !!agentReview && agentReview.format_run_id === runId && agentReview.preview_hash === review?.preview_hash && agentReview.source_hash === review?.source_hash }, null, 2), nameRef.current.replace(/\.[^.]+$/, '') + '-修订报告.json', 'application/json;charset=utf-8') }
  function renameDocument(value: string) { nameRef.current = value; setDocumentName(value); markDraftChanged() }
  function goToHeading(item: OutlineItem) { editor?.chain().focus().setTextSelection(item.position + 1).scrollIntoView().run() }
  return (
    <main className="app-shell" data-workspace-ready={!workspaceLoading ? 'true' : 'false'}>
      <input ref={fileInputRef} type="file" accept=".md,.markdown,.txt,.docx" hidden onChange={handleFileUpload} />
      <header className="topbar">
        <div className="brand"><button type="button" className="icon-button layout-toggle" onClick={toggleNavigation} aria-expanded={leftRailOpen} aria-controls="document-sidebar" aria-label={leftRailOpen ? '收起文档侧栏' : '展开文档侧栏'} title={leftRailOpen ? '收起文档侧栏' : '展开文档侧栏'} disabled={!activeDocumentId}><PanelLeft size={19}/></button><div className="brand-name">研辩台 <span>ThesisDebate</span></div></div>
        <div className="top-actions">
          <AgentChannelSwitcher apiBase={API_BASE} onManage={() => setSettingsOpen(true)} disabled={busy}/>
          <button type="button" className="ghost-button compact-action" onClick={() => openReviewPanel('agents')} disabled={!activeDocumentId} aria-controls="review-sidebar"><Layers3 size={16}/>Agent 审查</button>
          <button type="button" className="ghost-button compact-action" onClick={() => openReviewPanel('assistance')} disabled={!activeDocumentId} aria-controls="review-sidebar"><History size={16}/>返修助手</button>
          <details className="workspace-tools" ref={toolsMenuRef}><summary className="ghost-button" aria-label="打开工具菜单"><MoreHorizontal size={18}/><span>工具</span></summary><div className="workspace-tools-menu" aria-label="文档工具">
            <span className="tools-menu-label">文档</span>
            <button type="button" onClick={() => runTool(() => { void createDocument() })} disabled={busy || workspaceLoading}><FilePlus2 size={16}/>新建文档</button>
            <button type="button" onClick={() => runTool(() => fileInputRef.current?.click())} disabled={busy || workspaceLoading}><FileUp size={16}/>导入文档</button>
            <button type="button" onClick={() => runTool(() => setTemplatesOpen(true))} disabled={busy || workspaceLoading}><FileText size={16}/>文档模板</button>
            <button type="button" onClick={() => runTool(() => setStudioOpen(true))} disabled={!activeDocumentId || busy || workspaceLoading}><ArrowDownToLine size={16}/>预期效果与导出</button>
            <span className="tools-menu-label">显示与修订</span>
            <button type="button" onClick={() => runTool(() => setShowDiff(value => !value))} disabled={!activeDocumentId} aria-pressed={showDiff}><GitCompare size={16}/>{showDiff ? '收起正文对比' : '显示正文对比'}</button>
            <button type="button" onClick={() => runTool(() => setFormulaOpen(value => !value))} disabled={!activeDocumentId} aria-pressed={formulaOpen}><Sigma size={16}/>{formulaOpen ? '收起公式工具' : '插入公式'}</button>
            <button type="button" onClick={() => runTool(restoreOriginal)} disabled={!canDecide}><History size={16}/>恢复本次原稿</button>
            <button type="button" onClick={() => runTool(exportReport)} disabled={!canDecide || !hasContent}><ArrowDownToLine size={16}/>导出修订报告</button>
            <span className="tools-menu-label">设置</span>
            <button type="button" onClick={() => runTool(() => setSettingsOpen(true))} aria-label="打开 Agent 设置"><Settings size={16}/>Agent 设置</button>
          </div></details>
        </div>
      </header>
      {!activeDocumentId ? <section className="workspace-welcome" data-testid="workspace-empty">
        {workspaceLoading ? <p role="status">正在打开工作区…</p> : <div className="welcome-card"><div className="welcome-mark"><FileText size={30} /></div><h1>开始一篇新文档</h1><p>新建文档，或导入 Markdown、TXT、DOCX 文件。</p><div className="welcome-actions"><button className="primary-button" onClick={createDocument} disabled={busy}><FilePlus2 size={16} />新建文档</button><button className="soft-button" onClick={() => fileInputRef.current?.click()} disabled={busy}><FileUp size={16} />导入文档</button></div>{requestError && <div className="review-alert" role="alert">{requestError}</div>}{documents.length > 0 && <div className="welcome-documents"><h2>最近文档</h2>{documents.map(document => <div className="welcome-document-row" key={document.id}><button onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={14}/><span>{document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}</div>}</div>}
      </section> : <>
        <section className="workspace-toolbar"><div className="crumbs"><strong>文档工作区</strong><span className={'status-pill save-' + saveState} role="status">{saveLabels[saveState]}</span>{hasLocalEdits && runId && <span className="draft-indicator">正文已修改</span>}</div><div className="toolbar-actions"><button type="button" className="ghost-button compact-action" onClick={() => rightPanelOpen ? setRightPanelOpen(false) : openReviewPanel(tab)} aria-expanded={rightPanelOpen} aria-controls="review-sidebar"><PanelRight size={16}/>{rightPanelOpen ? '收起审查' : '展开审查'}</button><button className="primary-button" onClick={scanFormat} disabled={busy || !hasContent || saveConflictRef.current}><ScanLine size={16}/>{scanState || '扫描格式'}</button></div></section>
        <div className={'workspace-grid' + (!leftRailOpen ? ' nav-collapsed' : '') + (!rightPanelOpen ? ' review-collapsed' : '')}>
          {(leftRailOpen || rightPanelOpen) && <button type="button" className="panel-backdrop" aria-label="关闭侧栏，返回正文" onClick={() => { setLeftRailOpen(false); setRightPanelOpen(false) }}/>}
          <aside className="sidebar left-sidebar" id="document-sidebar" aria-label="文档与目录">
            <div className="side-head"><h2>我的文档</h2><button className="icon-button" onClick={() => setLibraryOpen(value => !value)} aria-label={libraryOpen ? '折叠文档列表' : '展开文档列表'} aria-expanded={libraryOpen}><ChevronDown size={16} className={!libraryOpen ? 'collapsed-chevron' : ''}/></button></div>
            {libraryOpen && <><label className="library-search-label" htmlFor="document-search">搜索文档</label><input id="document-search" className="library-search" type="search" value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="按名称筛选" aria-label="搜索文档"/><nav className="document-library" aria-label="文档列表">{visibleDocuments().map(document => <div className="library-document-row" key={document.id}><button className={'library-document ' + (document.id === activeDocumentId ? 'active' : '')} onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={15}/><span title={document.name}>{document.id === activeDocumentId ? documentName || '未命名文档' : document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'} title={agentTasks[document.id].review_id}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}{visibleDocuments().length === 0 && <p className="review-empty">没有匹配的文档。</p>}</nav>{taskStatusError && <p className="task-status-error" role="status">审查状态暂不可用，请检查本地服务连接。</p>}</>}
            <div className="section-heading outline-heading"><span>文档目录 · {outline.length ? outline.length + ' 个标题' : '暂无标题'}</span><button className="icon-button" onClick={() => setOutlineOpen(value => !value)} aria-label={outlineOpen ? '折叠文档目录' : '展开文档目录'} aria-expanded={outlineOpen}><ChevronDown size={16} className={!outlineOpen ? 'collapsed-chevron' : ''}/></button></div>
            {outlineOpen && <nav className="outline" aria-label="文档目录">{outline.length ? outline.map((item, index) => <button key={index} className="outline-item" style={{paddingLeft: 12 + Math.min(item.level - 1, 4) * 12}} onClick={() => goToHeading(item)} title={item.title}><span>{item.title}</span></button>) : <p className="review-empty">正文中的标题会显示在这里。</p>}</nav>}
          </aside>
          <section className="editor-panel">
            <div className="editor-head"><div className="document-heading"><label htmlFor="document-name">文档名称</label><input id="document-name" aria-label="文档名称" value={documentName} onChange={event => renameDocument(event.target.value)} onBlur={() => { void flushDraft().catch(() => undefined) }} disabled={busy}/></div><button className="soft-button" onClick={() => { void flushDraft().then(() => notify('已保存')).catch(() => undefined) }} disabled={busy || saveState === 'saving' || saveState === 'conflict'}><Save size={14}/>保存</button></div>
            <div className="active-template"><span>文档模板：{profileNames[selectedProfileId] || selectedProfileId}</span><button type="button" className="ghost-button" onClick={() => setTemplatesOpen(true)} disabled={busy}>切换模板</button></div>
            {requestError && <div className="review-alert" role="alert">{requestError}</div>}{saveError && <div className="review-alert" role="alert">{saveError}{saveState === 'error' && <button onClick={() => { void flushDraft().catch(() => undefined) }}>重试保存</button>}{saveState === 'conflict' && <><button onClick={exportMarkdown} disabled={!hasContent}>导出当前副本</button><button onClick={reopenSavedDocument}>重新打开已保存版本</button></>}</div>}
            {hasLocalEdits && runId && <div className="review-alert warning" role="status">正文已修改，重新扫描后可继续处理建议。</div>}{uploadWarnings.length > 0 && <details className="import-notice"><summary>导入说明</summary><ul>{uploadWarnings.map(warning => <li key={warning}>{warning}</li>)}</ul></details>}
            <div className="paper-wrap"><article className="paper styled-manuscript" style={documentStyleCss(documentStyle)}><EditorContent editor={editor}/>{!hasContent && <p className="editor-placeholder">在这里输入正文，或导入文档开始编辑。</p>}</article></div>
            <div className="formula-bar" hidden={!formulaOpen}><div className="formula-label-left"><Sigma size={16}/>LaTeX 公式</div><input value={latex} onChange={event => setLatex(event.target.value)} placeholder="输入 LaTeX 公式" aria-label="LaTeX 公式源码" disabled={busy}/><button className="insert-button" onClick={insertFormula} disabled={busy || !latex.trim()}>插入公式</button><button type="button" className="icon-button" onClick={() => setFormulaOpen(false)} aria-label="收起公式工具"><X size={15}/></button>{formulaNotice && <span className="formula-notice" role="status"><Check size={14}/>{formulaNotice}</span>}</div>
            <div className="format-diff-container" hidden={!showDiff}><FormatDiffPanel apiBase={API_BASE} runId={runId} review={review} pendingCount={pending.length} dirty={hasLocalEdits} disabled={busy}/></div>
          </section>
          <aside className="sidebar right-sidebar" id="review-sidebar" aria-label="审查工作区"><div className="review-rail-head"><span>审查工作区</span><button type="button" className="icon-button" onClick={() => setRightPanelOpen(false)} aria-label="收起审查面板"><PanelRight size={16}/></button></div>
            <div className="review-progress"><h2>{hasLocalEdits && runId ? '等待重新扫描' : review ? reviewStatus[review.status] : '修订建议'}</h2><div className="review-counts"><span>{review?.applied_count ?? 0} 项已应用</span>{!!review?.blocked_change_ids.length && <span>{review.blocked_change_ids.length} 项冲突</span>}</div></div>
            <div className="assistant-entry"><button type="button" onClick={() => setStudioOpen(true)} disabled={busy || !hasContent}><Settings size={15}/><span><strong>Agent 对话排版</strong><small>告诉我字体、字号和间距要求，先看预期效果</small></span></button></div><div className="tabs"><button className={tab === 'changes' ? 'tab active' : 'tab'} onClick={() => setTab('changes')}>修改建议{changes.length > 0 && <span>{pending.length}</span>}</button><button className={tab === 'agents' ? 'tab active' : 'tab'} onClick={() => setTab('agents')}>Agent 审查</button><button className={tab === 'review' ? 'tab active' : 'tab'} onClick={() => setTab('review')}>修订检查</button><button className={tab === 'assistance' ? 'tab active' : 'tab'} onClick={() => setTab('assistance')}>返修助手</button></div>
            <AgentReviewPanel apiBase={API_BASE} runId={runId} sourceHash={review?.source_hash ?? ''} previewHash={review?.preview_hash ?? ''} documentEpoch={documentEpoch} dirty={hasLocalEdits} busy={busy} hidden={tab !== 'agents'} selectedReviewId={selectedReviewId} applyVersion={applyAgentVersion} onLocate={locateAgentQuote} onReviewChange={setAgentReview}/>
            {tab === 'assistance' && <DocumentAssistant apiBase={API_BASE} documentId={activeDocumentId} documentName={documentName} content={readContent()} profileId={selectedProfileId} documentEpoch={documentEpoch} onLocate={locateAgentQuote}/>}
            {tab === 'changes' ? <>
              <div className="change-list">
                {!!pending.length && <button type="button" className="bulk-accept-button" onClick={acceptAllPending} disabled={!canDecide || !pending.some(change => !review?.blocked_change_ids.includes(change.id) && ['paragraph.spacing','heading.spacing','heading.numbering'].includes(change.rule))} title="先在对比中检查；仅整批接受标题编号及空白格式项，引用和内容建议逐项审阅">确认低风险格式修改 <span>{pending.filter(change => !review?.blocked_change_ids.includes(change.id) && ['paragraph.spacing','heading.spacing','heading.numbering'].includes(change.rule)).length}</span></button>}
                {lastBatch?.runId === runId && <button type="button" className="ghost-button" onClick={undoBatch} disabled={!canDecide}>撤销本批格式修改</button>}
                {!changes.length && <p className="review-empty">{runId ? '未发现需要修改的内容。' : '扫描当前文档以查看修改建议。'}</p>}
                <GroupedChanges changes={pending} activeId={activeId} contextKey={String(runId) + ':' + documentEpoch} disabled={!canDecide} blockedIds={review?.blocked_change_ids ?? []} onLocate={focusChange} onDecide={(change, decision) => { void decide(change, decision) }} onBatch={(items, decision) => { void decideGroup(items, decision) }}/>
                {!!accepted.length && <details className="resolved-changes"><summary>已接受 · {accepted.length} 项</summary>{accepted.map(change => <button type="button" key={change.id} className="resolved-card accepted" onClick={() => focusChange(change)}><Check size={14}/><span>{change.title}</span><small>已接受</small></button>)}</details>}
                {!!changes.filter(item => item.status === 'rejected').length && <details className="resolved-changes"><summary>已保留 · {changes.filter(item => item.status === 'rejected').length} 项</summary>{changes.filter(item => item.status === 'rejected').map(change => <button type="button" key={change.id} className="resolved-card rejected" onClick={() => focusChange(change)}><X size={14}/><span>{change.title}</span><small>已保留</small></button>)}</details>}
              </div>
            </> : tab === 'review' ? <div className="debate-list" aria-label="修订检查报告">{!review ? <p className="review-empty">请先扫描当前文档。</p> : <><div className={'judge-note review-result-' + review.status}>{reviewStatus[review.status]}：{review.summary}</div>{review.checks.map(check => <div className="agent-row" key={check.id}><div className={'agent-avatar ' + (check.status === 'passed' ? 'teal' : 'amber')}>{check.status === 'passed' ? <Check size={13}/> : <AlertTriangle size={13}/>}</div><div><div className="agent-name">{check.title}<span className="review-check-status">{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '待处理'}</span></div><p>{check.detail}</p></div></div>)}</>}</div> : null}
          </aside>
        </div>
        <footer className="statusbar"><div><span className={'save-status save-' + saveState}>{saveLabels[saveState]}</span>{hasContent && <span>{editor?.getText().replace(/\s/g, '').length ?? 0} 字</span>}</div><div><button onClick={undoLast} disabled={!canDecide || !accepted.length}><RotateCcw size={13}/>撤销</button><button onClick={() => setStudioOpen(true)} disabled={!hasContent || busy}><ArrowDownToLine size={13}/>导出</button></div></footer>
      </>}
      <DocumentStudio apiBase={API_BASE} open={studioOpen} onClose={() => setStudioOpen(false)} documentId={activeDocumentId} documentName={documentName} content={readContent()} originalContent={hasLocalEdits ? null : previewData?.source ?? null} suggestedContent={hasLocalEdits ? null : suggestedContent} profileStyle={profileStyles[selectedProfileId] ?? null} documentEpoch={documentEpoch} disabled={busy || workspaceLoading || saveState === 'conflict'} onStyleChange={setDocumentStyle}/>
      <AgentSettings apiBase={API_BASE} open={settingsOpen} onClose={() => setSettingsOpen(false)}/>
      <SchoolTemplatePanel apiBase={API_BASE} open={templatesOpen} selectedProfileId={selectedProfileId} onClose={() => setTemplatesOpen(false)} onSelect={selectSchoolTemplate} disabled={busy}/>
      {toast && <div className="toast"><Check size={15}/>{toast}</div>}
    </main>
  )
}
