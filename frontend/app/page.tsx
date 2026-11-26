'use client'
import { useEffect, useRef, useState } from 'react'
import { useTheme } from 'next-themes'
import { Moon, Sun, ArrowUpRight, ChevronRight, BookOpen, ShieldCheck, ArrowRight, CircleCheck, MessageSquareText } from 'lucide-react'
import { EditorContent, useEditor, type Editor } from '@tiptap/react'
import { EditorState } from '@tiptap/pm/state'
import { editorExtensions, MARKDOWN_SCHEMA_VERSION, migrateLegacyDocument, parseMarkdown, serializeMarkdown, type StoredEditorDocument } from '../lib/markdown'
import AgentReviewPanel, { type AgentReview, type AgentVersionResult } from '../components/AgentReviewPanel'
import { TextDiff } from '../components/RevisionDiff'
import AgentSettings from '../components/AgentSettings'
import SchoolTemplatePanel from '../components/SchoolTemplatePanel'
import FormatDiffPanel from '../components/FormatDiffPanel'
import DocumentStudio from '../components/DocumentStudio'
import { DEFAULT_DOCUMENT_STYLE, documentStyleCss, type DocumentStyle } from '../lib/documentStudio'
import { listFormatProfiles, type FormatProfile } from '../lib/schoolTemplates'
import { ArrowDownToLine, Check, ChevronDown, FilePlus2, FileText, FileUp, GitCompare, Highlighter, History, Layers3, RotateCcw, Save, ScanLine, Settings, Sigma, X, AlertTriangle } from 'lucide-react'
import ReplicaPages, { ReplicaHeader, ReplicaNavigation, screenNames, type Screen } from '../components/ReplicaPages'
import ReplicaRibbon from '../components/ReplicaRibbon'
import { LayoutDashboard, FolderOpen, GraduationCap, Menu } from 'lucide-react'
import { Card } from '../components/ui/card'
import 'katex/dist/katex.min.css'
type Status = 'pending' | 'accepted' | 'rejected'
type ChangeKind = 'format' | 'structure' | 'reference' | 'ai'
type Change = { id: string; title: string; detail: string; before: string; after: string; kind: ChangeKind; status: Status; anchor: string; rule: string; decidedAt?: string | null }
type ApiChange = { id: string; property: string; explanation: string; before: string; after: string; kind: ChangeKind; status: Status; anchor: { quote: string }; rule_id: string; decided_at?: string | null }
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
type GuideStep = 'document' | 'template' | 'scan' | 'review' | 'finish'
type GuideRecord = { autoShown: boolean; open: boolean; step: GuideStep; runId: string | null; order: string[]; cursor: string; skipped: string[] }
const GUIDE_STEPS: { id: GuideStep; label: string }[] = [
  { id: 'document', label: '文档' }, { id: 'template', label: '规范' }, { id: 'scan', label: '扫描' }, { id: 'review', label: '审阅' }, { id: 'finish', label: '检查与导出' },
]
const EMPTY_GUIDE_RECORD: GuideRecord = { autoShown: false, open: false, step: 'document', runId: null, order: [], cursor: '', skipped: [] }
declare global { interface Window { __thesisCloseSnapshot?: () => CloseSnapshot | null; __thesisCloseResume?: (message: string) => void; __thesisSavePending?: boolean } }
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8001')
const reviewStatus = { passed: '检查通过', needs_review: '待处理', blocked: '存在冲突' }
const saveLabels: Record<SaveState, string> = { saved: '已保存', unsaved: '待保存', saving: '保存中…', error: '保存失败', conflict: '保存冲突' }
function kindName(kind: ChangeKind) { return { format: '格式', structure: '结构', reference: '引用', ai: 'AI 建议' }[kind] }

