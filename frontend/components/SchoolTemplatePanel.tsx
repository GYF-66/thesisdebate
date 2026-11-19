'use client'

import { useEffect, useRef, useState } from 'react'
import { FileUp, Plus, X } from 'lucide-react'
import { auditTemplate, defaultHeadingNumbering, defaultSections, documentKindOptions, extractTemplateRules, headingNumberingExample, headingStyleOptions, importSchoolTemplate, listFormatProfiles, mergeTemplateRules, optimizeTemplate, resolveTemplateRules, saveSchoolTemplate, type DocumentKind, type FormatProfile, type HeadingStyle, type SchoolTemplateDraft, type TemplateAudit } from '../lib/schoolTemplates'
import { DEFAULT_DOCUMENT_STYLE, cloneDocumentStyle, type DocumentStyle } from '../lib/documentStudio'
import FontPicker from './FontPicker'

type Props = {
  apiBase: string
  open: boolean
  selectedProfileId: string
  onClose: () => void
  onSelect: (profileId: string, profile: FormatProfile) => void | Promise<void>
  disabled?: boolean
}

const emptyDraft = (): SchoolTemplateDraft => ({ name: '', institution: '', heading_numbering: defaultHeadingNumbering(), source_name: null, warnings: [], document_kind: 'thesis', review_profile_id: 'academic', required_sections: [], cover_fields: [], submission_checklist: [], rule_sources: [] })
const errorText = (error: unknown) => error instanceof Error && error.message !== 'Failed to fetch' ? error.message : '连接失败，模板设置尚未保存，请重试。'

