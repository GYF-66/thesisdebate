'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import { ArrowDownToLine, Check, FileText, LoaderCircle, MessageSquare, RotateCcw, Send, X } from 'lucide-react'
import { editorExtensions, parseMarkdown } from '../lib/markdown'
import { chatDocumentStyle, cloneDocumentStyle, copyStyleField, DEFAULT_DOCUMENT_STYLE, documentStyleChanges, documentStyleCss, exportDocument, FontConfirmationRequiredError, getDocumentLayout, saveDocumentLayout, validateDocumentStyle, type FontIssue, type DocumentFont, type DocumentHeadingStyle, type DocumentStyle, type ExportFormat, type StudioMessage } from '../lib/documentStudio'
import FontPicker from './FontPicker'
import LineSpacingControl from './LineSpacingControl'
import './document-studio.css'
import { styleChangeGroup } from '../lib/changeGrouping'
import './grouped-changes.css'

type Props = {
  apiBase: string; open: boolean; onClose: () => void; documentId: string | null; documentEpoch: number
  documentName: string; content: string; originalContent: string | null; suggestedContent: string | null
  previewTemplateOnOpen?: boolean; profileStyle: DocumentStyle | null; disabled: boolean; onStyleChange?: (style: DocumentStyle) => void
  onExport?: (scope: ExportScope) => void
}
type ExportScope = 'original' | 'expected' | 'current'
type ChatMessage = StudioMessage & { provider?: 'local' | 'configured'; warnings?: string[] }
type PdfPair = { key: string; original: string; expected: string }
type FontConfirmation = { key: string; action: 'pdf' | 'export'; issues: FontIssue[]; substitutions: Record<string, string> }
const scopeNames = { original: '原稿', expected: '预期稿', current: '当前稿' }
const errorMessage = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '无法连接本机排版服务，请重试。'
const bodyFields: { field: Exclude<keyof DocumentStyle, 'font_family' | 'headings'>; label: string; min: number; max: number; step: number }[] = [
  { field: 'font_size_pt', label: '正文字号（磅）', min: 8, max: 36, step: 0.5 },
  { field: 'paragraph_before_pt', label: '正文段前（磅）', min: 0, max: 72, step: 1 },
  { field: 'paragraph_after_pt', label: '正文段后（磅）', min: 0, max: 72, step: 1 },
  { field: 'first_line_indent_pt', label: '首行缩进（磅）', min: 0, max: 72, step: 1 },
]
const marginFields: { field: 'margin_top_mm' | 'margin_bottom_mm' | 'margin_left_mm' | 'margin_right_mm'; label: string }[] = [
  { field: 'margin_top_mm', label: '上页边距（毫米）' }, { field: 'margin_bottom_mm', label: '下页边距（毫米）' },
  { field: 'margin_left_mm', label: '左页边距（毫米）' }, { field: 'margin_right_mm', label: '右页边距（毫米）' },
]

function ReadOnlyPaper({ content, style, label }: { content: string; style: DocumentStyle; label: string }) {
  const parsed = useMemo(() => { try { return { document: parseMarkdown(content), error: '' } } catch { return { document: parseMarkdown(''), error: '此稿暂时无法显示排版预览，仍可查看源码并导出。' } } }, [content])
  const editor = useEditor({ extensions: editorExtensions, content: parsed.document, editable: false, immediatelyRender: false, editorProps: { attributes: { 'aria-label': label + '排版预览', tabindex: '0' } } })
  const containerRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.6)
  useEffect(() => {
    const node = containerRef.current
    if (!node) return
    const resize = new ResizeObserver(entries => { const width = entries[0]?.contentRect.width ?? 500; setScale(Math.min(1, Math.max(0.2, (width - 24) / (210 * 96 / 25.4)))) })
    resize.observe(node)
    return () => resize.disconnect()
  }, [])
  useEffect(() => { editor?.commands.setContent(parsed.document, { emitUpdate: false }) }, [editor, parsed.document])
  return <div className="ds-paper-scroll" ref={containerRef}><article className="ds-page ds-document" style={{ ...documentStyleCss(style), zoom: scale }} aria-label={label + '纸张'}>{parsed.error ? <p role="alert">{parsed.error}</p> : <EditorContent editor={editor}/>}</article></div>
}