export default function Home() {
  const [screen, setScreen] = useState<Screen>('overview')
  const [menuOpen, setMenuOpen] = useState(false)
  const [overviewQuery, setOverviewQuery] = useState('')
  const [editorZoom, setEditorZoom] = useState(1)
  const [rulesChanged, setRulesChanged] = useState(false)
  useEffect(() => {
    function sync() { const value=location.hash.slice(1); setScreen(Object.hasOwn(screenNames,value)?value as Screen:'overview'); setMenuOpen(false) }
    sync(); window.addEventListener('hashchange',sync)
    return () => window.removeEventListener('hashchange',sync)
  }, [])
  useEffect(() => { const open=()=>{setTab('agents');setRightPanelOpen(true)}; window.addEventListener('replica-open-agents',open);return()=>window.removeEventListener('replica-open-agents',open)},[])
  function navigateScreen(value: Screen) {
    setScreen(value); setMenuOpen(false)
    if(location.hash !== '#'+value) location.hash=value
    window.scrollTo({top:0,behavior:'instant'})
  }
  function openWorkspacePanel(panel: 'changes' | 'agents' | 'review') {
    navigateScreen('workbench'); setRightPanelOpen(true); setTab(panel)
    window.requestAnimationFrame(() => document.querySelector('.right-sidebar')?.scrollIntoView({block:'nearest'}))
  }
  const { theme, setTheme } = useTheme()
  const [themeReady, setThemeReady] = useState(false)
  useEffect(() => { setThemeReady(true) }, [])
  const darkTheme = themeReady && theme === 'dark'
  const [documents, setDocuments] = useState<WorkspaceSummary[]>([])
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(true)
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [leftRailOpen, setLeftRailOpen] = useState(true)
  const [rightPanelOpen, setRightPanelOpen] = useState(false)
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
  const [tab, setTab] = useState<'changes' | 'agents' | 'review'>('changes')
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
  const [guideOpen, setGuideOpen] = useState(false)
  const [guideStep, setGuideStep] = useState<GuideStep>('document')
  const [guideRunId, setGuideRunId] = useState<string | null>(null)
  const [guideOrder, setGuideOrder] = useState<string[]>([])
  const [guideCursor, setGuideCursor] = useState('')
  const [guideSkipped, setGuideSkipped] = useState<string[]>([])
  const guideFlowRef = useRef({ open: false, step: 'document' as GuideStep, documentId: null as string | null })
  guideFlowRef.current = { open: guideOpen, step: guideStep, documentId: activeDocumentId }
  const profileRef = useRef('generic-thesis')
  const fileInputRef = useRef<HTMLInputElement>(null)
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
      if (guideFlowRef.current.open && guideFlowRef.current.step === 'document' && current.getText().trim()) setGuideStage('template')
    },
  })
  editorRef.current = editor
  const pending = changes.filter(item => item.status === 'pending')
  const accepted = changes.filter(item => item.status === 'accepted').sort((a, b) => (a.decidedAt || '').localeCompare(b.decidedAt || ''))
  const active = changes.find(item => item.id === activeId) || changes[0]
  const hasContent = !!activeDocumentId && !!readContent().trim()
  const canDecide = !!activeDocumentId && !!runId && !!review && !hasLocalEdits && !busy && !saveConflictRef.current
  function refreshOutline(instance: Editor) {
    const headings: OutlineItem[] = []
    instance.state.doc.descendants((node, position) => { if (node.type.name === 'heading' && node.textContent.trim()) headings.push({ title: node.textContent, level: Number(node.attrs.level || 1), position }) })
    setOutline(headings)
  }
  function readContent() { return markdownRef.current ?? (editorRef.current ? serializeMarkdown(editorRef.current.getJSON()) : '') }
  function markDraftChanged() { saveRequiredRef.current = true; saveSerialRef.current += 1; setSaveTick(value => value + 1); if (!saveConflictRef.current) setSaveState('unsaved') }
  function notify(message: string) { setToast(message); window.setTimeout(() => setToast(''), 2600) }
  function readGuideRecord(documentId: string): GuideRecord {
    try {
      const parsed = JSON.parse(localStorage.getItem('thesisdebate:review-guide:v1:' + documentId) || 'null') as Partial<GuideRecord> | null
      const steps: GuideStep[] = ['document', 'template', 'scan', 'review', 'finish']
      return {
        autoShown: parsed?.autoShown === true,
        open: parsed?.open === true,
        step: steps.includes(parsed?.step as GuideStep) ? parsed!.step as GuideStep : 'document',
        runId: typeof parsed?.runId === 'string' ? parsed.runId : null,
        order: Array.isArray(parsed?.order) ? parsed!.order.filter((id): id is string => typeof id === 'string') : [],
        cursor: typeof parsed?.cursor === 'string' ? parsed.cursor : '',
        skipped: Array.isArray(parsed?.skipped) ? parsed!.skipped.filter((id): id is string => typeof id === 'string') : [],
      }
    } catch { return { ...EMPTY_GUIDE_RECORD } }
  }
  function writeGuideRecord(documentId: string, patch: Partial<GuideRecord>) {
    try { localStorage.setItem('thesisdebate:review-guide:v1:' + documentId, JSON.stringify({ ...readGuideRecord(documentId), ...patch })) }
    catch { /* Guide state is an enhancement; workspace and review data remain in the local API. */ }
  }
  function setGuideStage(step: GuideStep) {
    setGuideStep(step)
    const documentId = guideFlowRef.current.documentId
    if (documentId) writeGuideRecord(documentId, { step })
  }
  function openGuideTemplates() {
    setTemplatesOpen(true)
    if (guideOpen && guideStep === 'document') setGuideStage('template')
  }
  function openGuideExport() {
    exitGuide()
    setStudioOpen(true)
  }
  function continueFromDocumentStep() {
    if (!hasContent) {
      editor?.chain().focus().run()
      return
    }
    setGuideStage('template')
  }
  function toggleGuide() {
    if (!activeDocumentId) return
    const open = !guideOpen
    setGuideOpen(open)
    writeGuideRecord(activeDocumentId, { open, autoShown: true })
  }
  function exitGuide() {
    setGuideOpen(false)
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { open: false, autoShown: true })
  }
  function moveGuideTo(changeId: string) {
    setGuideCursor(changeId)
    setActiveId(changeId)
    const index = guideOrder.indexOf(changeId)
    const target = changes.find(item => item.id === changeId)
    if (target?.anchor.trim()) locateAgentQuote(target.anchor.trim())
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: changeId, step: 'review' })
    if (index >= 0) setGuideStep('review')
  }
  function startGuideReview(currentRunId: string, items: Change[]) {
    const order = items.map(item => item.id)
    const first = items.find(item => item.status === 'pending')?.id || ''
    setGuideRunId(currentRunId); setGuideOrder(order); setGuideCursor(first); setGuideSkipped([])
    setGuideStep(first ? 'review' : 'finish')
    if (first) setActiveId(first)
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { runId: currentRunId, order, cursor: first, skipped: [], step: first ? 'review' : 'finish' })
  }
  function skipGuideChange() {
    if (!active || active.status !== 'pending' || !guideOrder.length) return
    const skipped = [...new Set([...guideSkipped, active.id])]
    const currentIndex = guideOrder.indexOf(active.id)
    const nextId = guideOrder.slice(Math.max(0, currentIndex + 1)).find(id => {
      const item = changes.find(change => change.id === id)
      return item?.status === 'pending' && !skipped.includes(id)
    })
    setGuideSkipped(skipped)
    if (nextId) {
      setGuideCursor(nextId); setActiveId(nextId); setGuideStep('review')
      const next = changes.find(item => item.id === nextId)
      if (next?.anchor.trim()) locateAgentQuote(next.anchor.trim())
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { skipped, cursor: nextId, step: 'review' })
    } else {
      setGuideCursor(''); setGuideStep('finish')
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { skipped, cursor: '', step: 'finish' })
    }
  }
  function advanceGuideAfterDecision(change: Change, items: Change[]) {
    const currentIndex = guideOrder.indexOf(change.id)
    const nextId = guideOrder.slice(Math.max(0, currentIndex + 1)).find(id => {
      const item = items.find(candidate => candidate.id === id)
      return item?.status === 'pending' && !guideSkipped.includes(id)
    })
    if (nextId) {
      const next = items.find(item => item.id === nextId)
      setGuideCursor(nextId); setActiveId(nextId); setGuideStep('review')
      if (next?.anchor.trim()) locateAgentQuote(next.anchor.trim())
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: nextId, step: 'review' })
    } else {
      setGuideCursor(''); setGuideStep('finish')
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: '', step: 'finish' })
    }
  }
  function moveGuideStep(direction: -1 | 1) {
    const currentIndex = GUIDE_STEPS.findIndex(step => step.id === guideStep)
    const next = GUIDE_STEPS[Math.max(0, Math.min(GUIDE_STEPS.length - 1, currentIndex + direction))]
    if (!next) return
    if (next.id === 'review' && !runId) return setGuideStage('scan')
    if (next.id === 'review' && runId && guideRunId !== runId) startGuideReview(runId, changes)
    else setGuideStage(next.id)
  }
  function skipGuideStage() {
    if (guideStep === 'template') return setGuideStage('scan')
    if (guideStep === 'scan') return setGuideStage('finish')
    if (guideStep === 'review') return skipGuideChange()
  }
  function previousGuideChange() {
    const currentIndex = guideOrder.indexOf(guideCursor || active?.id || '')
    const previousId = guideOrder.slice(0, Math.max(0, currentIndex)).reverse().find(id => changes.some(item => item.id === id && item.status === 'pending') && !guideSkipped.includes(id))
    if (previousId) moveGuideTo(previousId)
    else moveGuideStep(-1)
  }
  function continueGuidePending() {
    const next = changes.find(item => item.status === 'pending')
    if (!next) return
    setGuideCursor(next.id); setActiveId(next.id); setGuideStep('review'); setGuideOpen(true)
    if (next.anchor.trim()) locateAgentQuote(next.anchor.trim())
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { open: true, cursor: next.id, step: 'review' })
  }
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
  function mapApiChange(item: ApiChange): Change { return { id: item.id, title: item.property, detail: item.explanation, before: item.before, after: item.after, kind: item.kind, status: item.status, anchor: item.anchor?.quote || '', rule: item.rule_id, decidedAt: item.decided_at } }
  async function readRun(currentRunId: string) {
    const [apiChanges, apiPreview, apiReview, apiSuggested] = await Promise.all([requestJson<ApiChange[]>('/format-runs/' + currentRunId + '/changes'), requestJson<PreviewResponse>('/format-runs/' + currentRunId + '/preview'), requestJson<ReviewResponse>('/format-runs/' + currentRunId + '/review'), requestJson<{preview:string}>('/format-runs/' + currentRunId + '/suggested-preview')])
    return { apiChanges, apiPreview, apiReview, apiSuggested }
  }
  function displayRun(currentRunId: string, result: Awaited<ReturnType<typeof readRun>>) {
    const mapped = result.apiChanges.map(mapApiChange)
    setRunId(currentRunId); setChanges(mapped); setActiveId(mapped.find(item => item.status === 'pending')?.id || mapped[0]?.id || ''); setPreviewData(result.apiPreview); setReview(result.apiReview)
    setSuggestedContent(result.apiSuggested.preview)
    return mapped
  }
  async function activateDocument(document: WorkspaceDocument, token: number, guideTrigger?: 'new' | 'import') {
    await requestJson('/workspace/active', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: document.id }) })
    if (token !== operationRef.current) return
    workspaceRef.current = document; nameRef.current = document.name; lastRunRef.current = document.last_run_id; headHashRef.current = document.head_hash
    profileRef.current = document.profile_id || 'generic-thesis'; setSelectedProfileId(profileRef.current)
    setRulesChanged(false)
    const storedGuide = readGuideRecord(document.id)
    const shouldAutoOpen = !!guideTrigger && !storedGuide.autoShown
    const initialGuide: GuideRecord = shouldAutoOpen
      ? { ...storedGuide, autoShown: true, open: true, step: guideTrigger === 'import' ? 'template' : 'document', runId: null, order: [], cursor: '', skipped: [] }
      : storedGuide
    if (shouldAutoOpen) writeGuideRecord(document.id, initialGuide)
    setGuideOpen(initialGuide.open); setGuideStep(initialGuide.step); setGuideRunId(initialGuide.runId)
    setGuideOrder(initialGuide.order); setGuideCursor(initialGuide.cursor); setGuideSkipped(initialGuide.skipped)
    markdownRef.current = document.content; saveRequiredRef.current = false; saveConflictRef.current = false; closePausedRef.current = false
    revisionRef.current += 1; dirtyRef.current = !!document.content.trim(); setHasLocalEdits(dirtyRef.current)
    setActiveDocumentId(document.id); setDocumentName(document.name); setSaveState('saved'); setSaveError(''); setUploadWarnings([]); setLatex('')
    setDocumentStyle(DEFAULT_DOCUMENT_STYLE); setSuggestedContent(null)
    setRunId(null); setReview(null); setPreviewData(null); setChanges([]); setActiveId(''); setAgentReview(null); setDocumentEpoch(value => value + 1)
    editorRef.current?.commands.setContent(migrateLegacyDocument(document.content, document.editor_json), { emitUpdate: false })
    if (editorRef.current) {
      // A loaded document starts its own undo history. Importing or switching
      // documents must never become an undoable edit in the next manuscript.
      const instance = editorRef.current
      instance.view.updateState(EditorState.create({ schema: instance.schema, doc: instance.state.doc, plugins: instance.state.plugins }))
      refreshOutline(instance)
    }
    updateLibrary(document)
    if (document.last_run_id) {
      try {
        const result = await readRun(document.last_run_id); if (token !== operationRef.current) return
        const mapped = displayRun(document.last_run_id, result)
        const ruleStatus = await requestJson<{matches:boolean}>('/format-runs/' + encodeURIComponent(document.last_run_id) + '/rule-selection-status')
        if (token !== operationRef.current) return
        setRulesChanged(!ruleStatus.matches)
        if (initialGuide.runId !== document.last_run_id) {
          const first = mapped.find(item => item.status === 'pending')?.id || ''
          const stage: GuideStep = initialGuide.step === 'review' ? (first ? 'review' : 'finish') : initialGuide.step
          const nextGuide = { runId: document.last_run_id, order: mapped.map(item => item.id), cursor: first, step: stage }
          setGuideRunId(nextGuide.runId); setGuideOrder(nextGuide.order); setGuideCursor(nextGuide.cursor); setGuideStep(nextGuide.step)
          writeGuideRecord(document.id, nextGuide)
        }
        dirtyRef.current = document.content !== (result.apiPreview.preview ?? result.apiPreview.source ?? ''); setHasLocalEdits(dirtyRef.current)
      }
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
      if (guideOpen && guideStep === 'template') setGuideStage('scan')
    } catch (error) { reportError(error, token); throw error }
    finally { finishOperation(token) }
  }
  async function openDocumentReview(id: string) {
    if (id !== activeDocumentId) { setSelectedReviewId(null); await switchDocument(id); if (workspaceRef.current?.id !== id) return }
    setSelectedReviewId(agentTasks[id]?.review_id ?? null)
    setTab('agents')
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
    try { await flushDraft(); const document = await requestJson<WorkspaceDocument>('/workspace/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: '未命名文档.md', content: '', profile_id: profileRef.current, editor_json: { type: 'doc', content: [{ type: 'paragraph' }] } }) }); await activateDocument(document, token, 'new'); editorRef.current?.commands.focus() } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function createExample() {
    const token = beginOperation(); if (token === null) return
    try {
      await flushDraft()
      const content = '# 数字化学习平台研究与设计\n\n## 研究背景\n\n数字化学习平台正在改变学生获取知识与反馈的方式。本研究关注平台使用体验与学习成效之间的关系。\n\n## 研究方法\n\n采用问卷与前后测相结合的方法，收集 38 份有效样本，以完成率、满意度和成绩变化作为评价指标。\n\n## 数据分析\n\n对不同使用频率的学生进行分组比较，报告样本分布、组间差异与研究局限。所有数据均经过匿名化处理。\n\n参考文献\n- 示例作者. 数字化学习研究. 2026.\n'
      const document = await requestJson<WorkspaceDocument>('/workspace/documents', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:'论文修订练习.md',content,profile_id:profileRef.current,editor_json:parseMarkdown(content)})})
      await activateDocument(document, token, 'import')
      notify('已创建独立练习文档，可以运行格式扫描')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function handleFileUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''; if (file) await importDocument(file)
  }
  async function importDocument(file: File) {
    if (!/\.(md|markdown|txt|docx|pdf)$/i.test(file.name)) { setRequestError('支持导入 PDF、Word（DOCX）、Markdown 和 TXT 文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setRequestError('文件不能超过 10 MB。'); return }
    const token = beginOperation(); if (token === null) return
    try {
      await flushDraft()
      const document = await requestJson<WorkspaceDocument & { source_type: string; warnings: string[] }>('/workspace/import?filename=' + encodeURIComponent(file.name) + '&profile_id=' + encodeURIComponent(profileRef.current), { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })
      await activateDocument(document, token, 'import')
      navigateScreen('workbench')
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
    return { count: mapped.length, preview: result.apiPreview, changes: mapped }
  }
  async function scanFormat() {
    if (!editor || !workspaceRef.current || !readContent().trim()) return
    const token = beginOperation(); if (token === null) return
    const revision = revisionRef.current; setScanState('扫描中…')
    try {
      await flushDraft(); requireCurrentEditor(token, revision)
      const selection = await requestJson<{enabled:string[]}>('/format-profiles/'+encodeURIComponent(profileRef.current)+'/rule-selection')
      requireCurrentEditor(token, revision)
      const run = await requestJson<FormatRunIdentity>('/format-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profileRef.current, requested_rules: selection.enabled, document_id: workspaceRef.current.id, document_version_id: 'draft-' + workspaceRef.current.revision, document_name: nameRef.current, document_type: 'markdown', content: readContent(), ...(headHashRef.current ? { expected_head_hash: headHashRef.current } : {}) }) })
      requireCurrentEditor(token, revision); setRulesChanged(false); setRightPanelOpen(true); const refreshed = await refreshRun(run.id, token, revision); startGuideReview(run.id, refreshed.changes); notify('发现 ' + refreshed.count + ' 条修改建议')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function decide(change: Change, decision: 'accept' | 'reject', advanceGuide = false) {
    if (!requireFreshRun()) return; const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try { await flushDraft(); await requestJson('/format-changes/' + change.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, expected_source_hash: review?.source_hash }) }); const refreshed = await refreshRun(runId!, token, revision); if (advanceGuide) advanceGuideAfterDecision(change, refreshed.changes); notify(decision === 'accept' ? '已接受修改' : '已保留原文') } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function acceptAllPending() {
    if (!requireFreshRun()) return
    const blocked = new Set(review?.blocked_change_ids ?? [])
    const candidates = pending.filter(change => !blocked.has(change.id))
    if (!candidates.length) return
    const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    let acceptedCount = 0
    let skippedCount = 0
    let stopError: unknown = null
    try {
      await flushDraft()
      for (const change of candidates) {
        requireCurrentEditor(token, revision)
        try {
          await requestJson('/format-changes/' + change.id + '/decision', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision: 'accept', expected_source_hash: review?.source_hash }),
          })
          acceptedCount += 1
        } catch (error) {
          skippedCount += 1
          const status = (error as Error & { status?: number }).status
          const message = error instanceof Error ? error.message : String(error)
          if (status !== 409 || /原文哈希已变化|扫描时原文已变化|文档版本与扫描版本/.test(message)) {
            stopError = error
            break
          }
        }
      }
      await refreshRun(runId!, token, revision)
      if (stopError) reportError(stopError, token)
      notify(skippedCount ? '已接受 ' + acceptedCount + ' 项，' + skippedCount + ' 项未接受（可能存在冲突）' : '已接受全部 ' + acceptedCount + ' 项修改')
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
    // Insertion must preserve text selected by search or a previous toolbar action.
    const insertionAnchor = editor.state.selection.to
    const inserted = editor.chain().focus().setTextSelection(insertionAnchor).insertContent({ type: 'blockMath', attrs: { latex: source } }).run()
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
  function locateAgentQuote(quote: string) {
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
      let start = 0
      while (start < text.length) {
        const found = text.indexOf(candidate, start); if (found < 0) break
        const match = { from: positions[found].from, to: positions[found + candidate.length - 1].to }
        if (!matches.some(item => item.from === match.from && item.to === match.to)) matches.push(match)
        start = found + candidate.length
      }
    }
    if (matches.length !== 1) return notify(matches.length > 1 ? '原文有多处匹配，请结合证据定位。' : '当前正文未找到该原文。')
    editor.chain().focus().setTextSelection(matches[0]).scrollIntoView().run()
  }
  function focusChange(change: Change) {
    setActiveId(change.id)
    if (guideStep === 'review' && guideOrder.includes(change.id)) {
      setGuideCursor(change.id)
      if (change.status === 'pending' && guideSkipped.includes(change.id)) {
        const skipped = guideSkipped.filter(id => id !== change.id); setGuideSkipped(skipped)
        if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: change.id, skipped })
      } else if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: change.id })
    }
    if (change.anchor.trim()) locateAgentQuote(change.anchor.trim())
  }
  function download(content: string, filename: string, contentType: string) { const url = URL.createObjectURL(new Blob([content], { type: contentType })); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000) }
  function exportMarkdown() { if (!activeDocumentId || !hasContent || busyRef.current) return; download(readContent(), (nameRef.current || '文档').replace(/\.[^.]+$/, '') + '.md', 'text/markdown;charset=utf-8') }
  function exportReport() { if (!requireFreshRun() || busyRef.current) return; download(JSON.stringify({ document_name: nameRef.current, review, changes, preview: previewData, agent_review: agentReview, agent_review_matches_current_snapshot: !!agentReview && agentReview.format_run_id === runId && agentReview.preview_hash === review?.preview_hash && agentReview.source_hash === review?.source_hash }, null, 2), nameRef.current.replace(/\.[^.]+$/, '') + '-修订报告.json', 'application/json;charset=utf-8') }
  function renameDocument(value: string) { nameRef.current = value; setDocumentName(value); markDraftChanged() }
  function goToHeading(item: OutlineItem) { editor?.chain().focus().setTextSelection(item.position + 1).scrollIntoView().run() }
  return (
    <main className={"app-shell production-shell replica-shell unslop-workspace replica-"+screen} data-workspace-ready={!workspaceLoading ? 'true' : 'false'}>
      <input ref={fileInputRef} type="file" accept=".md,.markdown,.txt,.docx,.pdf" hidden onChange={handleFileUpload} />
      <a className="overview-skip" href="#replica-content">跳到主要内容</a>
      <ReplicaNavigation screen={screen} navigate={navigateScreen} count={documents.length} onSettings={()=>setSettingsOpen(true)} open={menuOpen} onClose={()=>setMenuOpen(false)}/>
      <div className="rep-main" id="replica-content">
      <ReplicaHeader screen={screen} query={overviewQuery} onQuery={setOverviewQuery} navigate={navigateScreen} onSettings={()=>setSettingsOpen(true)} onMenu={()=>setMenuOpen(value=>!value)} pending={pending.length}/>
      <ReplicaPages screen={screen} api={API_BASE} documents={documents} activeId={activeDocumentId} name={documentName} content={readContent()} query={overviewQuery} onQuery={setOverviewQuery} navigate={navigateScreen} onOpen={id=>{navigateScreen('workbench');if(id!==activeDocumentId)void switchDocument(id)}} onImport={()=>fileInputRef.current?.click()} onDrop={file=>{void importDocument(file)}} onCreate={()=>{navigateScreen('workbench');void createDocument()}} onExample={()=>{navigateScreen('workbench');void createExample()}} onTemplates={()=>setTemplatesOpen(true)} onSettings={()=>setSettingsOpen(true)} onExport={()=>setStudioOpen(true)} onScan={()=>{void scanFormat()}} onLocate={id=>{navigateScreen('workbench');setRightPanelOpen(true);setTab('changes');const change=changes.find(c=>c.id===id);if(change)requestAnimationFrame(()=>focusChange(change))}} onRulesChanged={()=>setRulesChanged(true)} onProfile={selectSchoolTemplate} profileId={selectedProfileId} report={review} changes={changes} dirty={hasLocalEdits||rulesChanged} busy={busy} loading={workspaceLoading} error={requestError}/>
      <div className="reference-workbench" hidden={screen !== 'workbench'}>
      {!activeDocumentId ? <section className="workspace-welcome" data-testid="workspace-empty">
        {workspaceLoading ? <p role="status">正在打开工作区…</p> : <>
          <div className="welcome-heading"><h1>从初稿到定稿</h1></div>
          <div className="welcome-layout">
            <Card className="welcome-manuscript"><h2>论文写作，<br/>从这里继续。</h2><p>导入文稿，选择学校规范，<br/>把每一条建议变成可确认、可撤销的修改。</p><div className="welcome-actions"><button className="primary-button" onClick={() => fileInputRef.current?.click()} disabled={busy}><FileUp size={16}/>导入我的论文<ArrowUpRight size={16}/></button><button className="soft-button" onClick={createDocument} disabled={busy}><FilePlus2 size={16}/>新建文档</button></div><div className="welcome-filetypes">支持 Word · PDF 文字稿 · Markdown · TXT</div></Card>
            <Card className="welcome-process"><div className="section-caption"><span>一条完整的修订路径</span><ShieldCheck size={18}/></div><ol>{[{title:'导入与编辑',detail:'保留原始文件，建立可编辑稿件。'},{title:'检查论文规范',detail:'按模板检查标题、间距与参考文献。'},{title:'逐项确认修订',detail:'对照原文，接受、保留或撤销建议。'},{title:'预览并导出',detail:'检查版式，导出 Word、PDF 或 Markdown。'}].map((step,index) => <li key={step.title}><span className="step-number">0{index+1}</span><div><h3>{step.title}</h3><p>{step.detail}</p></div><ArrowUpRight size={15}/></li>)}</ol><button className="example-button" onClick={createExample} disabled={busy}>用示例试一次<ArrowRight size={17}/></button></Card>
          </div>
          
          {requestError && <div className="review-alert" role="alert">{requestError}</div>}
          {documents.length > 0 && <div className="welcome-documents"><h2>最近文档</h2>{documents.map(document => <div className="welcome-document-row" key={document.id}><button onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={14}/><span>{document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}</div>}
        </>}
      </section> : <>
        <ReplicaRibbon editor={editor} name={documentName} busy={busy} onHome={()=>navigateScreen('overview')} onSave={()=>{void flushDraft().then(()=>notify('已保存')).catch(()=>undefined)}} onImport={()=>fileInputRef.current?.click()} onNew={()=>{void createDocument()}} onExport={()=>setStudioOpen(true)} onLayout={()=>setStudioOpen(true)} onTemplates={()=>setTemplatesOpen(true)} onReview={()=>setRightPanelOpen(value=>!value)} onScan={()=>{void scanFormat()}} zoom={editorZoom} onZoom={setEditorZoom}/>
        {rulesChanged&&<p className="rep-alert" role="status">规则选择已更新，请重新扫描以查看新的检查结果。</p>}
        <section className="workspace-toolbar"><div className="crumbs"><strong>文档工作区</strong><span className={'status-pill save-' + saveState} role="status">{saveLabels[saveState]}</span>{hasLocalEdits && runId && <span className="draft-indicator">正文已修改</span>}</div><div className="toolbar-actions"><button className="ghost-button layout-toggle" onClick={() => setLeftRailOpen(value => !value)} aria-expanded={leftRailOpen}>{leftRailOpen ? '收起导航' : '显示导航'}</button><button className="ghost-button layout-toggle" onClick={() => setRightPanelOpen(value => !value)} aria-expanded={rightPanelOpen}>{rightPanelOpen ? '收起审查' : '显示审查'}</button><button className="ghost-button" onClick={() => setShowDiff(value => !value)}><GitCompare size={16}/>{showDiff ? '收起对比' : '显示对比'}</button><button type="button" className="ghost-button" onClick={() => { setRightPanelOpen(true); setTab('agents') }} aria-label="查看多角色审查工作流"><Layers3 size={16}/>查看工作流</button><button className="primary-button" data-guide-target={guideOpen && guideStep === 'scan' && hasContent && !busy ? 'true' : undefined} onClick={scanFormat} disabled={busy || !hasContent || saveConflictRef.current}><ScanLine size={16}/>{scanState || '运行格式扫描'}</button></div></section>
        <div className={'workspace-grid' + (!leftRailOpen ? ' nav-collapsed' : '') + (!rightPanelOpen ? ' review-collapsed' : '')}>
          <aside className="sidebar left-sidebar" aria-label="文档导航">
            <div className="side-head"><h2>我的文档</h2><button className="icon-button" onClick={() => setLibraryOpen(value => !value)} aria-label={libraryOpen ? '折叠文档列表' : '展开文档列表'} aria-expanded={libraryOpen}><ChevronDown size={16} className={!libraryOpen ? 'collapsed-chevron' : ''}/></button></div>
            {libraryOpen && <><label className="library-search-label" htmlFor="document-search">搜索文档</label><input id="document-search" className="library-search" type="search" value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="按名称筛选" aria-label="搜索文档"/><nav className="document-library" aria-label="文档列表">{visibleDocuments().map(document => <div className="library-document-row" key={document.id}><button className={'library-document ' + (document.id === activeDocumentId ? 'active' : '')} onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={15}/><span title={document.name}>{document.id === activeDocumentId ? documentName || '未命名文档' : document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'} title={agentTasks[document.id].review_id}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}{visibleDocuments().length === 0 && <p className="review-empty">没有匹配的文档。</p>}</nav>{taskStatusError && <p className="task-status-error" role="status">审查状态暂不可用，请检查本地服务连接。</p>}</>}
            <div className="section-heading outline-heading"><span>文档目录 · {outline.length ? outline.length + ' 个标题' : '暂无标题'}</span><button className="icon-button" onClick={() => setOutlineOpen(value => !value)} aria-label={outlineOpen ? '折叠文档目录' : '展开文档目录'} aria-expanded={outlineOpen}><ChevronDown size={16} className={!outlineOpen ? 'collapsed-chevron' : ''}/></button></div>
            {outlineOpen && <nav className="outline" aria-label="文档目录">{outline.length ? outline.map((item, index) => <button key={index} className="outline-item" style={{paddingLeft: 12 + Math.min(item.level - 1, 4) * 12}} onClick={() => goToHeading(item)} title={item.title}><span>{item.title}</span></button>) : <p className="review-empty">正文中的标题会显示在这里。</p>}</nav>}
          </aside>
          <section className="editor-panel">
            <div className="editor-head"><div className="document-heading"><label htmlFor="document-name">文档名称</label><input id="document-name" aria-label="文档名称" value={documentName} onChange={event => renameDocument(event.target.value)} onBlur={() => { void flushDraft().catch(() => undefined) }} disabled={busy}/></div><button className="soft-button" onClick={() => { void flushDraft().then(() => notify('已保存')).catch(() => undefined) }} disabled={busy || saveState === 'saving' || saveState === 'conflict'}><Save size={14}/>保存</button></div>
            <div className="active-template"><span>学校模板：{profileNames[selectedProfileId] || selectedProfileId}</span><button type="button" className="ghost-button" data-guide-target={guideOpen && guideStep === 'template' ? 'true' : undefined} onClick={openGuideTemplates} disabled={busy}>切换模板</button></div>
            {guideOpen && <section className="review-guide" role="region" aria-label="审阅步骤">
              <div className="review-guide-head"><div><strong>审阅步骤</strong><span>{GUIDE_STEPS.findIndex(step => step.id === guideStep) + 1} / {GUIDE_STEPS.length}</span></div><button type="button" className="guide-close" onClick={exitGuide}>退出引导</button></div>
              <ol className="review-guide-steps">{GUIDE_STEPS.map((step, index) => <li key={step.id} className={step.id === guideStep ? 'active' : index < GUIDE_STEPS.findIndex(item => item.id === guideStep) ? 'done' : ''}><b>{index + 1}</b><span>{step.label}</span></li>)}</ol>
              <div className="review-guide-body">
                {guideStep === 'document' && <><h3>先准备文档</h3><p>{hasContent ? '文档已有正文，可以直接进入规范设置。' : '在中间稿件区域输入或粘贴正文，输入后会自动进入规范设置。'}</p></>}
                {guideStep === 'template' && <><h3>确认排版规范</h3><p>点击“切换模板”，选择并应用学校规范后会进入扫描。</p></>}
                {guideStep === 'scan' && <><h3>运行格式扫描</h3><p>{runId ? '扫描已完成，可以开始逐项审阅。' : '点击页面上方高亮的“运行格式扫描”，生成可逐项审核的建议。'}</p></>}
                {guideStep === 'review' && active && <><div className="review-guide-count">第 {Math.max(1, guideOrder.indexOf(active.id) + 1)} 项 · 仍有 {pending.length} 项待处理</div><h3>{active.title}</h3><p>{active.detail}</p><div className="change-diff"><span className="before">− {active.before}</span><span className="after">＋ {active.after}</span></div></>}
                {guideStep === 'review' && !active && <><h3>没有可审阅的建议</h3><p>当前扫描没有待处理项，可以进入检查与导出。</p></>}
                {guideStep === 'finish' && <><h3>检查并导出</h3><p>当前仍有 {pending.length} 项待处理。点击页面底部高亮的导出按钮查看可用格式；跳过的建议仍保持待处理。</p></>}
              </div>
              <div className="review-guide-actions">
                <button type="button" className="soft-button" onClick={() => guideStep === 'review' ? previousGuideChange() : moveGuideStep(-1)} disabled={guideStep === 'document' || busy}>上一步</button>
                {guideStep !== 'review' && guideStep !== 'finish' && <button type="button" className="soft-button" onClick={skipGuideStage} disabled={busy}>跳过此步骤</button>}
                {guideStep === 'finish' && pending.length > 0 && <button type="button" className="soft-button" onClick={continueGuidePending}>继续处理待处理项</button>}
                {guideStep === 'review' && active && <><button type="button" className="soft-button" onClick={skipGuideChange} disabled={busy}>跳过此项</button><button type="button" className="reject-btn" onClick={() => decide(active, 'reject', true)} disabled={!canDecide}>保留原文</button><button type="button" className="accept-btn" data-guide-target="true" onClick={() => decide(active, 'accept', true)} disabled={!canDecide || !!review?.blocked_change_ids.includes(active.id)}>接受修改</button></>}
                {guideStep === 'document' && <button type="button" className="primary-button" onClick={continueFromDocumentStep} data-guide-target="true">{hasContent ? '下一步' : '开始编辑'}</button>}
                {guideStep === 'template' && <button type="button" className="primary-button" onClick={openGuideTemplates}>选择学校规范</button>}
                {guideStep === 'finish' && <button type="button" className="soft-button" onClick={exitGuide}>完成</button>}
                {guideStep === 'review' && !active && <button type="button" className="primary-button" onClick={() => setGuideStage('finish')}>下一步</button>}
              </div>
            </section>}
            {!guideOpen && activeDocumentId && <button type="button" className="guide-reopen" onClick={toggleGuide}>重新打开引导</button>}
            {requestError && <div className="review-alert" role="alert">{requestError}</div>}{saveError && <div className="review-alert" role="alert">{saveError}{saveState === 'error' && <button onClick={() => { void flushDraft().catch(() => undefined) }}>重试保存</button>}{saveState === 'conflict' && <><button onClick={exportMarkdown} disabled={!hasContent}>导出当前副本</button><button onClick={reopenSavedDocument}>重新打开已保存版本</button></>}</div>}
            {hasLocalEdits && runId && <div className="review-alert warning" role="status">正文已修改，重新扫描后可继续处理建议。</div>}{uploadWarnings.length > 0 && <details className="import-notice"><summary>导入说明</summary><ul>{uploadWarnings.map(warning => <li key={warning}>{warning}</li>)}</ul></details>}
            <div className="paper-wrap"><article className="paper styled-manuscript" data-guide-editor={guideOpen && guideStep === 'document' ? 'true' : undefined} style={{...documentStyleCss(documentStyle),zoom:editorZoom}}><EditorContent editor={editor}/>{!hasContent && <p className="editor-placeholder">在这里输入正文，或导入文档开始编辑。</p>}</article></div>
            <div className="formula-bar"><div className="formula-label-left"><Sigma size={16}/>LaTeX 公式</div><input value={latex} onChange={event => setLatex(event.target.value)} placeholder="输入 LaTeX 公式" aria-label="LaTeX 公式源码" disabled={busy}/><button className="insert-button" onClick={insertFormula} disabled={busy || !latex.trim()}>插入公式</button>{formulaNotice && <span className="formula-notice" role="status"><Check size={14}/>{formulaNotice}</span>}</div>
            <div className="format-diff-container" hidden={!showDiff}><FormatDiffPanel apiBase={API_BASE} runId={runId} review={review} pendingCount={pending.length} dirty={hasLocalEdits} disabled={busy}/></div>
          </section>
          <aside className="sidebar right-sidebar" aria-label="修订与评审">
            <div className="review-progress"><h2>{hasLocalEdits && runId ? '等待重新扫描' : review ? reviewStatus[review.status] : '修订建议'}</h2><div className="review-counts"><span>{review?.applied_count ?? 0} 项已应用</span>{!!review?.blocked_change_ids.length && <span>{review.blocked_change_ids.length} 项冲突</span>}</div></div>
            <div className="assistant-entry"><button type="button" onClick={() => setStudioOpen(true)} disabled={busy || !hasContent}><Settings size={15}/><span><strong>论文排版助手</strong></span></button></div><div className="tabs"><button className={tab === 'changes' ? 'tab active' : 'tab'} onClick={() => setTab('changes')}>修改建议{changes.length > 0 && <span>{pending.length}</span>}</button><button className={tab === 'agents' ? 'tab active' : 'tab'} onClick={() => setTab('agents')}>多角色审查</button><button className={tab === 'review' ? 'tab active' : 'tab'} onClick={() => setTab('review')}>修订检查</button></div>
            <AgentReviewPanel apiBase={API_BASE} runId={runId} sourceHash={review?.source_hash ?? ''} previewHash={review?.preview_hash ?? ''} documentEpoch={documentEpoch} dirty={hasLocalEdits} busy={busy} hidden={tab !== 'agents'} selectedReviewId={selectedReviewId} applyVersion={applyAgentVersion} onLocate={locateAgentQuote} onReviewChange={setAgentReview}/>
            {tab === 'changes' ? <>
              <div className="change-list">
                {!!pending.length && <button type="button" className="bulk-accept-button" onClick={acceptAllPending} disabled={!canDecide || !pending.some(change => !review?.blocked_change_ids.includes(change.id))} title="接受所有未处理且未被标记冲突的修改">一键接受全部修改 <span>{pending.filter(change => !review?.blocked_change_ids.includes(change.id)).length}</span></button>}
                {!changes.length && <p className="review-empty">{runId ? '未发现需要修改的内容。' : '扫描当前文档以查看修改建议。'}</p>}
                {pending.map(change => <article key={change.id} data-rule={change.rule} className={'change-card ' + (activeId === change.id ? 'selected' : '')}>
                  <button type="button" className="change-card-content" onClick={() => focusChange(change)} aria-label={'定位修改：' + change.title}><div className="change-card-top"><span className={'change-type ' + change.kind}>{kindName(change.kind)}</span><span className="change-anchor">{change.anchor}</span></div><strong>{change.title}</strong><p>{change.detail}</p><div className="change-diff"><span className="before">− <TextDiff before={change.before} after={change.after} side="before"/></span><span className="after">＋ <TextDiff before={change.before} after={change.after} side="after"/></span></div></button>
                  <div className="change-card-actions"><button type="button" className="accept-btn" onClick={() => decide(change, 'accept')} disabled={!canDecide || !!review?.blocked_change_ids.includes(change.id)}><Check size={14}/>接受此项</button><button type="button" className="reject-btn" onClick={() => decide(change, 'reject')} disabled={!canDecide}><X size={14}/>保留此项</button></div>
                </article>)}
                {accepted.map(change => <button type="button" key={change.id} className="resolved-card accepted" onClick={() => focusChange(change)}><Check size={14}/><span>{change.title}</span><small>已接受</small></button>)}
                {changes.filter(item => item.status === 'rejected').map(change => <button type="button" key={change.id} className="resolved-card rejected" onClick={() => focusChange(change)}><X size={14}/><span>{change.title}</span><small>已保留</small></button>)}
              </div>
              {active && <div className="selected-detail"><div className="detail-title">当前修改</div><div className="detail-anchor"><Highlighter size={14}/>{active.anchor}</div><p>{active.detail}</p><div className="change-diff"><span className="before">− <TextDiff before={active.before} after={active.after} side="before"/></span><span className="after">＋ <TextDiff before={active.before} after={active.after} side="after"/></span></div><div className="detail-actions"><button className="accept-btn" onClick={() => decide(active, 'accept')} disabled={!canDecide || active.status === 'accepted' || !!review?.blocked_change_ids.includes(active.id)}><Check size={14}/>接受修改</button><button className="reject-btn" onClick={() => decide(active, 'reject')} disabled={!canDecide || active.status === 'rejected'}><X size={14}/>保留原文</button></div></div>}
            </> : tab === 'review' ? <div className="debate-list" aria-label="修订检查报告">{!review ? <p className="review-empty">请先扫描当前文档。</p> : <><div className={'judge-note review-result-' + review.status}>{reviewStatus[review.status]}：{review.summary}</div>{review.checks.map(check => <div className="agent-row" key={check.id}><div className={'agent-avatar ' + (check.status === 'passed' ? 'teal' : 'amber')}>{check.status === 'passed' ? <Check size={13}/> : <AlertTriangle size={13}/>}</div><div><div className="agent-name">{check.title}<span className="review-check-status">{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '待处理'}</span></div><p>{check.detail}</p></div></div>)}</>}</div> : null}
          </aside>
        </div>
        <footer className="statusbar"><div><span className={'save-status save-' + saveState}>{saveLabels[saveState]}</span>{hasContent && <span>{editor?.getText().replace(/\s/g, '').length ?? 0} 字</span>}</div><div><button onClick={restoreOriginal} disabled={!canDecide}><History size={13}/>恢复本次原稿</button><button onClick={undoLast} disabled={!canDecide || !accepted.length}><RotateCcw size={13}/>撤销</button><button data-guide-target={guideOpen && guideStep === 'finish' ? 'true' : undefined} onClick={guideOpen && guideStep === 'finish' ? openGuideExport : () => setStudioOpen(true)} disabled={!hasContent || busy}><ArrowDownToLine size={13}/>导出 PDF / MD / DOCX</button><button onClick={exportReport} disabled={!canDecide || !hasContent}><ArrowDownToLine size={13}/>导出修订报告</button></div></footer>
      </>}
      </div>
      </div>
      <DocumentStudio apiBase={API_BASE} open={studioOpen} onClose={() => setStudioOpen(false)} documentId={activeDocumentId} documentName={documentName} content={readContent()} originalContent={hasLocalEdits ? null : previewData?.source ?? null} suggestedContent={hasLocalEdits ? null : suggestedContent} profileStyle={profileStyles[selectedProfileId] ?? null} documentEpoch={documentEpoch} disabled={busy || workspaceLoading || saveState === 'conflict'} onStyleChange={setDocumentStyle}/>
      <AgentSettings apiBase={API_BASE} open={settingsOpen} onClose={() => setSettingsOpen(false)}/>
      <SchoolTemplatePanel apiBase={API_BASE} open={templatesOpen} selectedProfileId={selectedProfileId} onClose={() => setTemplatesOpen(false)} onSelect={selectSchoolTemplate} guideActive={guideOpen && guideStep === 'template'} disabled={busy}/>
      {toast && <div className="toast"><Check size={15}/>{toast}</div>}
    </main>
  )
}
