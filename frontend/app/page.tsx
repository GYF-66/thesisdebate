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
import WorkspaceAssistant from '../components/WorkspaceAssistant'
import LiteraturePanel from '../components/LiteraturePanel'
import { EXAMPLE_CONTENT, EXAMPLE_NAME } from '../lib/exampleDocument'
import type { TaskResult } from '../lib/workspaceAssistant'
import './workspace-experience.css'
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
type GuideStep = 'document' | 'template' | 'scan' | 'review' | 'finish'
type GuideRecord = { autoShown: boolean; open: boolean; step: GuideStep; runId: string | null; order: string[]; cursor: string; skipped: string[]; exported?: boolean }
const GUIDE_STEPS: { id: GuideStep; label: string }[] = [
  { id: 'document', label: '准备正文' }, { id: 'template', label: '选择规范' }, { id: 'scan', label: '检查文档' }, { id: 'review', label: '确认修改' }, { id: 'finish', label: '导出' },
]
const EMPTY_GUIDE_RECORD: GuideRecord = { autoShown: false, open: false, step: 'document', runId: null, order: [], cursor: '', skipped: [] }
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
  const [templatePreview, setTemplatePreview] = useState(false)
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
  const profileRef = useRef('generic-thesis')
  const guideFlowRef = useRef({ open: false, step: 'document' as GuideStep, documentId: null as string | null })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const toolsMenuRef = useRef<HTMLDetailsElement>(null)
  const editorRef = useRef<Editor | null>(null)
  const operationRef = useRef(0), revisionRef = useRef(0), dirtyRef = useRef(false), busyRef = useRef(false)
  const markdownRef = useRef<string | null>(null), workspaceRef = useRef<WorkspaceDocument | null>(null), nameRef = useRef('')
  const lastRunRef = useRef<string | null>(null), headHashRef = useRef<string | null>(null)
  const saveRequiredRef = useRef(false), saveSerialRef = useRef(0), savePromiseRef = useRef<Promise<void> | null>(null)
  const saveConflictRef = useRef(false), closePausedRef = useRef(false), initializedRef = useRef(false)
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  guideFlowRef.current = { open: guideOpen, step: guideStep, documentId: activeDocumentId }
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
  function readGuideRecord(documentId: string): GuideRecord {
    try {
      const parsed = JSON.parse(localStorage.getItem('thesisdebate:review-guide:v1:' + documentId) || 'null') as Partial<GuideRecord> | null
      const steps: GuideStep[] = ['document', 'template', 'scan', 'review', 'finish']
      return { autoShown: parsed?.autoShown === true, open: parsed?.open === true,
        step: steps.includes(parsed?.step as GuideStep) ? parsed!.step as GuideStep : 'document',
        runId: typeof parsed?.runId === 'string' ? parsed.runId : null,
        order: Array.isArray(parsed?.order) ? parsed!.order.filter((id): id is string => typeof id === 'string') : [],
        cursor: typeof parsed?.cursor === 'string' ? parsed.cursor : '',
        skipped: Array.isArray(parsed?.skipped) ? parsed!.skipped.filter((id): id is string => typeof id === 'string') : [],
        exported: parsed?.exported === true,
      }
    } catch { return { ...EMPTY_GUIDE_RECORD } }
  }
  function writeGuideRecord(documentId: string, patch: Partial<GuideRecord>) {
    try { localStorage.setItem('thesisdebate:review-guide:v1:' + documentId, JSON.stringify({ ...readGuideRecord(documentId), ...patch })) }
    catch { /* Guide state is optional; document and review state stay in the local API. */ }
  }
  function setGuideStage(step: GuideStep) {
    setGuideStep(step)
    const documentId = guideFlowRef.current.documentId
    if (documentId) writeGuideRecord(documentId, { step })
  }
  function exitGuide() {
    setGuideOpen(false)
    const documentId = guideFlowRef.current.documentId
    if (documentId) writeGuideRecord(documentId, { open: false, autoShown: true })
  }
  function openGuideTemplates() {
    setTemplatesOpen(true)
    if (guideOpen && guideStep === 'document') setGuideStage('template')
  }
  function openGuideExport() { setStudioOpen(true) }
  function reopenGuide() {
    setGuideOpen(true)
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { open: true, autoShown: true })
  }
  function completeGuideExport(scope: 'original' | 'expected' | 'current') {
    if (scope !== 'current' || !activeDocumentId) return
    writeGuideRecord(activeDocumentId, { open: false, autoShown: true, exported: true, step: 'finish' })
    setGuideOpen(false); notify('当前稿已导出。之后可从“使用引导”重新查看步骤。')
  }
  function startGuideReview(currentRunId: string, items: Change[]) {
    const order = items.map(item => item.id), first = items.find(item => item.status === 'pending')?.id || ''
    const step = first ? 'review' : 'finish'
    setGuideRunId(currentRunId); setGuideOrder(order); setGuideCursor(first); setGuideSkipped([]); setGuideStep(step)
    if (first) setActiveId(first)
    if (activeDocumentId) writeGuideRecord(activeDocumentId, { runId: currentRunId, order, cursor: first, skipped: [], step })
  }
  function advanceGuideAfterDecision(change: Change, items: Change[]) {
    const currentIndex = guideOrder.indexOf(change.id)
    const nextId = guideOrder.slice(Math.max(0, currentIndex + 1)).find(id => items.some(item => item.id === id && item.status === 'pending') && !guideSkipped.includes(id))
    if (nextId) {
      const next = items.find(item => item.id === nextId)
      setGuideCursor(nextId); setActiveId(nextId); setGuideStep('review')
      if (next?.anchor.trim()) locateAgentQuote(next.anchor.trim(), next.occurrence)
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: nextId, step: 'review' })
    } else {
      setGuideCursor(''); setGuideStep('finish')
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: '', step: 'finish' })
    }
  }
  function skipGuideChange() {
    if (!active || active.status !== 'pending') return
    const skipped = [...new Set([...guideSkipped, active.id])], index = guideOrder.indexOf(active.id)
    const nextId = guideOrder.slice(Math.max(0, index + 1)).find(id => changes.some(item => item.id === id && item.status === 'pending') && !skipped.includes(id))
    setGuideSkipped(skipped)
    if (nextId) {
      const next = changes.find(item => item.id === nextId)
      setGuideCursor(nextId); setActiveId(nextId); setGuideStep('review')
      if (next?.anchor.trim()) locateAgentQuote(next.anchor.trim(), next.occurrence)
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { skipped, cursor: nextId, step: 'review' })
    } else {
      setGuideCursor(''); setGuideStep('finish')
      if (activeDocumentId) writeGuideRecord(activeDocumentId, { skipped, cursor: '', step: 'finish' })
    }
  }
  function moveGuideStep(direction: -1 | 1) {
    const index = GUIDE_STEPS.findIndex(item => item.id === guideStep), next = GUIDE_STEPS[Math.max(0, Math.min(GUIDE_STEPS.length - 1, index + direction))]
    if (!next) return
    if (next.id === 'review' && !runId) return setGuideStage('scan')
    if (next.id === 'review' && guideRunId !== runId && runId) return startGuideReview(runId, changes)
    setGuideStage(next.id)
  }
  function skipGuideStage() {
    if (guideStep === 'template') return setGuideStage('scan')
    if (guideStep === 'scan') return setGuideStage('finish')
  }
  function continueFromDocumentStep() {
    if (!hasContent) { editor?.chain().focus().run(); return }
    setGuideStage('template')
  }
  function continueGuidePending() {
    const next = changes.find(item => item.status === 'pending')
    if (!next) return
    setGuideOpen(true); setGuideCursor(next.id); setActiveId(next.id); setGuideStep('review')
    if (next.anchor.trim()) locateAgentQuote(next.anchor.trim(), next.occurrence)
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
  async function activateDocument(document: WorkspaceDocument, token: number, guideTrigger?: 'new' | 'import') {
    await requestJson('/workspace/active', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: document.id }) })
    if (token !== operationRef.current) return
    workspaceRef.current = document; nameRef.current = document.name; lastRunRef.current = document.last_run_id; headHashRef.current = document.head_hash
    profileRef.current = document.profile_id || 'generic-thesis'; setSelectedProfileId(profileRef.current)
    const storedGuide = readGuideRecord(document.id), shouldAutoOpen = !!guideTrigger && !storedGuide.autoShown
    const initialGuide = shouldAutoOpen ? { ...storedGuide, autoShown: true, open: true, step: guideTrigger === 'import' ? 'template' as GuideStep : 'document' as GuideStep, runId: null, order: [], cursor: '', skipped: [] } : storedGuide
    if (shouldAutoOpen) writeGuideRecord(document.id, initialGuide)
    setGuideOpen(initialGuide.open); setGuideStep(initialGuide.step); setGuideRunId(initialGuide.runId)
    setGuideOrder(initialGuide.order); setGuideCursor(initialGuide.cursor); setGuideSkipped(initialGuide.skipped)
    markdownRef.current = document.content; saveRequiredRef.current = false; saveConflictRef.current = false; closePausedRef.current = false
    revisionRef.current += 1; dirtyRef.current = !!document.content.trim(); setHasLocalEdits(dirtyRef.current)
    setActiveDocumentId(document.id); setDocumentName(document.name); setSaveState('saved'); setSaveError(''); setUploadWarnings([]); setLatex('')
    setDocumentStyle(DEFAULT_DOCUMENT_STYLE); setSuggestedContent(null)
    setRunId(null); setReview(null); setPreviewData(null); setChanges([]); setActiveId(''); setAgentReview(null); setDocumentEpoch(value => value + 1)
    editorRef.current?.commands.setContent(migrateLegacyDocument(document.content, document.editor_json), { emitUpdate: false })
    if (editorRef.current) refreshOutline(editorRef.current)
    updateLibrary(document)
    if (document.last_run_id) {
      try {
        const result = await readRun(document.last_run_id); if (token !== operationRef.current) return
        const mapped = displayRun(document.last_run_id, result)
        if (initialGuide.runId !== document.last_run_id) {
        const first = mapped.find(item => item.status === 'pending')?.id || ''
        const step: GuideStep = initialGuide.step === 'review' ? first ? 'review' : 'finish' : initialGuide.step
          const next = { runId: document.last_run_id, order: mapped.map(item => item.id), cursor: first, step }
          setGuideRunId(next.runId); setGuideOrder(next.order); setGuideCursor(next.cursor); setGuideStep(next.step)
          writeGuideRecord(document.id, next)
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
      if (profile.document_style && workspaceRef.current) { setTemplatePreview(true); setStudioOpen(true) }
      if (guideOpen && guideStep === 'template') setGuideStage('scan')
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
    try { await flushDraft(); const document = await requestJson<WorkspaceDocument>('/workspace/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: '未命名文档.md', content: '', profile_id: profileRef.current, editor_json: { type: 'doc', content: [{ type: 'paragraph' }] } }) }); await activateDocument(document, token, 'new'); editorRef.current?.commands.focus() } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function createExample() {
    const token = beginOperation(); if (token === null) return
    try {
      await flushDraft()
      const document = await requestJson<WorkspaceDocument>('/workspace/documents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: EXAMPLE_NAME, content: EXAMPLE_CONTENT, profile_id: 'generic-thesis' }) })
      await activateDocument(document, token, 'import')
      setGuideStep('scan'); writeGuideRecord(document.id, { open: true, step: 'scan' })
      setLeftRailOpen(false); setRightPanelOpen(false)
      notify('练习文档已准备好，先运行一次检查。')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function prepareAssistant() {
    const documentId = workspaceRef.current?.id
    await flushDraft()
    if (!documentId || documentId !== workspaceRef.current?.id) throw new Error('文档已切换，请在当前文档重试。')
    return readContent()
  }
  async function syncAssistantRun(currentRunId: string) {
    if (!workspaceRef.current || busyRef.current) return
    const id = workspaceRef.current.id
    const document = await requestJson<WorkspaceDocument>('/workspace/documents/' + encodeURIComponent(id))
    if (workspaceRef.current?.id !== id || document.last_run_id !== currentRunId) return
    const result = await readRun(currentRunId)
    if (workspaceRef.current?.id !== id) return
    workspaceRef.current = { ...workspaceRef.current, head_hash: document.head_hash, last_run_id: currentRunId }
    headHashRef.current = document.head_hash; lastRunRef.current = currentRunId
    displayRun(currentRunId, result)
    dirtyRef.current = readContent() !== (result.apiPreview.preview ?? result.apiPreview.source ?? '')
    setHasLocalEdits(dirtyRef.current); updateLibrary(document)
  }
  function assistantAction(action: string, result: TaskResult) {
    if (action === 'settings') { setSettingsOpen(true); return }
    if (action === 'export' || action === 'layout') { setStudioOpen(true); return }
    if (result.review_id) setSelectedReviewId(result.review_id)
    if (['changes', 'agents', 'assistance', 'literature'].includes(action)) openReviewPanel(action as ReviewTab)
  }
  async function handleFileUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
    if (!/\.(md|markdown|txt|docx)$/i.test(file.name)) { setRequestError('支持导入 Markdown、TXT 和 DOCX 文件。'); return }
    if (file.size > 10 * 1024 * 1024) { setRequestError('文件不能超过 10 MB。'); return }
    const token = beginOperation(); if (token === null) return
    try {
      await flushDraft()
      const document = await requestJson<WorkspaceDocument & { source_type: string; warnings: string[] }>('/workspace/import?filename=' + encodeURIComponent(file.name) + '&profile_id=' + encodeURIComponent(profileRef.current), { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file })
      await activateDocument(document, token, 'import')
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
      const run = await requestJson<FormatRunIdentity>('/format-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profileRef.current, document_id: workspaceRef.current.id, document_version_id: 'draft-' + workspaceRef.current.revision, document_name: nameRef.current, document_type: 'markdown', content: readContent(), ...(headHashRef.current ? { expected_head_hash: headHashRef.current } : {}) }) })
      requireCurrentEditor(token, revision); const refreshed = await refreshRun(run.id, token, revision); startGuideReview(run.id, refreshed.changes); openReviewPanel('changes'); notify('发现 ' + refreshed.count + ' 条修改建议')
    } catch (error) { reportError(error, token) } finally { finishOperation(token) }
  }
  async function decide(change: Change, decision: 'accept' | 'reject', advanceGuide = false) {
    if (!requireFreshRun()) return; const token = beginOperation(); if (token === null) return; const revision = revisionRef.current
    try { await flushDraft(); await requestJson('/format-changes/' + change.id + '/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, expected_source_hash: review?.source_hash }) }); const refreshed = await refreshRun(runId!, token, revision); if (advanceGuide) advanceGuideAfterDecision(change, refreshed.changes); notify(decision === 'accept' ? '已接受修改' : '已保留原文') } catch (error) { reportError(error, token) } finally { finishOperation(token) }
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
      const refreshed = await refreshRun(runId!, token, revision)
      if (guideOpen) startGuideReview(runId!, refreshed.changes)
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
  function focusChange(change: Change) {
    setActiveId(change.id)
    if (guideOpen && guideStep === 'review' && guideOrder.includes(change.id)) {
      setGuideCursor(change.id)
      if (change.status === 'pending' && guideSkipped.includes(change.id)) {
        const skipped = guideSkipped.filter(id => id !== change.id)
        setGuideSkipped(skipped)
        if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: change.id, skipped })
      } else if (activeDocumentId) writeGuideRecord(activeDocumentId, { cursor: change.id })
    }
    if (change.anchor.trim()) locateAgentQuote(change.anchor.trim(), change.occurrence)
  }
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
          {activeDocumentId && <details className="model-connection"><summary>模型连接</summary><AgentChannelSwitcher apiBase={API_BASE} onManage={() => setSettingsOpen(true)} disabled={busy}/></details>}
          {activeDocumentId && <button type="button" className="ghost-button compact-action" onClick={() => openReviewPanel('literature')} aria-controls="review-sidebar">查找文献</button>}
          {activeDocumentId && <button type="button" className="soft-button compact-action" onClick={() => openReviewPanel('assistant')} aria-controls="review-sidebar">文档助手</button>}
          <details className="workspace-tools" ref={toolsMenuRef}><summary className="ghost-button" aria-label="打开工具菜单"><MoreHorizontal size={18}/><span>工具</span></summary><div className="workspace-tools-menu" aria-label="文档工具">
            <span className="tools-menu-label">文档</span>
            <button type="button" onClick={() => runTool(() => { void createDocument() })} disabled={busy || workspaceLoading}><FilePlus2 size={16}/>新建文档</button>
            <button type="button" onClick={() => runTool(() => { void createExample() })} disabled={busy || workspaceLoading}><FileText size={16}/>用示例试一次</button>
            <button type="button" onClick={() => runTool(() => fileInputRef.current?.click())} disabled={busy || workspaceLoading}><FileUp size={16}/>导入文档</button>
            <button type="button" onClick={() => runTool(() => setTemplatesOpen(true))} disabled={busy || workspaceLoading}><FileText size={16}/>文档模板</button>
            <button type="button" onClick={() => runTool(() => setStudioOpen(true))} disabled={!activeDocumentId || busy || workspaceLoading}><ArrowDownToLine size={16}/>调整排版与导出</button>
            <span className="tools-menu-label">显示与修订</span>
            <button type="button" onClick={() => runTool(() => setShowDiff(value => !value))} disabled={!activeDocumentId} aria-pressed={showDiff}><GitCompare size={16}/>{showDiff ? '收起正文对比' : '显示正文对比'}</button>
            <button type="button" onClick={() => runTool(() => setFormulaOpen(value => !value))} disabled={!activeDocumentId} aria-pressed={formulaOpen}><Sigma size={16}/>{formulaOpen ? '收起公式工具' : '插入公式'}</button>
            <button type="button" onClick={() => runTool(restoreOriginal)} disabled={!canDecide}><History size={16}/>恢复本次原稿</button>
            <button type="button" onClick={() => runTool(exportReport)} disabled={!canDecide || !hasContent}><ArrowDownToLine size={16}/>导出修订报告</button>
            <span className="tools-menu-label">设置</span>
            <button type="button" onClick={() => runTool(() => setSettingsOpen(true))} aria-label="打开 Agent 设置"><Settings size={16}/>模型设置</button>
            <button type="button" onClick={() => runTool(reopenGuide)} disabled={!activeDocumentId}>使用引导</button>
          </div></details>
        </div>
      </header>
      {!activeDocumentId ? <section className="workspace-welcome" data-testid="workspace-empty">
        {workspaceLoading ? <p role="status">正在打开工作区…</p> : <div className="welcome-card"><span className="welcome-kicker">论文与课程报告 · 修订工作台</span><h1>把一篇初稿，<br/>改到心里有底。</h1><p>从一份练习文档开始：检查问题、核对原文，<br className="desktop-break"/>再确认一次修改。熟悉之后，带入自己的论文。</p><div className="welcome-actions"><button className="primary-button" onClick={() => void createExample()} disabled={busy}>用示例试一次 <span aria-hidden="true">→</span></button><button className="soft-button" onClick={() => fileInputRef.current?.click()} disabled={busy}><FileUp size={16} />导入我的文档</button></div><div className="welcome-secondary"><button className="text-action" onClick={() => void createDocument()} disabled={busy}>从空白文档开始</button><span>支持 DOCX、Markdown、TXT</span></div><ol className="welcome-route"><li><span>01</span><div><strong>找到问题</strong><p>按文档规范检查格式与结构</p></div></li><li><span>02</span><div><strong>核对修改</strong><p>看清原文、建议和依据再决定</p></div></li><li><span>03</span><div><strong>带走确认稿</strong><p>导出文档，保留修订记录</p></div></li></ol>{requestError && <div className="review-alert" role="alert">{requestError}</div>}{documents.length > 0 && <div className="welcome-documents"><h2>最近文档</h2>{documents.map(document => <div className="welcome-document-row" key={document.id}><button onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={14}/><span>{document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}</div>}</div>}
      </section> : <>
        <section className="workspace-toolbar"><div className="crumbs"><strong>文档工作区</strong><span className={'status-pill save-' + saveState} role="status">{saveLabels[saveState]}</span>{hasLocalEdits && runId && <span className="draft-indicator">正文已修改</span>}</div><div className="toolbar-actions"><button className="ghost-button guide-toggle" onClick={guideOpen ? exitGuide : reopenGuide}>{guideOpen ? '收起引导' : '使用引导'}</button><button type="button" className="ghost-button compact-action" onClick={() => setStudioOpen(true)} disabled={!hasContent || busy}>排版与导出</button><button className="primary-button" onClick={scanFormat} disabled={busy || !hasContent || saveConflictRef.current}><ScanLine size={16}/>{scanState || '检查文档'}</button></div></section>
        {guideOpen && <section className="quick-guide" data-guide-step={guideStep} aria-label="使用引导"><div className="guide-position"><small>下一步</small><span>{GUIDE_STEPS.findIndex(item => item.id === guideStep) + 1} / 5</span><strong>{GUIDE_STEPS.find(item => item.id === guideStep)?.label}</strong></div><p>{({ document: '先输入正文，也可以从工具菜单导入文档。', template: '上传学校的 Word / PDF 模板，自动填写格式要求。', scan: '运行本地检查，看看哪里需要修改。无需配置模型。', review: `右侧有 ${pending.length} 项待确认建议。先看原文和变化，再接受或保留。`, finish: pending.length ? `还有 ${pending.length} 项未确认。可继续核对，也可只导出当前确认稿。` : '修改已处理。导出当前稿，完成这次练习。' })[guideStep]}</p><div className="guide-actions">{guideStep === 'document' && <button className="soft-button" disabled={!hasContent || busy} onClick={continueFromDocumentStep}>正文已准备好</button>}{guideStep === 'template' && <><button className="soft-button" onClick={openGuideTemplates}>选择规范</button><button className="text-action" onClick={() => setGuideStage('scan')}>使用当前规范</button></>}{guideStep === 'scan' && <button className="soft-button" disabled={busy || !hasContent} onClick={() => void scanFormat()}>开始检查</button>}{guideStep === 'review' && <button className="soft-button" onClick={() => { openReviewPanel('changes'); if (active) focusChange(active) }}>查看建议</button>}{guideStep === 'finish' && <><button className="soft-button" onClick={openGuideExport}>导出当前稿</button>{pending.length > 0 && <button className="text-action" onClick={() => { continueGuidePending(); openReviewPanel('changes') }}>继续核对</button>}</>}<button className="icon-button" aria-label="退出使用引导" onClick={exitGuide}><X size={15}/></button></div></section>}
        <div className={'workspace-grid' + (!leftRailOpen ? ' nav-collapsed' : '') + (!rightPanelOpen ? ' review-collapsed' : '')}>
          {(leftRailOpen || rightPanelOpen) && <button type="button" className="panel-backdrop" aria-label="关闭侧栏，返回正文" onClick={() => { setLeftRailOpen(false); setRightPanelOpen(false) }}/>}
          <aside className="sidebar left-sidebar" id="document-sidebar" aria-label="文档与目录">
            <div className="side-head"><h2>我的文档</h2><button className="icon-button" onClick={() => setLibraryOpen(value => !value)} aria-label={libraryOpen ? '折叠文档列表' : '展开文档列表'} aria-expanded={libraryOpen}><ChevronDown size={16} className={!libraryOpen ? 'collapsed-chevron' : ''}/></button></div>
            <div className="library-actions"><button onClick={() => void createDocument()} disabled={busy}><FilePlus2 size={14}/>新建</button><button onClick={() => fileInputRef.current?.click()} disabled={busy}><FileUp size={14}/>导入</button></div>
            {libraryOpen && <><label className="library-search-label" htmlFor="document-search">搜索文档</label><input id="document-search" className="library-search" type="search" value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="按名称筛选" aria-label="搜索文档"/><nav className="document-library" aria-label="文档列表">{visibleDocuments().map(document => <div className="library-document-row" key={document.id}><button className={'library-document ' + (document.id === activeDocumentId ? 'active' : '')} onClick={() => switchDocument(document.id)} disabled={busy}><FileText size={15}/><span title={document.name}>{document.id === activeDocumentId ? documentName || '未命名文档' : document.name}</span></button>{agentTasks[document.id] && <button className={'agent-task-badge task-' + agentTasks[document.id].status} onClick={() => void openDocumentReview(document.id)} disabled={busy} aria-label={'打开 ' + document.name + ' 的审查任务'} title={agentTasks[document.id].review_id}>{agentTaskLabel(agentTasks[document.id])}</button>}</div>)}{visibleDocuments().length === 0 && <p className="review-empty">没有匹配的文档。</p>}</nav>{taskStatusError && <p className="task-status-error" role="status">审查状态暂不可用，请检查本地服务连接。</p>}</>}
            <div className="section-heading outline-heading"><span>文档目录 · {outline.length ? outline.length + ' 个标题' : '暂无标题'}</span><button className="icon-button" onClick={() => setOutlineOpen(value => !value)} aria-label={outlineOpen ? '折叠文档目录' : '展开文档目录'} aria-expanded={outlineOpen}><ChevronDown size={16} className={!outlineOpen ? 'collapsed-chevron' : ''}/></button></div>
            {outlineOpen && <nav className="outline" aria-label="文档目录">{outline.length ? outline.map((item, index) => <button key={index} className="outline-item" style={{paddingLeft: 12 + Math.min(item.level - 1, 4) * 12}} onClick={() => goToHeading(item)} title={item.title}><span>{item.title}</span></button>) : <p className="review-empty">正文中的标题会显示在这里。</p>}</nav>}
          </aside>
          <section className="editor-panel">
            <div className="editor-head"><div className="document-heading"><label htmlFor="document-name">文档名称</label><input id="document-name" aria-label="文档名称" value={documentName} onChange={event => renameDocument(event.target.value)} onBlur={() => { void flushDraft().catch(() => undefined) }} disabled={busy}/></div><button className="soft-button" onClick={() => { void flushDraft().then(() => notify('已保存')).catch(() => undefined) }} disabled={busy || saveState === 'saving' || saveState === 'conflict'}><Save size={14}/>保存</button></div>
            <div className={'active-template' + (guideOpen && guideStep === 'template' ? ' guide-target' : '')}><span>文档模板：{profileNames[selectedProfileId] || selectedProfileId}</span><button type="button" className="ghost-button" onClick={() => setTemplatesOpen(true)} disabled={busy}>切换模板</button></div>
            {requestError && <div className="review-alert" role="alert">{requestError}</div>}{saveError && <div className="review-alert" role="alert">{saveError}{saveState === 'error' && <button onClick={() => { void flushDraft().catch(() => undefined) }}>重试保存</button>}{saveState === 'conflict' && <><button onClick={exportMarkdown} disabled={!hasContent}>导出当前副本</button><button onClick={reopenSavedDocument}>重新打开已保存版本</button></>}</div>}
            {hasLocalEdits && runId && <div className="review-alert warning" role="status">正文已修改，重新扫描后可继续处理建议。</div>}{uploadWarnings.length > 0 && <details className="import-notice"><summary>导入说明</summary><ul>{uploadWarnings.map(warning => <li key={warning}>{warning}</li>)}</ul></details>}
            <div className="paper-wrap"><article className="paper styled-manuscript" style={documentStyleCss(documentStyle)}><EditorContent editor={editor}/>{!hasContent && <p className="editor-placeholder">在这里输入正文，或导入文档开始编辑。</p>}</article></div>
            <div className="formula-bar" hidden={!formulaOpen}><div className="formula-label-left"><Sigma size={16}/>LaTeX 公式</div><input value={latex} onChange={event => setLatex(event.target.value)} placeholder="输入 LaTeX 公式" aria-label="LaTeX 公式源码" disabled={busy}/><button className="insert-button" onClick={insertFormula} disabled={busy || !latex.trim()}>插入公式</button><button type="button" className="icon-button" onClick={() => setFormulaOpen(false)} aria-label="收起公式工具"><X size={15}/></button>{formulaNotice && <span className="formula-notice" role="status"><Check size={14}/>{formulaNotice}</span>}</div>
            <div className="format-diff-container" hidden={!showDiff}><FormatDiffPanel apiBase={API_BASE} runId={runId} review={review} pendingCount={pending.length} dirty={hasLocalEdits} disabled={busy}/></div>
          </section>
          <aside className="sidebar right-sidebar" id="review-sidebar" aria-label="审查工作区"><div className="review-rail-head"><span>文档工具</span><button type="button" className="icon-button" onClick={() => setRightPanelOpen(false)} aria-label="收起审查面板"><PanelRight size={16}/></button></div>
            <div className="tabs workspace-tabs" role="group" aria-label="文档工具导航">{([['assistant', '助手'], ['changes', '修改建议'], ['literature', '查文献'], ['agents', '内容审查'], ['assistance', '导师与资料'], ['review', '修订检查']] as const).map(([id, title]) => <button key={id} className={tab === id ? 'tab active' : 'tab'} aria-pressed={tab === id} onClick={() => setTab(id)}>{title}{id === 'changes' && pending.length > 0 && <span>{pending.length}</span>}</button>)}</div>
            {tab === 'changes' && <div className="review-progress"><h2>{hasLocalEdits && runId ? '等待重新检查' : '逐项核对修改'}</h2><div className="review-counts"><span>{review?.applied_count ?? 0} 项已应用</span><span>{pending.length} 项待确认</span>{!!review?.blocked_change_ids.length && <span>{review.blocked_change_ids.length} 项冲突</span>}</div></div>}
            {tab === 'assistant' && <WorkspaceAssistant key={activeDocumentId} apiBase={API_BASE} documentId={activeDocumentId} content={readContent()} disabled={busy || saveState === 'conflict'} prepare={prepareAssistant} onAction={assistantAction} onRun={syncAssistantRun} onStyle={setDocumentStyle} onLocate={locateAgentQuote}/>}
            {tab === 'literature' && <LiteraturePanel key={activeDocumentId} apiBase={API_BASE} documentId={activeDocumentId} prepare={prepareAssistant} disabled={busy || saveState === 'conflict'}/>}
            <AgentReviewPanel apiBase={API_BASE} runId={runId} sourceHash={review?.source_hash ?? ''} previewHash={review?.preview_hash ?? ''} documentEpoch={documentEpoch} dirty={hasLocalEdits} busy={busy} hidden={tab !== 'agents'} selectedReviewId={selectedReviewId} applyVersion={applyAgentVersion} onLocate={locateAgentQuote} onReviewChange={setAgentReview}/>
            {tab === 'assistance' && <DocumentAssistant apiBase={API_BASE} documentId={activeDocumentId} documentName={documentName} content={readContent()} profileId={selectedProfileId} documentEpoch={documentEpoch} onLocate={locateAgentQuote}/>}
            {tab === 'changes' ? <>
              <div className="change-list">
                {!!pending.length && <button type="button" className="bulk-accept-button" onClick={acceptAllPending} disabled={!canDecide || !pending.some(change => !review?.blocked_change_ids.includes(change.id) && ['paragraph.spacing','heading.spacing','heading.numbering'].includes(change.rule))} title="先在对比中检查；仅整批接受标题编号及空白格式项，引用和内容建议逐项审阅">确认低风险格式修改 <span>{pending.filter(change => !review?.blocked_change_ids.includes(change.id) && ['paragraph.spacing','heading.spacing','heading.numbering'].includes(change.rule)).length}</span></button>}
                {lastBatch?.runId === runId && <button type="button" className="ghost-button" onClick={undoBatch} disabled={!canDecide}>撤销本批格式修改</button>}
                {!changes.length && <p className="review-empty">{runId ? '未发现需要修改的内容。' : '扫描当前文档以查看修改建议。'}</p>}
                <GroupedChanges changes={pending} activeId={activeId} contextKey={String(runId) + ':' + documentEpoch} disabled={!canDecide} blockedIds={review?.blocked_change_ids ?? []} onLocate={focusChange} onDecide={(change, decision) => { void decide(change, decision, guideOpen) }} onBatch={(items, decision) => { void decideGroup(items, decision) }}/>
                {!!accepted.length && <details className="resolved-changes"><summary>已接受 · {accepted.length} 项</summary>{accepted.map(change => <button type="button" key={change.id} className="resolved-card accepted" onClick={() => focusChange(change)}><Check size={14}/><span>{change.title}</span><small>已接受</small></button>)}</details>}
                {!!changes.filter(item => item.status === 'rejected').length && <details className="resolved-changes"><summary>已保留 · {changes.filter(item => item.status === 'rejected').length} 项</summary>{changes.filter(item => item.status === 'rejected').map(change => <button type="button" key={change.id} className="resolved-card rejected" onClick={() => focusChange(change)}><X size={14}/><span>{change.title}</span><small>已保留</small></button>)}</details>}
              </div>
            </> : tab === 'review' ? <div className="debate-list" aria-label="修订检查报告">{!review ? <p className="review-empty">请先扫描当前文档。</p> : <><div className={'judge-note review-result-' + review.status}>{reviewStatus[review.status]}：{review.summary}</div>{review.checks.map(check => <div className="agent-row" key={check.id}><div className={'agent-avatar ' + (check.status === 'passed' ? 'teal' : 'amber')}>{check.status === 'passed' ? <Check size={13}/> : <AlertTriangle size={13}/>}</div><div><div className="agent-name">{check.title}<span className="review-check-status">{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '待处理'}</span></div><p>{check.detail}</p></div></div>)}</>}</div> : null}
          </aside>
        </div>
        <footer className="statusbar"><div><span className={'save-status save-' + saveState}>{saveLabels[saveState]}</span>{hasContent && <span>{editor?.getText().replace(/\s/g, '').length ?? 0} 字</span>}</div><div><button onClick={undoLast} disabled={!canDecide || !accepted.length}><RotateCcw size={13}/>撤销</button><button onClick={() => setStudioOpen(true)} disabled={!hasContent || busy}><ArrowDownToLine size={13}/>导出</button></div></footer>
      </>}
      <DocumentStudio apiBase={API_BASE} open={studioOpen} previewTemplateOnOpen={templatePreview} onClose={() => { setStudioOpen(false); setTemplatePreview(false) }} documentId={activeDocumentId} documentName={documentName} content={readContent()} originalContent={hasLocalEdits ? null : previewData?.source ?? null} suggestedContent={hasLocalEdits ? null : suggestedContent} profileStyle={profileStyles[selectedProfileId] ?? null} documentEpoch={documentEpoch} disabled={busy || workspaceLoading || saveState === 'conflict'} onStyleChange={setDocumentStyle} onExport={completeGuideExport}/>
      <AgentSettings apiBase={API_BASE} open={settingsOpen} onClose={() => setSettingsOpen(false)}/>
      <SchoolTemplatePanel apiBase={API_BASE} open={templatesOpen} selectedProfileId={selectedProfileId} onClose={() => setTemplatesOpen(false)} onSelect={selectSchoolTemplate} disabled={busy}/>
      {toast && <div className="toast"><Check size={15}/>{toast}</div>}
    </main>
  )
}