export default function DocumentStudio({ apiBase, open, onClose, documentId, documentEpoch, documentName, content, originalContent, suggestedContent, profileStyle, previewTemplateOnOpen = false, disabled, onStyleChange, onExport }: Props) {
  const [currentStyle, setCurrentStyle] = useState<DocumentStyle>(() => cloneDocumentStyle(DEFAULT_DOCUMENT_STYLE))
  const [draftStyle, setDraftStyle] = useState<DocumentStyle>(() => cloneDocumentStyle(DEFAULT_DOCUMENT_STYLE))
  const [originalStyle, setOriginalStyle] = useState<DocumentStyle>(() => cloneDocumentStyle(DEFAULT_DOCUMENT_STYLE))
  const [openingContent, setOpeningContent] = useState(content)
  const [revision, setRevision] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [working, setWorking] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [exportLimitations, setExportLimitations] = useState<string[]>([])
  const [chatInput, setChatInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [undoStyles, setUndoStyles] = useState<DocumentStyle[]>([])
  const [exportScope, setExportScope] = useState<ExportScope>('current')
  const [exportFormat, setExportFormat] = useState<ExportFormat>('pdf')
  const [docxMode, setDocxMode] = useState<'preserve' | 'editable_copy'>('preserve')
  const [sourceInfo, setSourceInfo] = useState<{retained: boolean; warnings: string[]; mapped_paragraphs?: number} | null>(null)
  const [previewMode, setPreviewMode] = useState<'layout' | 'pdf' | 'source'>('layout')
  const [pdfPair, setPdfPair] = useState<PdfPair | null>(null)
  const [fontConfirmation, setFontConfirmation] = useState<FontConfirmation | null>(null)
  const [loadNonce, setLoadNonce] = useState(0)
  const modalRef = useRef<HTMLDivElement>(null)
  const callbackRef = useRef(onStyleChange)
  callbackRef.current = onStyleChange
  const baselineRef = useRef<{ key: string; style: DocumentStyle; content: string } | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const operationRef = useRef(0)
  const pdfRef = useRef<PdfPair | null>(null)
  const contextKey = JSON.stringify([documentId, documentEpoch])
  const contextRef = useRef(contextKey)
  contextRef.current = contextKey
  const expectedContent = suggestedContent ?? content
  const original = originalContent ?? openingContent
  const previewKey = JSON.stringify([contextKey, original, expectedContent, originalStyle, draftStyle])
  const snapshotRef = useRef(previewKey)
  snapshotRef.current = previewKey
  const confirmationKey = JSON.stringify([previewKey, content, currentStyle, exportScope, exportFormat, docxMode, open])
  const confirmationRef = useRef(confirmationKey)
  confirmationRef.current = confirmationKey
  useEffect(() => { setFontConfirmation(null) }, [confirmationKey])
  const changes = useMemo(() => documentStyleChanges(currentStyle, draftStyle), [currentStyle, draftStyle])
  const styleGroups = [...new Set(changes.map(change => styleChangeGroup(change.field)))].map(title => ({title, items:changes.filter(change => styleChangeGroup(change.field) === title)}))
  const blocked = disabled || loading || !!working || revision === null || !documentId

  function clearPdf() {
    if (pdfRef.current) { URL.revokeObjectURL(pdfRef.current.original); URL.revokeObjectURL(pdfRef.current.expected) }
    pdfRef.current = null; setPdfPair(null)
  }

  useEffect(() => {
    if (!open || !documentId) return
    const controller = new AbortController()
    setSourceInfo(null); setDocxMode('preserve')
    void fetch(apiBase + '/workspace/documents/' + encodeURIComponent(documentId) + '/source-compatibility', {signal:controller.signal})
      .then(async response => {if (!response.ok) throw new Error('原文件兼容性检查暂不可用'); return response.json() as Promise<{retained:boolean;warnings:string[];mapped_paragraphs?:number}>})
      .then(value => {if (!controller.signal.aborted) setSourceInfo(value)})
      .catch(() => {if (!controller.signal.aborted) setSourceInfo({retained:false,warnings:['无法读取原文件保护状态；导出前请检查本地服务。']})})
    return () => controller.abort()
  }, [apiBase, open, documentId, documentEpoch])

  useEffect(() => {
    if (!open || !documentId) return
    const controller = new AbortController()
    requestRef.current?.abort(); requestRef.current = controller
    const operation = ++operationRef.current
    setLoading(true); setRevision(null); setWorking(''); setError(''); setNotice(''); setExportLimitations([]); clearPdf()
    if (baselineRef.current?.key !== contextKey) { setMessages([]); setChatInput(''); setUndoStyles([]); setPreviewMode('layout'); setExportScope('current') }
    void getDocumentLayout(apiBase, documentId, controller.signal).then(result => {
      if (operation !== operationRef.current || contextRef.current !== contextKey || controller.signal.aborted) return
      setCurrentStyle(result.style); setDraftStyle(cloneDocumentStyle(previewTemplateOnOpen && profileStyle ? profileStyle : result.style)); setRevision(result.revision)
      if (previewTemplateOnOpen && profileStyle) setNotice('已自动载入模板排版。核对预期稿后，点击“应用全部排版”。')
      if (baselineRef.current?.key !== contextKey) baselineRef.current = { key: contextKey, style: cloneDocumentStyle(result.style), content }
      setOriginalStyle(cloneDocumentStyle(baselineRef.current.style))
      setOpeningContent(baselineRef.current.content)
      callbackRef.current?.(result.style)
    }).catch(failure => { if (!controller.signal.aborted && operation === operationRef.current) setError(errorMessage(failure)) })
      .finally(() => { if (operation === operationRef.current) setLoading(false) })
    return () => { controller.abort(); requestRef.current?.abort(); operationRef.current++; clearPdf() }
  }, [apiBase, open, documentId, documentEpoch, contextKey, loadNonce, previewTemplateOnOpen])

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; modalRef.current?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [open])

  if (!open) return null

  function begin(action: string) {
    requestRef.current?.abort()
    const controller = new AbortController(); requestRef.current = controller
    const operation = ++operationRef.current
    setWorking(action); setError(''); setNotice('')
    return { controller, operation, context: contextKey, snapshot: previewKey, confirmation: confirmationKey }
  }
  function valid(request: ReturnType<typeof begin>, checkContent = false) {
    return !request.controller.signal.aborted && request.operation === operationRef.current && request.context === contextRef.current && (!checkContent || request.snapshot === snapshotRef.current && request.confirmation === confirmationRef.current)
  }
  function finish(request: ReturnType<typeof begin>) { if (request.operation === operationRef.current) setWorking('') }
  function close() { if (working === 'save') return; requestRef.current?.abort(); operationRef.current++; onClose() }
  function updateBody(field: keyof DocumentStyle, value: string) {
    if (field === 'font_family') setDraftStyle(style => ({ ...style, font_family: value as DocumentFont }))
    else { const number = Number(value); if (value !== '' && Number.isFinite(number)) setDraftStyle(style => ({ ...style, [field]: number })) }
    setError(''); setNotice('')
  }
  function updateHeading(index: number, field: keyof DocumentHeadingStyle, value: string) {
    const parsed = field === 'font_family' ? value : Number(value)
    if (value === '' || typeof parsed === 'number' && !Number.isFinite(parsed)) return
    setDraftStyle(style => { const next = cloneDocumentStyle(style); Object.assign(next.headings[index], { [field]: parsed }); return next })
    setError(''); setNotice('')
  }

  async function applyStyle(style: DocumentStyle, label: string, undo = false) {
    if (blocked || !documentId || revision === null) return
    const request = begin('save')
    try {
      const next = validateDocumentStyle(style)
      const result = await saveDocumentLayout(apiBase, documentId, revision, next, request.controller.signal)
      if (!valid(request)) return
      if (undo) { setUndoStyles(stack => stack.slice(0, -1)); setDraftStyle(cloneDocumentStyle(result.style)) }
      else setUndoStyles(stack => [...stack, cloneDocumentStyle(currentStyle)])
      setCurrentStyle(result.style); setRevision(result.revision); callbackRef.current?.(result.style)
      setNotice(label + '，已保存到当前文档。正文内容保持原样。')
    } catch (failure) { if (valid(request)) setError(errorMessage(failure)) }
    finally { finish(request) }
  }

  async function sendChat(message = chatInput) {
    if (blocked || !documentId || !message.trim()) return
    const request = begin('chat'), prompt = message.trim()
    const history = messages.slice(-12).map(({ role, content: text }) => ({ role, content: text }))
    setMessages(items => [...items, { role: 'user', content: prompt }]); setChatInput('')
    try {
      const result = await chatDocumentStyle(apiBase, { document_id: documentId, message: prompt, style: draftStyle, history, content: content.slice(0, 15000), ...(profileStyle ? { profile_style: profileStyle } : {}) }, request.controller.signal)
      if (!valid(request, true)) { if (valid(request)) setError('正文或文档已变化，这次旧排版答复未应用，请重新发送。'); return }
      setMessages(items => [...items, { role: 'assistant', content: result.reply, provider: result.provider, warnings: result.warnings }])
      if (result.proposed_style) { setDraftStyle(result.proposed_style); setNotice('已生成待应用排版，可先对照预期稿，再接受修改。') }
    } catch (failure) { if (valid(request)) setError(errorMessage(failure)) }
    finally { finish(request) }
  }

  function confirmFonts(failure: unknown, action: 'pdf' | 'export', substitutions: Record<string, string>) {
    if (!(failure instanceof FontConfirmationRequiredError)) return false
    const mapping = { ...substitutions }
    failure.issues.forEach(issue => { mapping[issue.requested_font] = mapping[issue.requested_font] || issue.suggested_font || '' })
    setFontConfirmation({ key: confirmationKey, action, issues: failure.issues, substitutions: mapping })
    setError(''); setNotice('PDF 使用的字体需要确认；选择替代后才会继续本次操作。')
    return true
  }

  async function renderPdfPair(substitutions: Record<string, string> = {}) {
    if (blocked) return
    const request = begin('pdf')
    try {
      const results = await Promise.allSettled([
        exportDocument(apiBase, documentName + '-原稿', original, originalStyle, 'pdf', request.controller.signal, null, 'preserve', substitutions),
        exportDocument(apiBase, documentName + '-预期稿', expectedContent, draftStyle, 'pdf', request.controller.signal, null, 'preserve', substitutions),
      ])
      const failures = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected')
      const otherFailure = failures.find(result => !(result.reason instanceof FontConfirmationRequiredError))
      if (otherFailure) throw otherFailure.reason
      if (failures.length) {
        const issues = [...new Map(failures.flatMap(result => (result.reason as FontConfirmationRequiredError).issues).map(issue => [issue.requested_font, issue])).values()]
        throw new FontConfirmationRequiredError('请确认原稿与预期稿的PDF字体替代。', issues)
      }
      const [before, after] = results.map(result => (result as PromiseFulfilledResult<Awaited<ReturnType<typeof exportDocument>>>).value)
      if (!valid(request, true)) { if (valid(request)) setError('预期稿已变化，请重新生成 PDF 对照。'); return }
      clearPdf()
      const pair = { key: request.snapshot, original: URL.createObjectURL(before.blob), expected: URL.createObjectURL(after.blob) }
      pdfRef.current = pair; setPdfPair(pair); setPreviewMode('pdf'); setFontConfirmation(null); setNotice('PDF 对照已由本机导出服务生成。')
      setExportLimitations([...before.limitations.map(item => '原稿：' + item), ...after.limitations.map(item => '预期稿：' + item)])
    } catch (failure) { if (valid(request, true) && !confirmFonts(failure, 'pdf', substitutions)) setError(errorMessage(failure)) }
    finally { finish(request) }
  }

  async function download(substitutions: Record<string, string> = {}) {
    if (blocked) return
    const request = begin('export')
    try {
      const exportedContent = exportScope === 'original' ? original : exportScope === 'expected' ? expectedContent : content
      const exportedStyle = exportScope === 'original' ? originalStyle : exportScope === 'expected' ? draftStyle : currentStyle
      const name = documentName.replace(/\.[^.]+$/, '') + '-' + scopeNames[exportScope]
      const result = await exportDocument(apiBase, name, exportedContent, exportedStyle, exportFormat, request.controller.signal, documentId, docxMode, substitutions)
      if (!valid(request, true)) { if (valid(request)) setError('文档已变化，请重新导出当前版本。'); return }
      const url = URL.createObjectURL(result.blob), anchor = document.createElement('a')
      anchor.href = url; anchor.download = name + '.' + exportFormat; document.body.appendChild(anchor); anchor.click(); anchor.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
      setNotice('已导出' + scopeNames[exportScope] + ' ' + exportFormat.toUpperCase() + '。')
      onExport?.(exportScope)
      setFontConfirmation(null)
      setExportLimitations(result.limitations)
    } catch (failure) { if (valid(request, true) && !confirmFonts(failure, 'export', substitutions)) setError(errorMessage(failure)) }
    finally { finish(request) }
  }

  const pdfReady = pdfPair?.key === previewKey
  return <div className="ds-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
    <div className="ds-modal" ref={modalRef} role="dialog" aria-modal="true" aria-labelledby="document-studio-title" tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close() }
      if (event.key === 'Tab') {
        const focusable = Array.from(modalRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]') ?? []).filter(element => element.offsetParent !== null)
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && (document.activeElement === first || document.activeElement === modalRef.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === modalRef.current)) { event.preventDefault(); first?.focus() }
      }
    }}>
      <header className="ds-header"><div><h2 id="document-studio-title"><FileText size={21}/>预期效果与导出</h2><p>{documentName || '当前文档'} · 接受前先检查排版，再保存或导出</p></div><button type="button" className="ghost-button" aria-label="关闭预期效果与导出" disabled={working === 'save'} onClick={close}><X size={20}/></button></header>
      <div className="ds-export-bar"><label>导出范围<select aria-label="导出范围" value={exportScope} onChange={event => setExportScope(event.target.value as ExportScope)} disabled={blocked}><option value="original">原稿</option><option value="expected">预期稿（含待接受建议）</option><option value="current">当前稿（已接受）</option></select></label><label>文件格式<select aria-label="导出格式" value={exportFormat} onChange={event => setExportFormat(event.target.value as ExportFormat)} disabled={blocked}><option value="pdf">PDF · 固定版式</option><option value="docx">DOCX · 可继续编辑</option><option value="md">Markdown · 正文源码</option></select></label><button type="button" className="primary-button" onClick={() => { void download() }} disabled={blocked}><ArrowDownToLine size={15}/>{working === 'export' ? '正在导出…' : '导出所选稿件'}</button><span className="ds-export-help">MD 保存正文与公式源码；DOCX/PDF 的公式显示能力以导出提示为准。</span></div>
      {exportFormat === 'docx' && <div className="ds-export-limitations"><label>DOCX 导出方式 <select aria-label="DOCX 导出方式" value={docxMode} onChange={event => setDocxMode(event.target.value as 'preserve' | 'editable_copy')} disabled={blocked}><option value="preserve">结构保护 · 保留原 Word 版式</option><option value="editable_copy">可编辑副本 · 使用预览样式（可能丢失原对象）</option></select></label>{docxMode === 'preserve' && <p>结构保护导出保留原 DOCX 字体和版式；本窗口字体设置用于 PDF 与可编辑副本。</p>}{sourceInfo?.warnings.map((warning,index) => <p key={index}>{warning}</p>)}{sourceInfo?.retained && <a href={apiBase + '/workspace/documents/' + encodeURIComponent(documentId!) + '/original.docx'} download>下载留存的原 DOCX</a>}</div>}
      {loading && <p className="ds-status" role="status"><LoaderCircle size={16}/>正在读取文档排版…</p>}
      {error && <div className="ds-alert" role="alert">{error}{revision === null && <button type="button" className="ghost-button" onClick={() => setLoadNonce(value => value + 1)} disabled={loading}>重新读取排版</button>}</div>}
      {fontConfirmation?.key === confirmationKey && <section className="ds-font-confirmation" aria-label="确认PDF字体替代"><strong>确认本次 PDF 字体替代</strong><p>以下字体未满足本次 PDF 的安装、字形或嵌入要求。仅对本次导出替代，不改变文档保存的字体。</p>{fontConfirmation.issues.map(issue => <div key={issue.requested_font}><p><strong>{issue.requested_font}</strong>：{issue.reason}</p><label>替代字体<FontPicker apiBase={apiBase} value={fontConfirmation.substitutions[issue.requested_font] || ''} disabled={blocked} ariaLabel={'替代' + issue.requested_font} onChange={name => setFontConfirmation(value => value && ({...value, substitutions:{...value.substitutions,[issue.requested_font]:name}}))}/></label></div>)}<div className="ds-font-confirmation-actions"><button type="button" className="primary-button" disabled={blocked || fontConfirmation.issues.some(issue => !fontConfirmation.substitutions[issue.requested_font] || fontConfirmation.substitutions[issue.requested_font] === issue.requested_font)} onClick={() => { const pending = fontConfirmation; if (pending.key !== confirmationRef.current) { setFontConfirmation(null); return } if (pending.action === 'pdf') void renderPdfPair(pending.substitutions); else void download(pending.substitutions) }}>确认替代并继续</button><button type="button" className="ghost-button" disabled={!!working} onClick={() => {setFontConfirmation(null); setNotice('已取消本次字体替代。')}}>取消本次操作</button></div></section>}
      {notice && <p className="ds-notice" role="status"><Check size={15}/>{notice}</p>}
      {!!exportLimitations.length && <div className="ds-export-limitations" role="status"><strong>本次导出说明</strong>{exportLimitations.map((item, index) => <p key={index}>{item}</p>)}</div>}
      <div className="ds-body">
        <section className="ds-preview" aria-label="原稿与预期稿对比">
          <div className="ds-preview-toolbar"><div role="group" aria-label="预览方式"><button type="button" className="ghost-button" aria-pressed={previewMode === 'layout'} onClick={() => setPreviewMode('layout')}>排版预览</button><button type="button" className="ghost-button" aria-pressed={previewMode === 'source'} onClick={() => setPreviewMode('source')}>源码对照</button><button type="button" className="ghost-button" aria-pressed={previewMode === 'pdf'} disabled={blocked} onClick={() => { if (pdfReady) setPreviewMode('pdf'); else void renderPdfPair() }}>{working === 'pdf' ? '正在生成 PDF…' : '生成 PDF 对照'}</button></div><span>{changes.length ? changes.length + ' 项排版待应用' : '排版与当前稿一致'}</span></div>
          <details className="ds-preview-note"><summary>对照与导出说明</summary><p>{originalContent === null ? '尚无扫描原稿，以首次打开时正文作为原稿对照。' : '左侧保留扫描原稿；右侧显示预期稿。'}{suggestedContent !== null ? ' 预期稿包含待接受的格式建议；应用排版和导出均不会接受正文建议，正文建议仍须在修改列表逐项或全部接受。' : ' 预期稿使用当前正文。'} 原稿排版以本次文档首次读取的设置为准；屏幕预览连续排版，PDF 显示实际分页。</p></details>
          <div className="ds-comparison">
            {(['original', 'expected'] as const).map(scope => <section className="ds-preview-pane" key={scope} aria-label={scopeNames[scope] + '预览'}><header><strong>{scopeNames[scope]}</strong><span>{scope === 'original' ? '保留原有内容与排版' : '接受前可查看、可单独导出'}</span></header>{previewMode === 'source' ? <pre className="ds-source" aria-label={scopeNames[scope] + '源码'}>{scope === 'original' ? original : expectedContent}</pre> : previewMode === 'pdf' ? pdfReady ? <iframe title={scopeNames[scope] + ' PDF 实际分页'} src={scope === 'original' ? pdfPair.original : pdfPair.expected} className="ds-pdf"/> : <div className="ds-pdf-stale"><p>排版或正文已变化，请重新生成 PDF 对照。</p><button type="button" className="soft-button" onClick={() => { void renderPdfPair() }} disabled={blocked}>重新生成 PDF 对照</button></div> : <ReadOnlyPaper content={scope === 'original' ? original : expectedContent} style={scope === 'original' ? originalStyle : draftStyle} label={scopeNames[scope]}/>}</section>)}
          </div>
        </section>
        <aside className="ds-controls" aria-label="排版调整">
          <section className="ds-chat" aria-label="排版对话"><h3><MessageSquare size={16}/>告诉助手怎么排版</h3><p>填写要求后生成待应用排版，再决定是否接受。未配置模型时使用本地规则解析。</p><div className="ds-prompts"><button type="button" className="ghost-button" disabled={blocked} onClick={() => { void sendChat('正文宋体小四，1.5倍行距') }}>正文宋体小四，1.5倍行距</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => { void sendChat('标题黑体三号，段后12磅') }}>标题黑体三号，段后12磅</button></div><div className="ds-messages" aria-live="polite">{messages.map((message, index) => <div className={'ds-message ds-message-' + message.role} key={index}><strong>{message.role === 'user' ? '排版要求' : message.provider === 'local' ? '本地规则' : '已配置模型'}</strong><p>{message.content}</p>{message.warnings?.map((warning, number) => <small key={number}>{warning}</small>)}</div>)}{working === 'chat' && <p role="status">正在生成排版建议…</p>}</div><form onSubmit={event => { event.preventDefault(); void sendChat() }}><textarea aria-label="排版对话输入" placeholder="例如：正文宋体小四，行距1.5倍，段后6磅，左页边距30毫米" value={chatInput} maxLength={2000} disabled={blocked} onChange={event => setChatInput(event.target.value)} rows={3}/><button type="submit" className="primary-button" disabled={blocked || !chatInput.trim()}><Send size={14}/>生成待应用排版</button></form></section>
          <section className="ds-form" aria-label="直接设置排版"><div className="ds-section-title"><h3>直接调整排版</h3>{profileStyle && <button type="button" className="ghost-button" disabled={blocked} onClick={() => { try { setDraftStyle(validateDocumentStyle(profileStyle)); setNotice('已载入学校模板排版，请查看预期稿并确认应用。'); setError('') } catch (failure) { setError(errorMessage(failure)) } }}>预览学校模板排版</button>}</div><div className="ds-fields"><label>正文字体<FontPicker apiBase={apiBase} value={draftStyle.font_family} disabled={blocked} onChange={name => updateBody('font_family', name)} ariaLabel="正文字体"/></label><LineSpacingControl label="正文行距" value={draftStyle} disabled={blocked} onChange={patch => setDraftStyle(value => ({ ...value, ...patch }))}/>{bodyFields.map(({ field, label, min, max, step }) => <label key={field}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} value={draftStyle[field]} disabled={blocked} onChange={event => updateBody(field, event.target.value)}/></label>)}</div><details><summary>页边距（A4）</summary><div className="ds-fields">{marginFields.map(({ field, label }) => <label key={field}>{label}<input aria-label={label} type="number" min={10} max={60} step={0.5} value={draftStyle[field]} disabled={blocked} onChange={event => updateBody(field, event.target.value)}/></label>)}</div></details><details><summary>各级标题字体、字号与间距</summary>{draftStyle.headings.map((heading, index) => <fieldset key={index}><legend>{index + 1} 级标题</legend><div className="ds-fields"><LineSpacingControl label={(index+1)+'级标题行距'} value={heading} disabled={blocked} onChange={patch => setDraftStyle(value => { const next=cloneDocumentStyle(value); Object.assign(next.headings[index],patch); return next })}/><label>字体<FontPicker apiBase={apiBase} value={heading.font_family} disabled={blocked} onChange={name => updateHeading(index, 'font_family', name)} ariaLabel={(index + 1) + '级标题字体'}/></label>{([{ field: 'font_size_pt', label: '字号（磅）', min: 8, max: 48, step: 0.5 }, { field: 'space_before_pt', label: '段前（磅）', min: 0, max: 72, step: 1 }, { field: 'space_after_pt', label: '段后（磅）', min: 0, max: 72, step: 1 }] as const).map(({ field, label, min, max, step }) => <label key={field}>{label}<input aria-label={(index + 1) + '级标题' + label} type="number" min={min} max={max} step={step} value={heading[field]} disabled={blocked} onChange={event => updateHeading(index, field, event.target.value)}/></label>)}</div></fieldset>)}</details></section>
          <section className="ds-pending" aria-label="待应用排版"><div className="ds-section-title"><h3>待应用排版 {changes.length}</h3><button type="button" className="ghost-button" disabled={blocked || !undoStyles.length} onClick={() => { void applyStyle(undoStyles[undoStyles.length - 1], '已撤销上次排版', true) }}><RotateCcw size={13}/>撤销上次排版</button></div>{changes.length ? <><div className="ds-apply-all"><button type="button" className="primary-button" disabled={blocked} onClick={() => { void applyStyle(draftStyle, '已应用全部排版') }}><Check size={14}/>应用全部排版</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => { setDraftStyle(cloneDocumentStyle(currentStyle)); setNotice('已保留当前排版。') }}>保留当前排版</button></div><div className="ds-style-changes">{styleGroups.map(group => <details className="ds-style-group" key={group.title}><summary>{group.title} · {group.items.length} 项{group.title === '标题版式' ? '（含字体与磅值间距）' : ''}</summary><table className="ds-change-table"><thead><tr><th>属性</th><th>当前</th><th>预期</th><th>处理</th></tr></thead><tbody>{group.items.map(change => <tr key={change.field} data-style-field={change.field}><td>{change.label}</td><td>{change.before}</td><td>{change.after}</td><td><button type="button" className="ghost-button" disabled={blocked} onClick={() => { void applyStyle(copyStyleField(currentStyle, draftStyle, change.field), '已应用' + change.label) }}>应用</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => setDraftStyle(style => copyStyleField(style, currentStyle, change.field))}>保留</button></td></tr>)}</tbody></table><div className="ds-style-group-actions"><button type="button" className="primary-button" disabled={blocked} onClick={() => { void applyStyle(group.items.reduce((style, change) => copyStyleField(style, draftStyle, change.field), currentStyle), '已应用' + group.title) }}>应用此组</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => setDraftStyle(style => group.items.reduce((result, change) => copyStyleField(result, currentStyle, change.field), style))}>保留此组</button></div></details>)}</div></> : <p className="ds-muted">尚无排版变化。可通过对话、表单或学校模板生成预期效果。</p>}</section>
        </aside>
      </div>
    </div>
  </div>
}