export default function SchoolTemplatePanel({ apiBase, open, selectedProfileId, onClose, onSelect, disabled = false }: Props) {
  const [profiles, setProfiles] = useState<FormatProfile[]>([])
  const [choice, setChoice] = useState('')
  const [draft, setDraft] = useState<SchoolTemplateDraft>(emptyDraft)
  const [editing, setEditing] = useState(false)
  const [working, setWorking] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [loadNonce, setLoadNonce] = useState(0)
  const [mode, setMode] = useState<'apply' | 'optimize'>('apply')
  const [specification, setSpecification] = useState('')
  const [audit, setAudit] = useState<TemplateAudit | null>(null)
  const [selectedIssues, setSelectedIssues] = useState<string[]>([])
  const [sourceChoices, setSourceChoices] = useState<Record<string, string>>({})
  const fileRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const workingRef = useRef(false)
  const sessionRef = useRef(0)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { if (!workingRef.current) closeRef.current(); return }
      if (event.key !== 'Tab') return
      const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]') ?? []).filter(element => element.offsetParent !== null)
      if (!controls.length) { event.preventDefault(); return }
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [open])

  useEffect(() => {
    const session = ++sessionRef.current
    if (!open) return
    const controller = new AbortController()
    workingRef.current = false
    setWorking(''); setLoading(true); setError(''); setStatus(''); setEditing(false)
    setAudit(null); setSourceChoices({}); setSelectedIssues([]); setSpecification('')
    void listFormatProfiles(apiBase, controller.signal).then(items => {
      if (session !== sessionRef.current) return
      setProfiles(items)
      const selected = items.find(profile => profile.id === selectedProfileId) ?? items.find(profile => profile.id === 'generic-thesis') ?? items[0]
      setChoice(selected?.id ?? ''); setDraft(selected ? { ...selected, heading_numbering: [...selected.heading_numbering] } : emptyDraft())
    }).catch(failure => { if (!controller.signal.aborted && session === sessionRef.current) setError(errorText(failure)) }).finally(() => { if (session === sessionRef.current) setLoading(false) })
    return () => controller.abort()
  }, [apiBase, open, selectedProfileId, loadNonce])

  if (!open) return null
  const blocked = disabled || loading || !!working
  const selected = profiles.find(profile => profile.id === choice)

  function choose(id: string) {
    const profile = profiles.find(item => item.id === id)
    if (!profile) return
    setChoice(id); setDraft({ ...profile, heading_numbering: [...profile.heading_numbering] }); setEditing(false); setError(''); setStatus(''); setAudit(null); setSourceChoices({}); setSelectedIssues([])
  }

  function updateDraft(update: Partial<SchoolTemplateDraft>) { setDraft(value => ({ ...value, ...update })); setEditing(true); setError(''); setStatus(''); setAudit(null) }
  function validDraft(): SchoolTemplateDraft { return { ...draft, name: draft.name.trim() || '文档模板草案', institution: draft.institution.trim() || '自定义文档模板', required_sections: draft.required_sections?.map(item => item.trim()).filter(Boolean), cover_fields: draft.cover_fields?.map(item => item.trim()).filter(Boolean), submission_checklist: draft.submission_checklist?.map(item => item.trim()).filter(Boolean) } }
  async function runTool(label: string, work: () => Promise<void>) {
    if (blocked || workingRef.current) return
    workingRef.current = true; setWorking(label); setError(''); setStatus('')
    try { await work() } catch (failure) { setError(errorText(failure)) }
    finally { workingRef.current = false; setWorking('') }
  }
  function acceptDraft(value: SchoolTemplateDraft) { setDraft(value); setEditing(true); setAudit(null); setSourceChoices({}); setSelectedIssues([]) }
  async function extract() { await runTool('正在识别规范…', async () => { acceptDraft(await extractTemplateRules(apiBase, specification, validDraft())); setStatus('已保留规范出处。请选择并确认取值，冲突不会自动覆盖。') }) }
  async function inspect() { await runTool('正在检查模板…', async () => { setAudit(await auditTemplate(apiBase, validDraft())); setSelectedIssues([]); setMode('optimize') }) }
  async function optimize() { await runTool('正在生成优化副本…', async () => { acceptDraft(await optimizeTemplate(apiBase, validDraft(), selectedIssues, selected?.id)); setStatus('已生成优化草案。请核对后保存为独立模板；原模板不变。') }) }
  async function confirmSources(rejectIds: string[] = []) { await runTool('正在确认规则…', async () => { acceptDraft(await resolveTemplateRules(apiBase, validDraft(), rejectIds.length ? [] : Object.values(sourceChoices), rejectIds)); setStatus('规则决定已写入草案，请核对后保存。') }) }
  function updateStyle(key: keyof DocumentStyle, value: string | number) {
    if (!draft.document_style) return
    updateDraft({ document_style: { ...draft.document_style, [key]: value } })
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = ''
    if (!file || workingRef.current || disabled) return
    if (!/\.(docx|json|pdf|txt|md|markdown)$/i.test(file.name)) { setError('支持 DOCX 样例、JSON 规则、TXT/Markdown 或有文字层的 PDF 规范。'); return }
    if (file.size > 10 * 1024 * 1024) { setError('模板文件不能超过 10 MB。'); return }
    const session = sessionRef.current
    workingRef.current = true; setWorking('正在读取模板…'); setError(''); setStatus('')
    try {
      let imported = await importSchoolTemplate(apiBase, file, draft.document_kind ?? 'thesis')
      if (/\.(pdf|txt|md|markdown)$/i.test(file.name)) imported = await mergeTemplateRules(apiBase, validDraft(), imported)
      if (session !== sessionRef.current) return
      acceptDraft({ ...imported, heading_numbering: [...imported.heading_numbering], ...(!/\.json$/i.test(file.name) ? { document_kind: draft.document_kind ?? 'thesis', review_profile_id: draft.review_profile_id ?? 'academic' } : {}) })
      setStatus('材料已读取。请核对识别范围、来源与冲突，确认后保存。')
    } catch (failure) { if (session === sessionRef.current) setError(errorText(failure)) }
    finally { workingRef.current = false; if (session === sessionRef.current) setWorking('') }
  }

  async function apply() {
    if (blocked || workingRef.current) return
    if (editing && !draft.name.trim()) { setError('请填写模板名称。'); return }
    if (!editing && !selected) { setError('请先选择或上传一个模板。'); return }
    if (draft.rule_sources?.some(item => item.status === 'conflict' && !item.note.startsWith('未采用'))) { setError('请先选择并确认冲突字段的取值，再保存模板。'); return }
    const session = sessionRef.current
    workingRef.current = true; setWorking(editing ? '正在保存并选用…' : '正在选用模板…'); setError(''); setStatus('')
    try {
      let profile = selected!
      if (editing) {
        profile = await saveSchoolTemplate(apiBase, { ...validDraft(), parent_template_id: draft.parent_template_id ?? selected?.id ?? null })
        if (session !== sessionRef.current) return
        setProfiles(items => [...items, profile]); setChoice(profile.id); setDraft({ ...profile, heading_numbering: [...profile.heading_numbering] }); setEditing(false)
      }
      if (mode === 'apply') await onSelect(profile.id, profile)
      if (session === sessionRef.current) setStatus(mode === 'apply' ? '已选用“' + profile.name + '”。重新运行格式扫描后，按此模板生成修改建议。' : '已保存“' + profile.name + '”。原模板和文档保持不变，可切换到“按模板改文档”后选用。')
    } catch (failure) { if (session === sessionRef.current) setError(errorText(failure)) }
    finally { workingRef.current = false; if (session === sessionRef.current) setWorking('') }
  }

  return <div className="st-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !workingRef.current) onClose() }}>
    <div className="st-dialog" role="dialog" aria-modal="true" aria-labelledby="school-template-title" tabIndex={-1} ref={dialogRef}>
      <div className="st-header"><div><h2 id="school-template-title">文档模板</h2><p>论文、课程报告、实验报告和其他文档共用可确认的模板规则。</p></div><button type="button" className="ghost-button" onClick={onClose} disabled={!!working} aria-label="关闭文档模板"><X size={18}/></button></div>
      <div className="st-body">
        <div className="st-upload" role="group" aria-label="模板操作模式"><button type="button" className={mode === 'apply' ? 'primary-button' : 'soft-button'} aria-pressed={mode === 'apply'} onClick={() => setMode('apply')} disabled={blocked}>按模板改文档</button><button type="button" className={mode === 'optimize' ? 'primary-button' : 'soft-button'} aria-pressed={mode === 'optimize'} onClick={() => setMode('optimize')} disabled={blocked}>检查并优化模板</button></div>
        <p className="st-status">选用模板不会立即修改正文。请重新扫描，再审核并接受修改建议。</p>
        <label className="st-profile-select">已有模板<select aria-label="选择文档模板" value={choice} disabled={blocked || !profiles.length} onChange={event => choose(event.target.value)}>{!profiles.length && <option value="">{loading ? '正在读取模板…' : '暂无可用模板'}</option>}{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.institution} · {profile.name}{profile.id === selectedProfileId ? '（当前）' : ''}</option>)}</select></label>
        <div className="st-upload"><input hidden ref={fileRef} type="file" accept=".docx,.json,.pdf,.txt,.md,.markdown" onChange={upload}/><button type="button" className="soft-button" onClick={() => fileRef.current?.click()} disabled={blocked}><FileUp size={16}/>上传模板或规范</button><button type="button" className="ghost-button" disabled={blocked} onClick={() => { setDraft(emptyDraft()); setChoice(''); setEditing(true); setAudit(null); setSourceChoices({}); setError(''); setStatus('请先选择文档类型，填写名称并确认规则。') }}><Plus size={16}/>新建规则</button><small>DOCX / JSON 模板；TXT / Markdown / 有文字层的 PDF 规范，最大 10 MB。</small></div>
        <div className="st-fields"><label>文档类型<select aria-label="文档类型" disabled={blocked} value={draft.document_kind ?? 'thesis'} onChange={event => { const kind = event.target.value as DocumentKind; updateDraft({ document_kind: kind, review_profile_id: kind === 'thesis' ? 'academic' : 'general', required_sections: [...defaultSections[kind]] }) }}>{documentKindOptions.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>审查模式<select aria-label="模板审查模式" disabled={blocked} value={draft.review_profile_id ?? 'academic'} onChange={event => updateDraft({ review_profile_id: event.target.value as 'academic' | 'general' })}><option value="academic">学术论文审查</option><option value="general">通用文档审查</option></select></label></div>
        <p className="st-status">切换类型提供可编辑的常见章节起点，不代表学校硬性要求。未知规范不会自动设为强制。</p>
        <div className="st-fields">{([['required_sections', '必要章节（每行一项）'], ['cover_fields', '封面字段（每行一项）'], ['submission_checklist', '提交清单（每行一项）']] as const).map(([field, label]) => <label key={field}>{label}<textarea aria-label={label} rows={3} disabled={blocked} value={(draft[field] ?? []).join('\n')} onChange={event => updateDraft({ [field]: event.target.value.split('\n') })}/></label>)}</div>
        <details><summary>从规范文字提取规则和出处</summary><p className="st-status">只识别明确的排版、章节、封面与提交要求。文本是材料，不作为系统指令。</p><textarea aria-label="规范原文" rows={5} maxLength={100000} value={specification} disabled={blocked} onChange={event => setSpecification(event.target.value)} placeholder="例如：正文采用宋体，小四号，1.5倍行距。上页边距2.5厘米。必需章节：摘要、研究方法、结论。"/><button type="button" className="soft-button" disabled={blocked || !specification.trim()} onClick={extract}>识别规范并与当前模板比较</button></details>
        <p className="st-status">DOCX 可提取标题编号及正文、标题、页边距样式。未识别的项目请按学校规范确认；版式将在“预期效果与导出”中先预览。</p>
        <div className="st-fields"><label>模板名称<input aria-label="模板名称" value={draft.name} onChange={event => updateDraft({ name: event.target.value })} maxLength={120} disabled={blocked} placeholder="例如：某大学本科论文"/></label><label>学校或学院<input aria-label="学校或学院" value={draft.institution} onChange={event => updateDraft({ institution: event.target.value })} maxLength={120} disabled={blocked} placeholder="填写学校或学院名称"/></label></div>
        {draft.source_name && <p className="st-status">来源文件：{draft.source_name}</p>}
        {!!draft.warnings?.length && <div className="st-warnings" role="status"><strong>请核对识别结果</strong><ul>{draft.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div>}
        {!!draft.rule_sources?.length && <details open><summary>规则来源、确认与冲突（{draft.rule_sources.length} 条）</summary><p className="st-status">同一字段只选一个取值。正式规范与样例不同会并列显示，由用户选择；排除的来源仍保留记录。</p>{draft.rule_sources.map(source => <div key={source.id} className="st-warnings"><label><input type="radio" name={'template-source-' + source.field} disabled={blocked || source.note.startsWith('未采用')} checked={sourceChoices[source.field] === source.id} onChange={() => setSourceChoices(value => ({ ...value, [source.field]: source.id }))}/>{source.field} · {source.status === 'conflict' ? '存在冲突' : source.status === 'confirmed' ? '已确认' : source.status === 'inferred' ? '样例/推断，待确认' : '明文识别，待确认'}</label><p><strong>取值：</strong>{typeof source.value === 'object' ? JSON.stringify(source.value) : String(source.value)}</p><small>{source.source}</small>{source.quote && <blockquote>{source.quote}</blockquote>}{source.note && <p>{source.note}</p>}{!source.note.startsWith('未采用') && source.status !== 'confirmed' && <button type="button" className="ghost-button" disabled={blocked} onClick={() => confirmSources([source.id])}>不采用此规则</button>}</div>)}<button type="button" className="soft-button" disabled={blocked || !Object.keys(sourceChoices).length} onClick={() => confirmSources()}>确认所选取值</button></details>}
        {mode === 'optimize' && <section aria-label="模板体检与优化"><div className="st-upload"><button type="button" className="soft-button" disabled={blocked} onClick={inspect}>检查模板完整性与可读性</button><small>仅提供草案，不更改原模板或当前文档。正式来源规则不会被优化器擅改。</small></div>{audit && <><p role="status">{audit.summary}</p>{audit.issues.map(issue => <div key={issue.id} className="st-warnings"><label><input type="checkbox" disabled={blocked || !issue.can_apply} checked={selectedIssues.includes(issue.id)} onChange={event => setSelectedIssues(ids => event.target.checked ? [...ids, issue.id] : ids.filter(id => id !== issue.id))}/>{issue.title}{!issue.can_apply && '（需人工核对）'}</label><p>{issue.explanation}</p>{issue.can_apply && <small>建议：{Array.isArray(issue.proposed_value) ? issue.proposed_value.join('、') : String(issue.proposed_value)}</small>}</div>)}<button type="button" className="soft-button" disabled={blocked || !selectedIssues.length} onClick={optimize}>仅采用勾选建议，生成优化副本</button></>}</section>}
        <div className="st-level-grid">{draft.heading_numbering.map((style, index) => <label key={index}><span>{index + 1} 级标题</span><select aria-label={(index + 1) + '级标题编号'} disabled={blocked} value={style} onChange={event => { const levels = [...draft.heading_numbering]; levels[index] = event.target.value as HeadingStyle; updateDraft({ heading_numbering: levels }) }}>{headingStyleOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select><small className="st-rule-preview">{headingNumberingExample(style, index + 1)}</small></label>)}</div>
        <p className="st-status">预览展示编号形式；实际编号按正文的标题层级计数。编辑已有规则时，会保存为独立的新模板版本。</p>
        <label className="st-layout-toggle"><input type="checkbox" checked={!!draft.document_style} disabled={blocked} onChange={event => updateDraft({ document_style: event.target.checked ? cloneDocumentStyle(DEFAULT_DOCUMENT_STYLE) : null })}/>包含文档排版参数</label>
        {draft.document_style && <fieldset className="st-layout-settings" disabled={blocked}><legend>文档版式（可核对、修改）</legend><div className="st-layout-grid">
          <label>正文字体<FontPicker apiBase={apiBase} ariaLabel="学校正文字体" value={draft.document_style.font_family} disabled={blocked} onChange={font => updateStyle('font_family', font)}/></label>
          {([['font_size_pt','正文字号（磅）',8,36],['line_spacing','正文行距（倍）',1,3],['paragraph_before_pt','段前（磅）',0,72],['paragraph_after_pt','段后（磅）',0,72],['first_line_indent_pt','首行缩进（磅）',0,72],['margin_top_mm','上页边距（毫米）',10,60],['margin_bottom_mm','下页边距（毫米）',10,60],['margin_left_mm','左页边距（毫米）',10,60],['margin_right_mm','右页边距（毫米）',10,60]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input aria-label={'学校'+label} type="number" min={min} max={max} step="0.1" value={draft.document_style![key]} onChange={event => updateStyle(key, Number(event.target.value))}/></label>)}
        </div><div className="st-heading-appearance">{draft.document_style.headings.map((heading,index) => <div key={index}><strong>{index+1}级标题版式</strong><label>字体<FontPicker apiBase={apiBase} ariaLabel={(index+1)+'级标题字体'} value={heading.font_family} disabled={blocked} onChange={font => { const style=cloneDocumentStyle(draft.document_style!); style.headings[index].font_family=font; updateDraft({document_style:style}) }}/></label>{([['font_size_pt','字号',8,48],['line_spacing','行距',1,3],['space_before_pt','段前',0,72],['space_after_pt','段后',0,72]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input aria-label={(index+1)+'级标题'+label} type="number" min={min} max={max} step="0.1" value={heading[key]} onChange={event => { const style=cloneDocumentStyle(draft.document_style!); style.headings[index][key]=Number(event.target.value); updateDraft({document_style:style}) }}/></label>)}</div>)}</div></fieldset>}
        {error && <p className="st-error" role="alert">{error}</p>}
        {status && <p className="st-status" role="status">{status}</p>}
        {loading && <p className="st-status" role="status">正在读取模板列表…</p>}
        {error && !profiles.length && <button type="button" className="ghost-button" disabled={loading} onClick={() => setLoadNonce(value => value + 1)}>重新读取模板</button>}
      </div>
      <div className="st-footer"><button type="button" className="ghost-button" onClick={onClose} disabled={!!working}>关闭</button><button type="button" className="primary-button" onClick={apply} disabled={blocked || (!editing && !selected) || (mode === 'optimize' && !editing)}>{working || (mode === 'optimize' ? '确认保存独立模板' : editing ? '确认保存并选用' : '应用所选模板')}</button></div>
    </div>
  </div>
}
